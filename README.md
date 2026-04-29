# 微信 AI 助手

基于 NoneBot2 + MiniMax 的微信个人号 AI 回复机器人。

## 架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        微信客户端 (WeChat.exe)                    │
└─────────────────────────────────────────────────────────────────┘
                              │
              ┌───────────────┴───────────────┐
              │        NoveLoader.dll          │
              │       (进程注入/HOOK)           │
              │                                │
              │  • 捕获微信消息                  │
              │  • 拦截发送操作                  │
              │  • 共享内存回传 Python           │
              └───────────────┬───────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────────┐
│                     Py/main.py (Python SDK)                      │
│                                                                  │
│  WeChatService ──────── DLL 封装、微信多开、消息收发              │
│  NoneBotWsClient ────── WebSocket 客户端 → 连接 NoneBot2         │
└──────────────────────────────┬──────────────────────────────────┘
                               │ ws://127.0.0.1:18765/onebot/v12/ws
                               │ (OneBot V12 协议)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                   ai_plugin/bot.py (NoneBot2)                     │
│                                                                  │
│  ai_plugin/__init__.py ── 消息匹配、过滤系统消息                  │
│  ai_plugin/handler.py  ── AIHandler: AI 回复生成                 │
│  ai_plugin/group_notice.py ── 入群/退群通知卡片                  │
└──────────────────────────────┬──────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│                 lib/hok_brain (AI 大脑核心库)                     │
│                                                                  │
│  schemas/  ── EventEnvelope, ReplyAction                        │
│  brain/    ── ReplyContext (上下文封装), RuleEngine (兜底规则)    │
│  ai/       ── MiniMaxClient (LLM 调用)                           │
│  context/  ── ContextBuilder (系统提示构建)                       │
│  memory/   ── EmbeddingStore (向量检索), ProfileExtractor (画像) │
└─────────────────────────────────────────────────────────────────┘
```

## 目录结构

```
E:\Project\
├── Py/                          # 微信注入层 (Python SDK + DLL)
│   ├── main.py                  # 主入口，启动 WeChatService + WebSocket 客户端
│   ├── NoveLoader.dll           # 进程注入器
│   └── NoveHelper.dll           # 微信 HOOK
│
├── ai_plugin/                   # NoneBot2 AI 插件
│   ├── bot.py                   # NoneBot2 启动入口 (FastAPI, :18765)
│   ├── pyproject.toml           # 项目配置 + 插件注册
│   ├── nonebot2.toml            # NoneBot 配置
│   ├── .env.prod                # 环境变量 (API Key 等)
│   ├── ai_plugin/
│   │   ├── __init__.py          # 消息匹配器 (on_message + 规则过滤)
│   │   ├── handler.py           # AIHandler (三级回复策略)
│   │   ├── config.py            # 配置模型
│   │   └── group_notice.py      # 群事件通知 (入群/退群卡片)
│   ├── tools/
│   │   └── clean_db_profiles.py # 用户画像数据库清洗
│   └── data/
│       └── embeddings.db        # 向量存储 + 用户画像
│
├── lib/hok_brain/               # AI 大脑核心库 (共享)
│   ├── schemas/                 # 事件模型、回复动作
│   ├── brain/                   # 上下文封装、规则引擎
│   ├── ai/                      # MiniMax API 客户端
│   ├── context/                 # 系统提示词构建
│   └── memory/                  # 嵌入向量存储、用户画像、用户数据库
│
├── nonebot-plugin-mystool/      # 米游社插件 (第三方)
└── docs/                        # 开发文档
```

## 两个进程

系统由两个独立进程组成，通过 OneBot V12 WebSocket 通信：

| 进程 | 入口 | 职责 |
|------|------|------|
| **注入进程** | `Py/main.py` | 加载 DLL 注入微信、捕获/发送消息、WebSocket 客户端 |
| **AI 进程** | `ai_plugin/bot.py` | NoneBot2 服务器、消息处理、AI 回复生成 |

## 消息处理流程

```
微信消息
  → DLL HOOK 捕获
  → 共享内存 (33字节)
  → Py/main.py JSON 解析
  → WebSocket → NoneBot2 (OneBot V12)
  → ai_plugin 过滤 (自身消息/系统消息/非@)
  → hok_brain 上下文构建
  → AI 回复生成 (三级策略)
  → WebSocket 回传
  → DLL 发送微信消息
