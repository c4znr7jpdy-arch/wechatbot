# 微信 AI 助手

基于 AstrBot + DeepSeek/mimo/Grok 的微信个人号 AI 回复机器人，OneBot V11 协议。

`Python 3.13`(AstrBot) · `Python 3.11 32-bit`(注入层) · `AstrBot` · `OneBot V11` · `SQLite` · `DeepSeek` · `mimo` · `Grok` · `GPT-Image`

## 功能总览

### AI 对话

| 功能 | 触发方式 | 说明 |
|------|---------|------|
| AI 聊天 | @机器人 或私聊 | AstrBot 原生 provider 流式回复 |
| 联网搜索 | 自动检测（"搜索/查一下/什么是"等） | AstrBot 内置 web_search（bocha）+ AI 总结 |
| 图片理解 | 引用图片 + @机器人 | mimo VLM 看图说话 |
| 随机插话 | 群聊 1% 概率主动参与 | `astrbot_plugin_proactive_chat` |
| 转发记录解析 | 转发聊天记录给机器人 | 自动提取并理解转发内容 |
| 自学习/记忆 | 后台自动 | `self_iterative_core` + `livingmemory` / `mnemosyne` |

### 媒体生成

| 功能 | 触发方式 | 后端 |
|------|---------|------|
| 文生图 | "生成/画一张xxx" | GPT / MiniMax（可切换） |
| 图生图 | 引用图片 + "P图/改图" | GPT / MiniMax |
| 视频生成 | "生成视频xxx" | MiniMax Hailuo |
| 语音合成 | `#语音 <文本>` | Edge TTS（6 种音色） |

### 生活工具

| 命令 | 说明 |
|------|------|
| `/新闻` `/热榜` | 抖音热榜 Top20 |
| `/xx天气` | 中国气象局 3 天预报，如 `/绵阳天气` |
| `/油价xx` | 各省油价，如 `/油价四川` |
| `/epic` `/喜加一` | Epic Games 每周免费游戏 |
| `/kfc` `/疯狂星期四` | KFC 文案生成 |
| `/燕云` `/燕云动态` | 燕云十六声 B站官方动态 |

### 解析下载

| 功能 | 触发方式 | 支持平台 |
|------|---------|---------|
| 视频解析 | 发送分享链接 | 抖音、TikTok、B站 |
| B站用户动态 | 发送空间链接 | B站（自动 AI 摘要） |
| B站动态订阅 | `/订阅动态` | 定时推送新动态 |
| 磁力搜索 | `/bt <关键词>` | TPB API，支持排序 |

### 娱乐互动

| 插件 | 命令 | 说明 |
|------|------|------|
| 塔罗牌 | `/占卜` `/塔罗牌` | 4 张牌阵，5s 间隔发送 |
| 色图 | `/色图` `/来N张xx涩图` | 走代理 127.0.0.1:7890 |
| 复读机 | 自动触发 | 上两条消息相同时自动复读，冷却 2 分钟 |
| 米游社 | `/登录` `/签到` `/任务` | 米游社工具（扫码登录） |
| 洛克王国 | — | 洛克王国相关功能 |

## 管理员命令

管理员 wxid 由 `ADMIN_WXID` 环境变量配置（逗号分隔支持多个）。

| 命令 | 说明 |
|------|------|
| `#插件列表` | 查看所有功能模块状态 |
| `#启用 <key>` | 启用指定功能模块 |
| `#禁用 <key>` | 禁用指定功能模块 |
| `#切换图片模型 gpt/minimax` | 切换文生图 + 图生图后端 |
| `#切换图片模型` | 查看当前模型 |
| `#语音 <文本>` | 生成语音消息 |
| `#切换音色 <名称>` | 切换 TTS 音色（小艺/晓晓/云扬/云希/晓萱/晓墨） |
| `#测试语音 <路径>` | 测试发送 silk 语音文件 |
| `#测试原始语音xml` | 测试原始语音 XML 发送 |
| `#转发文件助手 [数量]` | 转发文件传输助手消息到当前对话 |
| `#爬评论 <URL或ID> [数量]` | 爬取抖音评论注入语料库（RAG 风格注入） |
| `/定时` | 定时任务管理（增删查） |
| `/订阅动态 <UID>` | 订阅 B站用户动态推送 |
| `/取消订阅动态 <UID>` | 取消订阅 |
| `/订阅列表` | 查看当前订阅 |

