# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Runtime Constraint

**Python 3.11 32-bit is mandatory** for the WeChat injection layer — the DLL injection requires 32-bit Python.
Path: `C:\Users\Administrator\AppData\Local\Programs\Python\Python311-32\python.exe`

AstrBot itself runs on Python 3.13 (in `astrbot_venv`).

## Starting the Project

Two processes, start in order:

```bash
# Terminal 1: AstrBot (must be up first)
cd E:\Project
astrbot_venv\Scripts\astrbot run

# Terminal 2: WeChat injection layer
cd E:\Project\Py
C:\Users\Administrator\AppData\Local\Programs\Python\Python311-32\python.exe main.py
```

No test suite or linting is configured.

## Architecture

Dual-process system communicating via OneBot V11 WebSocket (`ws://127.0.0.1:6199/ws`):

```
WeChat.exe ←DLL HOOK→ Py/main.py (Py3.11-32) ←V11 WS→ AstrBot (Py3.13) ←→ LLM/Plugins
```

- **AstrBot WebUI**: http://localhost:6185 (user: astrbot)
- **aiocqhttp adapter**: reverse WS on port 6199
- **LLM**: managed by AstrBot's provider system (DeepSeek default + mimo/Grok fallback chain)

### Py/main.py — WeChat Injection Layer (Python 3.11-32)
Loads NoveLoader.dll/NoveHelper.dll into WeChat, captures messages via shared memory, runs a `NoneBotWsClient` that bridges to AstrBot via OneBot V11 WebSocket. Handles raw send/receive for text, image, video, voice, file.

### AstrBot (Python 3.13)
- **WebUI**: http://localhost:6185
- **Config**: `data/cmd_config.json`
- **Plugins**: `data/plugins/` (Star plugin system)
- **LLM Provider**: AstrBot native provider system, configured in `data/cmd_config.json`

## AI Conversation Path (AstrBot native)

AI chat is handled entirely by AstrBot's provider pipeline. The legacy Hermes streaming + three-tier fallback in `ai_plugin/` is **deprecated and no longer on the active path**.

- **Default provider**: `deepseek/deepseek-v4-pro`
- **Fallback chain** (configured in `provider_settings.fallback_chat_models`):
  1. `openai_2/mimo-v2.5`
  2. `openai/grok-4.20-0309-non-reasoning`
  3. `openai_3/grok-4.3-low`
- **Image caption (VLM)**: `openai_2/mimo-v2.5`
- **Web search**: AstrBot built-in `web_search` (bocha engine), no longer MiniMax MCP
- **Wake prefix**: `/` or `\` (group chat also accepts `@bot`); private chat needs no prefix

## ai_plugin/ — Legacy Module Library (NOT a running process)

`ai_plugin/` is no longer launched as an independent NoneBot2 bot. It survives as a **module library** that AstrBot plugins under `data/plugins/jiang_*` import via `sys.path`:

- `jiang_commands` — reuses `news` / `weather` / `epic` / `kfc` / `oilprice` / `bilibili_dynamic` / `help_card`
- `jiang_douyin` — reuses `douyin/` parser
- `jiang_image` — reuses `image_generator` / `image_editor`
- `jiang_mystool` / `jiang_repeater` / `jiang_rocom` / `jiang_schedule` / `jiang_group_notice` — corresponding modules

`data/plugins/_nonebot_stubs.py` shims `nonebot` so NoneBot-style modules can be imported inside AstrBot.

The old NoneBot2 runtime and AI-chat path are archived under `ai_plugin/legacy_nonebot2/` and are **not on the active import path**:
- `bot.py` / `nonebot2.toml` / `pyproject.toml` — old NoneBot2 process entry/config/dependencies
- `ai_plugin/__init__.py` — old NoneBot plugin entry
- `ai_plugin/router.py` — old NoneBot message router (1300+ lines) with Hermes streaming dispatch
- `ai_plugin/handler.py` — `AIHandler` three-tier fallback (MiniMax → Grok → DeepSeek → RuleEngine)
- `ai_plugin/hermes_client.py` — Hermes Agent streaming client
- `ai_plugin/mcp_tools.py` / `ai_plugin/mcp_history.py` — MiniMax MCP search/VLM bridge
- `ai_plugin/tools.py` — old `CALL:tool:params` protocol

Business modules still in use or kept as reusable helpers: `image_generator.py`, `image_editor.py`, `video_generator.py`, `tts.py`, `douyin/`, `news.py`, `weather.py` (ALapi), `oilprice.py`, `epic.py`, `kfc.py`, `bilibili_dynamic.py`, `scheduler_tasks.py`, `magnet.py`, `style_corpus.py`, `help_card.py`, `group_notice.py`, `plugin_manager.py`, `utils.py`, `config.py`.

## AstrBot Plugin Layer (data/plugins/)

- **Core AI / memory**: `astrbot_plugin_self_iterative_core`, `astrbot_plugin_self_learning`, `astrbot_plugin_livingmemory`, `astrbot_plugin_mnemosyne`, `astrbot_plugin_proactive_chat`
- **Search / utilities**: `astrbot_plugin_kimi_web_search`, `astrbot_plugin_qq_group_daily_analysis`, `astrbot_plugin_zhenxunribao`, `astrbot_plugin_bittorrent`, `meme_manager`
- **Business (jiang_*)**: `jiang_commands`, `jiang_douyin`, `jiang_group_notice`, `jiang_image`, `jiang_mystool`, `jiang_repeater`, `jiang_rocom`, `jiang_schedule`
- **Connector (disabled)**: `astrbot_plugin_hapi_connector_disabled`

## Key Behavioral Rules

- **Group chat**: must @bot to trigger AI reply; commands (`/` or `\`) work without @
- **Private chat**: all messages get AI reply (no wake prefix)
- **Bot persona**: "姜小妹" — casual, direct, no emoji, no AI-assistant phrasing
- **Admin commands**: prefixed with `#`, restricted to wxids in `ADMIN_WXID` env var (currently `fengchenhao002`)
- **Self-messages and system notifications**: filtered by AstrBot platform settings (`ignore_bot_self_message`)