```

## AI 回复策略

| 优先级 | 策略 | API/来源 | 说明 |
|--------|------|----------|------|
| 1 | MiniMax 主模型 | `MiniMax-M2.7-highspeed` | 主要 AI |
| 2 | Fallback 备用 | `grok-4.20-0309` (freeapi) | 主接口失败时 |
| 3 | RuleEngine | 本地规则引擎 | API 全挂时兜底 |
| 失败 | 硬编码文本 | `"抱歉，我现在有点忙…"` | 最终保底 |

## 触发条件

- **私聊**：所有消息都回复
- **群聊**：需 @机器人 才回复（潜伏模式：未@时仍会存储对话用于画像学习）
- **过滤**：跳过自身消息、以 `/` 开头的命令、系统通知类消息

## 关键功能

- **群通知**：新成员入群发欢迎卡片，成员退群发离开卡片（含昵称/WxID）
- **用户画像**：自动从对话中提取喜好、口头禅等，存入向量数据库
- **对话记忆**：向量检索历史对话，用于 AI 上下文
- **自动重连**：WebSocket 断线后 3 秒自动重连

## 前置条件

### 1. 微信已安装
```powershell
dir "D:\SofeWare\Weixin\Weixin.exe"
```
默认路径在 `Py/main.py` 中硬编码，可按需修改。

### 2. Python 3.11 32位
```bash
python --version        # 需要 3.11
python -c "import struct; print(struct.calcsize('P') * 8)"  # 确认是 32 位
路径在C:\Users\Administrator\AppData\Local\Programs\Python\Python311-32\python.exe
```
DLL 为 32 位，Python 必须匹配。

### 3. 依赖安装
```bash
cd E:\Project\ai_plugin
pip install -e .
```

## 启动步骤

建议用两个终端分别启动：

```bash
# 终端 1：启动 AI 服务 (先启动，让 WebSocket Server 就绪)
cd E:\Project\ai_plugin
python bot.py

# 终端 2：启动微信注入
cd E:\Project\Py
python main.py
```

### 顺序说明
AI 进程先启动监听 127.0.0.1:18765，注入进程启动后会自动连接 WebSocket，然后通过 DLL 多开微信并注入。

## 配置

### 环境变量 (ai_plugin/.env.prod)
```
ENV_NAME=prod
BOT_IDENTIFIER=wechat_bot
BOT_WXID=wxid_xxxxxxxxxxxxx
MINIMAX_API_KEY=sk-xxx
MINIMAX_API_BASE_URL=https://api.minimaxi.com/v1
FALLBACK_API_KEY=sk-xxx
FALLBACK_API_BASE_URL=https://freeapi.dgbmc.top/v1
```

### 功能开关 (ai_plugin/config.py)
```
enable_stealth_mode = True    # 群聊潜伏模式
enable_private_reply = True   # 私聊回复
```

### 微信路径 (Py/main.py)
```python
wechat_exe_path = r"D:\SofeWare\Weixin\Weixin.exe"
```

## 常见问题

### Q1: "NoveLoader.dll not found"
DLL 文件必须在 `Py/` 目录下，与 `main.py` 同目录。

### Q2: 微信无法注入
确保微信未运行，`main.py` 会自动多开并注入。如已运行，可在代码中启用绑定已有进程模式。

### Q3: AI 不回复
- 确认 `ai_plugin/` 进程已先启动
- 检查 `.env.prod` 中 API Key 是否正确
- 确认 `BOT_WXID` 与机器人微信 ID 一致
- 查看 `wechat_service.log` 日志

### Q4: WebSocket 连接失败
- 确认 18765 端口未被占用
- 检查两个进程是否都在本机运行（127.0.0.1）

## 开发调试

### 查看日志
- 微信注入端：`Py/wechat_service.log`
- AI 端：NoneBot2 标准输出

### 清洗用户画像数据
```bash
cd E:\Project\ai_plugin
python tools/clean_db_profiles.py
```

### 运行测试
```bash
cd E:\Project\nonebot-plugin-mystool
pytest tests/ -v
```

## Git 仓库

本项目包含 4 个独立 Git 仓库：

| 目录 | 说明 |
|------|------|
| `ai_plugin/` | AI 插件主仓库 |
| `lib/hok_brain/` | AI 核心库 |
| `Py/` | 微信注入 SDK |
| `nonebot-plugin-mystool/` | 米游社插件 (上游: stable 分支) |