## 群聊行为

- **必须 @机器人** 才触发 AI 回复（命令 `/` 开头不需要 @）
- 引用图片 + @机器人 + "画/P/生成" → 自动走图生图
- 引用图片 + @机器人 + 普通文字 → 图片理解（VLM）
- @机器人但没说话 → 拉上下文语境回复
- 空 @ → 语境感知接话
- 微信 @后自动插入 Unicode 空白（正则兼容）
- 随机插话：1% 概率主动参与群聊讨论（10 分钟时间窗口、至少 3 条上下文）
- 自我消息和系统通知自动过滤
- 转发聊天记录自动解析内容

## AI 架构

### Provider 配置（AstrBot 原生）

AI 对话由 AstrBot 的 provider pipeline 接管，配置在 `data/cmd_config.json`：

```
默认: deepseek/deepseek-v4-pro
    ├─ openai_2/mimo-v2.5         (第一后备)
    ├─ openai/grok-4.20-0309-non-reasoning  (第二后备)
    └─ openai_3/grok-4.3-low       (第三后备)
```

- **图片理解（VLM）**: `openai_2/mimo-v2.5`
- **联网搜索**: AstrBot 内置 `web_search`（bocha 引擎）
- **唤醒前缀**: `/` 或 `\`（群聊 `@bot` 也触发；私聊无需前缀）

> 旧的 Hermes 流式 + MiniMax→Grok→DeepSeek 三级 fallback 已废弃，相关代码（`ai_plugin/router.py`、`handler.py`、`hermes_client.py`、`mcp_tools.py`）保留为死代码。

### 记忆与自学习

- `astrbot_plugin_self_iterative_core` — 自迭代核心
- `astrbot_plugin_self_learning` — 自学习
- `astrbot_plugin_livingmemory` / `astrbot_plugin_mnemosyne` — 长期记忆
- `astrbot_plugin_proactive_chat` — 群聊主动插话

### 工具调用

媒体生成与生活工具通过 `jiang_*` 插件以斜杠命令触发（`/新闻`、`/绵阳天气`、`#语音`、`#切换图片模型` 等），不再使用 `CALL:tool:params` 文本协议。

### 插件热管理

AstrBot 插件通过 WebUI（http://localhost:6185）启用/禁用。`ai_plugin/plugin_manager.py` 的旧 `#启用/#禁用` 机制仅对残留 NoneBot 模块生效，已不再是主入口。

## 系统架构

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
│                  Py/main.py  (Python 3.11 32-bit)                │
│  WeChatService ──────── DLL 封装、微信多开、消息收发              │
│  NoneBotWsClient ────── WebSocket 客户端 → 连接 AstrBot          │
└──────────────────────────────┬──────────────────────────────────┘
                               │ ws://127.0.0.1:6199/ws  (OneBot V11)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                  AstrBot  (Python 3.13, WebUI :6185)             │
