"""入口：装配 Application、注册处理器、启动长轮询。

运行：python bot.py
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import db
import handlers
from config import get_settings

log = logging.getLogger(__name__)


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """兜底异常处理：断线/网络错误交给 PTB 自己重试，其它记日志。"""
    exc = context.error
    log.warning("处理更新时出错：%s", exc, exc_info=exc)


def build_application() -> Application:
    settings = get_settings()
    db.get_db()  # 启动即建库，尽早暴露路径/权限问题

    admins = handlers.admin_ids()
    if not admins:  # pragma: no cover - config 已强制校验
        raise RuntimeError("没有可用的管理员 ID。")

    app = Application.builder().token(settings.bot_token).build()

    # 用户命令
    app.add_handler(CommandHandler(["start"], handlers.cmd_start))
    app.add_handler(CommandHandler(["help"], handlers.cmd_help))
    app.add_handler(CommandHandler(["id"], handlers.cmd_id))

    # 管理员命令
    app.add_handler(CommandHandler(["stats"], handlers.cmd_stats))
    app.add_handler(CommandHandler(["block"], handlers.cmd_block))
    app.add_handler(CommandHandler(["unblock"], handlers.cmd_unblock))
    app.add_handler(CommandHandler(["broadcast"], handlers.cmd_broadcast))

    # 私聊消息：管理员回复优先匹配，其余才当作普通用户消息
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND & filters.User(admins),
            handlers.admin_reply,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.ChatType.PRIVATE & ~filters.COMMAND & ~filters.User(admins),
            handlers.relay_user_message,
        )
    )

    app.add_error_handler(on_error)
    handlers.bind_bot(app.bot)
    return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    app = build_application()
    log.info("机器人已启动，等待消息…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
