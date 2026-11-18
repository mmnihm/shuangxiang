"""配置加载：BOT_TOKEN 与 ADMIN_IDS 必须来自环境变量，其余给默认值即可跑。

设计原则：没有配置项会让机器人启动时报错退出，只有真正缺令牌/管理员才报错。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

try:  # 本地开发读 .env，线上直接用系统环境变量
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

DEFAULT_WELCOME = (
    "👋 你好，{name}！\n\n"
    "这里是双向消息入口：你发的消息会转给管理员，管理员回复后会回到你这里。\n"
    "请勿发送账号密码、身份证等敏感信息。\n\n"
    "输入 /help 查看可用命令。"
)

DEFAULT_HELP = (
    "📖 <b>使用说明</b>\n\n"
    "/start 开始使用\n"
    "/id 查看自己的数字 ID\n"
    "/help 查看本说明\n\n"
    "直接发消息即可转给管理员，支持文字、图片、文件、语音等任意类型。"
)


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: list[int] = field(default_factory=list)
    db_path: str = "data/bot.db"
    rate_limit_seconds: float = 2.0
    welcome_text: str = DEFAULT_WELCOME
    help_text: str = DEFAULT_HELP


def _csv(value: str) -> list[str]:
    """逗号/换行/分号都能当分隔符，方便从各种后台粘贴。"""
    return [
        item.strip()
        for item in value.replace("\n", ",").replace(";", ",").split(",")
        if item.strip()
    ]


def _default_db_path() -> str:
    """自动挑一个能长期保留数据的目录，避免云平台重新部署丢数据。"""
    explicit = os.getenv("DB_PATH", "").strip()
    if explicit:
        return explicit
    for candidate in ("/data", "/app/data"):
        try:
            if os.path.isdir(candidate) and os.access(candidate, os.W_OK):
                return os.path.join(candidate, "bot.db")
        except OSError:  # pragma: no cover
            pass
    return "data/bot.db"


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("缺少 BOT_TOKEN 环境变量，请参考 .env.example 填写。")

    raw_admins = _csv(os.getenv("ADMIN_IDS", ""))
    if not raw_admins:
        raise RuntimeError("缺少 ADMIN_IDS 环境变量，至少填写一个管理员数字 ID（可用 /id 获取）。")

    try:
        admin_ids = [int(item) for item in raw_admins]
    except ValueError as exc:
        raise RuntimeError(f"ADMIN_IDS 只能填数字 ID，当前值：{raw_admins}") from exc

    db_path = _default_db_path()
    try:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    except OSError:  # pragma: no cover
        pass

    return Settings(
        bot_token=token,
        admin_ids=admin_ids,
        db_path=db_path,
        rate_limit_seconds=max(float(os.getenv("RATE_LIMIT_SECONDS", "2") or 0), 0.0),
        welcome_text=os.getenv("WELCOME_TEXT", DEFAULT_WELCOME),
        help_text=os.getenv("HELP_TEXT", DEFAULT_HELP),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """全局单例，避免各处重复解析环境变量。"""
    return load_settings()