│  aiocqhttp adapter ── 反向 WS 服务端 :6199                       │
│  provider 系统 ── DeepSeek(默认) / mimo / Grok fallback          │
│  web_search ── bocha 联网搜索                                    │
│  image_caption ── mimo VLM 图片理解                              │
│                                                                  │
│  data/plugins/ (Star 插件)                                       │
│    ├─ astrbot_plugin_self_iterative_core  自迭代核心              │
│    ├─ astrbot_plugin_self_learning        自学习                 │
│    ├─ astrbot_plugin_livingmemory         长期记忆               │
│    ├─ astrbot_plugin_mnemosyne            记忆系统               │
│    ├─ astrbot_plugin_proactive_chat       主动插话               │
│    ├─ astrbot_plugin_kimi_web_search      Kimi 搜索              │
│    ├─ astrbot_plugin_qq_group_daily_analysis                    │
│    ├─ astrbot_plugin_zhenxunribao                                 │
│    ├─ astrbot_plugin_bittorrent          磁力搜索                │
│    ├─ meme_manager                        表情包                 │
│    └─ jiang_*                            业务插件（复用 ai_plugin 模块）
└──────────────────────────────┬──────────────────────────────────┘
                               │ sys.path 复用
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│            ai_plugin/  (模块库，非独立进程)                       │
│  image_generator / image_editor  ── 文生图 / 图生图 (GPT+MiniMax) │
│  video_generator                  ── 文生视频 (MiniMax Hailuo)   │
│  tts.py                           ── 语音合成 (edge-tts → silk)  │
│  douyin/                          ── 抖音/TikTok/B站 解析        │
│  news / weather / oilprice / epic / kfc                          │
│  bilibili_dynamic                 ── B站动态 + 订阅              │
│  scheduler_tasks                  ── APScheduler 定时任务        │
│  magnet / style_corpus / help_card / group_notice               │
│  ─────────────────────────────────────────────────────────────── │
│  以下为已废弃死代码（不再走 AI 对话路径）:                        │
│   router.py / handler.py / hermes_client.py / mcp_tools.py      │
└─────────────────────────────────────────────────────────────────┘
```

## 目录结构

```
E:\Project\
├── Py/                              # 微信注入层
│   ├── main.py                      # 主入口 (DLL 加载 + WS 桥接)
│   ├── NoveLoader.dll / NoveHelper.dll
│   └── data/quoted_images/          # 引用图片 CDN 缓存
│
├── ai_plugin/                       # NoneBot2 业务层
│   ├── bot.py                       # 启动入口 (FastAPI, :18765)
│   ├── pyproject.toml               # 插件注册 + 依赖
│   ├── .env.prod                    # API Key 配置
│   ├── ai_plugin/
│   │   ├── __init__.py              # 命令注册 + 功能初始化
│   │   ├── router.py                # 消息路由 (意图检测/分发)
│   │   ├── handler.py               # AIHandler (三级 fallback)
│   │   ├── hermes_client.py         # Hermes Agent 流式客户端
│   │   ├── tools.py                 # 工具注册表 (CALL: 格式)
│   │   ├── plugin_manager.py        # 插件热管理
│   │   ├── message_buffer.py        # 消息缓冲 (SQLite + LRU)
│   │   ├── image_generator.py       # 文生图 (MiniMax + GPT)
│   │   ├── image_editor.py          # 图生图 (GPT + MiniMax)
│   │   ├── video_generator.py       # 文生视频 (MiniMax)
│   │   ├── mcp_tools.py             # 联网搜索 + VLM
│   │   ├── tts.py                   # 语音合成 (edge-tts)
│   │   ├── douyin/                  # 抖音/TikTok/B站 解析
│   │   ├── news.py / weather.py / oilprice.py / epic.py / kfc.py
│   │   ├── magnet.py                # 磁力搜索
│   │   ├── bilibili_dynamic.py      # B站动态 + 订阅
│   │   ├── scheduler_tasks.py       # 定时任务
│   │   ├── style_corpus.py          # 语料爬取
│   │   ├── help_card.py             # 帮助卡片 (Pillow)
│   │   ├── group_notice.py          # 入群/退群通知
│   │   ├── config.py                # Pydantic 配置
│   │   └── utils.py                 # 通用工具函数
│   └── data/                        # 运行时数据
│       ├── embeddings.db            # 向量数据库
│       ├── chat_history.db          # 聊天历史
│       ├── plugin_state.json        # 插件启用状态
│       └── schedule_tasks.json      # 定时任务持久化
│
├── lib/hok_brain/                   # AI 核心库
│   ├── ai/minimax_client.py         # LLM 客户端
│   ├── brain/rule_engine.py         # 关键词兜底
│   ├── context/builder.py           # 系统提示构建
│   └── memory/embedding_store.py    # 向量存储
│
├── nonebot-plugin-tarot/            # 塔罗牌插件 (V12 适配)
├── nonebot-plugin-setu/             # 色图插件 (V12 适配)
├── nonebot-plugin-mystool/          # 米游社插件
├── nonebot-plugin-repeater/         # 复读机插件
├── nonebot-plugin-rocom/            # 洛克王国插件
├── Douyin_TikTok_Download_API/      # 抖音/TikTok 下载工具
└── README.md
```

## 启动

```bash
# 终端 1：AstrBot（先启动，Python 3.13）
cd E:\Project
astrbot_venv\Scripts\astrbot run

