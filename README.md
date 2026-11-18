# 双向转发机器人

Telegram 私聊机器人：用户发来的消息转给所有管理员，管理员**直接回复**那条转发消息就能回给用户。
Python 长轮询 + SQLite，单进程即可运行，不依赖 webhook 和公网地址。

## 功能

- 用户侧：任意类型消息（文字、图片、文件、语音、贴纸……）原样复制给管理员，不丢格式
- 管理员侧：回复转发消息即可回给用户；支持文字、图片、文件等任意类型回执
- 新用户提醒：第一次 /start 时通知所有管理员
- 基础管理：`/stats` 统计、`/block` `/unblock` 拉黑解封、`/broadcast` 群发
- 限流：同一用户默认 2 秒内只转发一次，`RATE_LIMIT_SECONDS=0` 可关闭
- 健壮性：用户屏蔽机器人会自动标记拉黑；数据库路径不可写时自动退回临时目录/内存库

## 目录

| 文件 | 作用 |
| --- | --- |
| `bot.py` | 入口，装配 Application、注册处理器、启动长轮询 |
| `handlers.py` | 用户/管理员的消息处理与转发逻辑 |
| `db.py` | SQLite：用户、转发映射、运行时管理员、限流时间戳 |
| `config.py` | 环境变量加载与默认文案 |
| `tests/test_db.py` | 数据库层回归测试 |

## 快速开始

1. 找 [@BotFather](https://t.me/BotFather) 创建机器人，拿到 `BOT_TOKEN`
2. 复制配置并填写：

   ```bash
   cp .env.example .env
   # 填 BOT_TOKEN，ADMIN_IDS 先随便填个自己的 ID
   ```

3. 装依赖并运行：

   ```bash
   pip install -r requirements.txt
   python bot.py
   ```

4. 自己给机器人发 `/id`，把拿到的数字 ID 填进 `ADMIN_IDS`，重启即可。

## 管理员命令

| 命令 | 说明 |
| --- | --- |
| `/stats` | 用户数、活跃数、拉黑数、转发映射数 |
| `/block 用户ID` | 拉黑，之后该用户的消息不再转发 |
| `/unblock 用户ID` | 解封 |
| `/broadcast` | **回复**要群发的那条消息后发送，复制给所有活跃用户 |

普通用户命令：`/start`、`/help`、`/id`。

## 部署

`Dockerfile` 已就绪：

```bash
docker build -t shuangxiang-bot .
docker run -d --restart unless-stopped --env-file .env -v $(pwd)/data:/data shuangxiang-bot
```

- 云平台（Railway / FadeHost / 自建 VPS 等）只要挂持久盘到 `/data`，数据库会自动落到 `/data/bot.db`
- 不要把 `.env` 提交到仓库（已在 `.gitignore` 中排除）

## 测试

```bash
python -m pytest tests/ -q     # 或 python tests/test_db.py
```

## 备注

- 只处理私聊消息，群里的消息会忽略，避免误转发
- 转发映射默认保留最近 5 万条，够用又不至于让表无限增长
- 机器人需要能主动给用户发消息：如果用户从未和机器人对话，无法回执（Telegram 限制）
