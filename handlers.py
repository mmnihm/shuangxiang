"""消息处理：用户 ↔ 管理员的双向转发，以及管理员侧的轻量命令。

约定：
- 用户只在私聊里和机器人对话，任何类型消息都会被原样复制给所有管理员；
- 管理员直接回复那条转发消息即可回给用户（靠 reply_to_message + 映射表定位）；
- 管理员自己的消息不会再被当作"用户消息"转发出去。
"""

from __future__ import annotations

import html
import logging

from telegram import Message, Update
from telegram.constants import ParseMode
from telegram.error import Forbidden, TelegramError
from telegram.ext import ContextTypes

import db
from config import get_settings

log = logging.getLogger(__name__)

HEADER_TEMPLATE = "📩 来自 <b>{name}</b>{username}\nID：<code>{user_id}</code>\n\n直接回复本条或下面的消息即可回给用户。"


def _store():
    return db.get_db()


def admin_ids() -> list[int]:
    """配置里的管理员优先，再并上数据库里后天添加的。"""
    return list(dict.fromkeys(get_settings().admin_ids + _store().list_admins()))


def is_admin(user_id: int) -> bool:
    return user_id in admin_ids()


def _display_name(message: Message) -> tuple[str, str]:
    """返回 (显示名, @用户名 片段)，统一做 HTML 转义。"""
    user = message.from_user
    if user is None:  # pragma: no cover - 理论上不会发生
        return "未知用户", ""
    name = html.escape(user.full_name or str(user.id))
    tag = f"\n@{html.escape(user.username)}" if user.username else ""
    return name, tag


async def _notify_admins(text: str) -> None:
    for admin_id in admin_ids():
        try:
            await _store_bot().send_message(
                chat_id=admin_id, text=text, parse_mode=ParseMode.HTML
            )
        except TelegramError:
            continue


# 运行期保存一份 bot 引用，供 _notify_admins 这类无 update 的场景使用。
_bot_ref = None


def bind_bot(bot) -> None:
    global _bot_ref
    _bot_ref = bot


def _store_bot():
    if _bot_ref is None:  # pragma: no cover - 启动时一定会绑定
        raise RuntimeError("bot 尚未绑定")
    return _bot_ref


# ---------- 用户侧 ----------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    if message.chat.type != "private":
        return

    store = _store()
    first_time = store.upsert_user(user.id, user.username, user.full_name)
    text = get_settings().welcome_text.format(name=html.escape(user.first_name or "朋友"))
    await message.reply_text(text, parse_mode=ParseMode.HTML)

    if first_time and not is_admin(user.id):
        name, tag = _display_name(message)
        await _notify_admins(f"🆕 新用户：<b>{name}</b>{tag}\nID：<code>{user.id}</code>")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(get_settings().help_text, parse_mode=ParseMode.HTML)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None:
        return
    await message.reply_text(f"你的数字 ID：<code>{user.id}</code>", parse_mode=ParseMode.HTML)


async def relay_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """用户发来的任何消息：复制给每个管理员，并记录映射。"""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or message.chat.type != "private":
        return
    if is_admin(user.id) or user.is_bot:
        return

    store = _store()
    if store.is_blocked(user.id):
        log.info("已拉黑用户 %s 的消息被忽略", user.id)
        return

    rate_limit = get_settings().rate_limit_seconds
    if rate_limit > 0 and store.seconds_since_last_message(user.id) < rate_limit:
        return  # 静默丢弃，避免刷屏也避免和用户对吵

    store.upsert_user(user.id, user.username, user.full_name)
    name, tag = _display_name(message)
    header = HEADER_TEMPLATE.format(name=name, username=tag, user_id=user.id)

    delivered = 0
    for admin_id in admin_ids():
        try:
            head_msg = await context.bot.send_message(
                chat_id=admin_id, text=header, parse_mode=ParseMode.HTML
            )
        except TelegramError as exc:
            log.warning("给管理员 %s 发送标题失败：%s", admin_id, exc)
            continue
        store.save_link(admin_id, head_msg.message_id, user.id)

        try:
            copied = await context.bot.copy_message(
                chat_id=admin_id,
                from_chat_id=message.chat_id,
                message_id=message.message_id,
            )
        except TelegramError as exc:
            log.warning("给管理员 %s 复制消息失败：%s", admin_id, exc)
            continue
        store.save_link(admin_id, copied.message_id, user.id)
        delivered += 1

    if delivered == 0:
        await message.reply_text("消息暂时没有送达管理员，请稍后再试。")
        return

    store.prune_links()


