# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

The following files in `ai_plugin/` are **dead code on the active AI-chat path** (still present but unused for AI conversation):
- `router.py` — old NoneBot message router (1300+ lines) with Hermes streaming dispatch
- `handler.py` — `AIHandler` three-tier fallback (MiniMax → Grok → DeepSeek → RuleEngine)
- `hermes_client.py` — Hermes Agent streaming client
- `mcp_tools.py` / `mcp_history.py` — MiniMax MCP search/VLM bridge
- `__init__.py` — old NoneBot plugin entry

Business modules still in use: `image_generator.py`, `image_editor.py`, `video_generator.py`, `tts.py`, `douyin/`, `news.py`, `weather.py` (ALapi), `oilprice.py`, `epic.py`, `kfc.py`, `bilibili_dynamic.py`, `scheduler_tasks.py`, `magnet.py`, `style_corpus.py`, `help_card.py`, `group_notice.py`, `plugin_manager.py`, `utils.py`, `config.py`.

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