# 终端 2：微信注入层（后启动，Python 3.11 32-bit）
cd E:\Project\Py
C:\Users\Administrator\AppData\Local\Programs\Python\Python311-32\python.exe main.py
```

> **注入层必须 Python 3.11 32-bit** — DLL 注入要求 32 位进程。

## 配置

### AstrBot 主配置 (`data/cmd_config.json`)

```jsonc
{
  "platform": [{ "id": "aiocqhttp", "type": "aiocqhttp", "ws_reverse_port": 6199 }],
  "provider_sources": [
    { "id": "deepseek",  "provider": "deepseek" },   // 默认对话
    { "id": "openai_2",  "provider": "openai" },     // mimo (fallback + VLM)
    { "id": "openai",    "provider": "openai" },     // grok
    { "id": "openai_3",  "provider": "openai" },     // grok-4.3
    { "id": "google_gemini", "provider": "google" }
  ],
  "provider_settings": {
    "default_provider_id": "deepseek/deepseek-v4-pro",
    "fallback_chat_models": [
      "openai_2/mimo-v2.5",
      "openai/grok-4.20-0309-non-reasoning",
      "openai_3/grok-4.3-low"
    ],
    "default_image_caption_provider_id": "openai_2/mimo-v2.5",
    "web_search": true,
    "websearch_provider": "bocha"
  },
  "wake_prefix": ["/", "\\"]
}
```

### 环境变量 (ai_plugin/.env.prod，被 jiang_* 插件复用)

```env
ENV_NAME=prod

# GPT-Image (文生图 + 图生图)
GPT_IMAGE_API_KEY=sk-xxx
GPT_IMAGE_BASE_URL=https://freeapi.dgbmc.top
GPT_IMAGE_MODEL=gpt-image-2

# MiniMax (Video + Image 后端，仍被 image_generator / video_generator 使用)
MINIMAX_API_KEY=sk-xxx
MINIMAX_API_BASE_URL=https://api.minimaxi.com/v1

# 管理员 wxid（逗号分隔多个）
ADMIN_WXID=fengchenhao002

# 抖音 Cookie（视频解析用）
DOUYIN_COOKIE=xxx

# 网络代理（色图插件用）
HTTP_PROXY=http://127.0.0.1:7890
```

## 常见问题

| 问题 | 原因/解决 |
|------|----------|
| 图片生成失败 400 | GPT content_policy_violation，修改提示词 |
| 图生图提示"未下载" | 需长按图片 → 引用 → 再发提示词 |
| 命令不生效 | 必须 `/` 开头，或检查 `#插件列表` 是否已禁用 |
| AI 不回复 | 群聊需要 @机器人；检查 `#插件列表` 中 ai_chat 状态 |
| DLL 注入失败 | Python 必须 32 位 3.11 |
| 语音发送失败 | 检查 ffmpeg 是否在 PATH 中 |
| 抖音解析失败 | 检查 DOUYIN_COOKIE 是否过期 |
| 定时任务不执行 | 用 `/定时` 查看任务列表，检查 APScheduler 日志 |