## Configuration

- `data/cmd_config.json` — AstrBot main config (platform, provider, plugin, dashboard)
- `ai_plugin/.env.prod` — Legacy API keys still consumed by reused modules (GPT-Image, Douyin cookie, etc.)
- `astrbot_venv\` — AstrBot's Python 3.13 virtual environment

## Git Structure

Main repo with submodules: `Py`, `ai_plugin`, `lib/hok_brain`, `nonebot-plugin-mystool`.

---

## Important Notes (from project memory)

### Admin Identity
管理员 wxid 固定为 `fengchenhao002`，无论微信昵称改成什么，这个 wxid 始终是管理员。

### AstrBot WeChat Compat Patches (must re-apply after every `pip install --upgrade astrbot`)

**1. aiocqhttp_message_event.py — session_id non-numeric support**
File: `astrbot_venv/Lib/site-packages/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_message_event.py`
WeChat group ID is `xxx@chatroom` format (not pure digits). Change `session_id.isdigit()` to:
```python
group_id = int(session_id) if session_id.isdigit() else session_id
```

**2. aiocqhttp_platform_adapter.py — at message wxid support**
File: `astrbot_venv/Lib/site-packages/astrbot/core/platform/sources/aiocqhttp/aiocqhttp_platform_adapter.py`
WeChat user IDs are `wxid_xxx` strings. Wrap `int(m["data"]["qq"])` in try-except, skip `get_group_member_info` for non-numeric IDs, use `name` field from message segment instead.

### WeChat Raw XML Send

- DLL API `type=11214` is the CDN raw XML send interface.
- Request shape: `{"type":11214,"data":{"to_wxid":"filehelper","content":"<appmsg ...>...</appmsg>"}}`.
- Important: `content` should be the inner `<appmsg>` XML. Received app messages are often stored as full `<?xml?><msg><appmsg>...</appmsg>...</msg>` raw XML, but `type=11214` should be sent the extracted `<appmsg>...</appmsg>` portion.
- Playable music cards are `<appmsg>` messages with `<type>3</type>`, a playable `<dataurl>`, and `<songalbumurl>` for cover art.

### Image Generation
- **Dual backend**: MiniMax `image-01` + GPT-Image `gpt-image-2` (via `freeapi.dgbmc.top` proxy, API key in `.env.prod`)
- **grok-imagine-image-lite** also available on `freeapi.dgbmc.top`, works well (fast, ~10s)
- **gpt-image-2** often times out on free proxies — use `grok-imagine-image-lite` as fallback
- Admin command `#切换图片模型 gpt/minimax` to switch backend at runtime
- Images auto-cleaned from `data/images/` every 2 hours

### Video Generation
Shelved — user's token plan doesn't support video models. Code remains in `video_generator.py`, not initialized.

### Douyin/TikTok/Bilibili Integration
Embedded as `ai_plugin/ai_plugin/douyin/` submodule (based on Evil0ctal/Douyin_TikTok_Download_API).
- Images: ≤5 sent to group, >5 sent to file transfer (avoid spam), auto webp→jpg
- Videos: watermark-free download (>25MB falls back to link), 403 falls back to text+link
- Bilibili: parses title/author/stats, no direct download
- Cookie configured in `.env.prod` `DOUYIN_COOKIE`

### Message Processing Priority
```
User message → #commands → Douyin/Bilibili/TikTok links → Image gen → Video gen → Search-enhanced AI reply → Plain AI reply
```

### Collaboration Rules
- **Before coding**: deeply understand the project and code, don't guess architecture intent
- **Don't submit**: unrelated temp files; ask before committing
- **memu-bot decisions**: `ignored` means AI chose not to reply (not a service crash); when @-mentioned, must reply (AI judgment issue, not code bug)

### Daily Report Convention
At end of each dev session, generate a daily report (`daily-YYYY-MM-DD.md`) summarizing all changes, issues, and fixes.