# ---------- 管理员侧 ----------


async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """管理员回复某条转发消息 → 原样回给对应用户。"""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not is_admin(user.id):
        return
    if message.chat.type != "private":
        return  # 群里不处理，避免误判

    replied = message.reply_to_message
    if replied is None:
        await message.reply_text("回复某条用户消息即可回给用户；发送 /help 查看管理员命令。")
        return

    target = _store().get_link_user(message.chat_id, replied.message_id)
    if target is None:
        await message.reply_text("这条消息没有对应的用户记录（可能太旧已被清理）。")
        return

    try:
        await context.bot.copy_message(
            chat_id=target,
            from_chat_id=message.chat_id,
            message_id=message.message_id,
        )
    except Forbidden:
        _store().mark_blocked(target, True)
        await message.reply_text(f"用户 {target} 已屏蔽机器人，已自动标记为拉黑。")
    except TelegramError as exc:
        await message.reply_text(f"发送失败：{exc}")


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not is_admin(user.id):
        return
    stats = _store().statistics()
    await message.reply_text(
        "📊 <b>统计</b>\n\n"
        f"总用户：{stats['total']}\n"
        f"活跃：{stats['active']}\n"
        f"拉黑：{stats['blocked']}\n"
        f"转发映射：{stats['links']}",
        parse_mode=ParseMode.HTML,
    )


async def cmd_block(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _toggle_block(update, context, blocked=True)


async def cmd_unblock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _toggle_block(update, context, blocked=False)


async def _toggle_block(update: Update, context: ContextTypes.DEFAULT_TYPE, blocked: bool) -> None:
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not is_admin(user.id):
        return
    if not context.args:
        await message.reply_text("用法：/block 用户ID 或 /unblock 用户ID")
        return
    try:
        target = int(context.args[0])
    except ValueError:
        await message.reply_text("用户 ID 必须是数字。")
        return
    ok = _store().mark_blocked(target, blocked)
    action = "拉黑" if blocked else "解封"
    if ok:
        await message.reply_text(f"已{action}用户 {target}。")
        try:
            await context.bot.send_message(
                chat_id=target,
                text="你已被暂时限制发送消息。" if blocked else "已恢复，可以继续发送消息了。",
            )
        except TelegramError:
            pass
    else:
        await message.reply_text(f"没有找到用户 {target}，可能还没和机器人对话过。")


async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """回复一条消息并发送 /broadcast，把它群发给所有活跃用户。"""
    message = update.effective_message
    user = update.effective_user
    if message is None or user is None or not is_admin(user.id):
        return

    source = message.reply_to_message
    if source is None:
        await message.reply_text("用法：回复要群发的那条消息，然后发送 /broadcast")
        return

    targets = _store().active_user_ids()
    if not targets:
        await message.reply_text("还没有活跃用户。")
        return

    sent, failed = 0, 0
    for target in targets:
        try:
            await context.bot.copy_message(
                chat_id=target,
                from_chat_id=source.chat_id,
                message_id=source.message_id,
            )
            sent += 1
        except Forbidden:
            _store().mark_blocked(target, True)
            failed += 1
        except TelegramError:
            failed += 1
    await message.reply_text(f"群发完成：成功 {sent}，失败 {failed}。")
