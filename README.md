# 微信 AI 助手

基于 NoneBot2 + MiniMax/GPT 的微信个人号 AI 回复机器人，OneBot V12 协议。

## 插件系统

| 插件 | 命令 | 说明 |
|------|------|------|
| **ai_plugin** | @机器人 对话 | AI 对话、文生图、图生图、视频、搜索、抖音解析 |
| **nonebot_plugin_tarot** | `/占卜` `/塔罗牌` | 塔罗牌占卜，4张牌阵 5s 间隔发送 |
| **nonebot_plugin_mystool** | `/登录` `/签到` `/任务` | 米游社工具 |
| **nonebot_plugin_setu_collection** | `/色图` `/来N张xx涩图` | 色图获取，走代理 127.0.0.1:7890 |
| **nonebot_plugin_repeater** | 自动触发 | 复读机：上两条消息相同时自动复读，冷却 2 分钟 |

## AI 功能

| 功能 | 触发 | 默认模型 |
|------|------|---------|
| 文生图 | "生成/画一张xxx" | GPT |
| 图生图 | 引用图片 + "P图/改图" 或直接描述 | GPT |
| 视频生成 | "生成视频xxx" | MiniMax |
| 联网搜索 | 自动检测 | MiniMax MCP |
| 抖音解析 | 发送抖音/TikTok/B站链接 | — |

## 群聊行为

- **必须 @机器人** 才回复（包含引用消息）
- 引用图片 + @机器人 + 生图意图 → 自动走图生图
- 命令以 `/` 开头，不 @也能触发
- 微信 @后自动插入 Unicode 空白（正则处理）

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        微信客户端 (WeChat.exe)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              │        NoveLoader.dll          │
              │       (进程注入/HOOK)           │
              └───────────────┬───────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Py/main.py (Python SDK)                      │
│  WeChatService ──────── DLL 封装、微信多开、消息收发              │
│  NoneBotWsClient ────── WebSocket 客户端 → 连接 NoneBot2         │
└──────────────────────────────┬──────────────────────────────────┘
                               │ ws://127.0.0.1:18765/onebot/v12/ws
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   ai_plugin/bot.py (NoneBot2)                     │
│  router.py   ── 消息路由、生图/图生图/视频/AI回复                │
│  image_generator.py ── 文生图 (MiniMax + GPT)                   │
│  image_editor.py    ── 图生图 (MiniMax + GPT)                   │
│  douyin/       ── 抖音/TikTok/B站 解析                          │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                 lib/hok_brain (AI 大脑核心库)                     │
│  schemas/  ── EventEnvelope, ReplyAction                        │
│  brain/    ── ReplyContext, RuleEngine                          │
│  ai/       ── MiniMaxClient (LLM 调用)                           │
│  context/  ── ContextBuilder (系统提示构建)                       │
│  memory/   ── EmbeddingStore, ProfileExtractor                  │
└─────────────────────────────────────────────────────────────────┘
```

## 目录结构

```
E:\Project\
├── Py/                          # 微信注入层
│   ├── main.py                  # 主入口
│   ├── NoveLoader.dll / NoveHelper.dll
│   └── data/quoted_images/      # 引用图片 CDN 缓存
│
├── ai_plugin/                   # NoneBot2 AI 插件
│   ├── bot.py                   # 启动入口 (FastAPI, :18765)
│   ├── pyproject.toml           # 插件注册
│   ├── .env.prod                # API Key 等
│   ├── ai_plugin/
│   │   ├── __init__.py          # 消息匹配 + 命令排除
│   │   ├── router.py            # 消息路由 (生图/图生图/AI/抖音)
│   │   ├── handler.py           # AIHandler
│   │   ├── image_generator.py   # 文生图 (默认GPT)
│   │   ├── image_editor.py      # 图生图 (默认GPT)
│   │   ├── douyin/              # 抖音/TikTok/B站 解析
│   │   └── config.py            # 配置
│   └── data/
│
├── nonebot-plugin-tarot/        # 塔罗牌插件 (V12适配)
├── nonebot-plugin-setu/         # 色图插件 (V12适配)
├── nonebot-plugin-mystool/      # 米游社插件
├── lib/hok_brain/               # AI 核心库
└── README.md
```

## 启动

```bash
# 终端 1：AI 服务 (先启动)
cd E:\Project\ai_plugin
C:\Users\Administrator\AppData\Local\Programs\Python\Python311-32\python.exe bot.py

# 终端 2：微信注入
cd E:\Project\Py
C:\Users\Administrator\AppData\Local\Programs\Python\Python311-32\python.exe main.py
```

## 配置 (.env.prod)

```env
ENV_NAME=prod

# MiniMax (Chat + Video + Image)
MINIMAX_API_KEY=sk-xxx
MINIMAX_API_BASE_URL=https://api.minimaxi.com/v1

# GPT-Image (文生图 + 图生图)
GPT_IMAGE_API_KEY=sk-xxx
GPT_IMAGE_BASE_URL=https://freeapi.dgbmc.top
```

## 管理员命令

私聊发送（admin_wxid 用户）：
- `#切换图片模型 gpt` → 切换到 GPT
- `#切换图片模型 minimax` → 切换到 MiniMax
- `#切换图片模型` → 查看当前

## 常见问题

| 问题 | 原因/解决 |
|------|----------|
| 图片生成失败 400 | GPT content_policy_violation，修改提示词 |
| 图生图提示"未下载" | 需长按图片→引用→再发提示词 |
| 命令不生效 | 必须 `/` 开头 |
| AI 不回复 | 群聊需要 @机器人 |
| DLL 注入失败 | Python 必须 32位 3.11 |
