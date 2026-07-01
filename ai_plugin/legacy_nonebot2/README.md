# Legacy NoneBot2 Archive

This directory contains the retired NoneBot2 runtime and old AI conversation
path. It is kept for reference only and is not part of the active WeChat bot.

Active runtime:

```text
WeChat injection layer -> OneBot V11 WebSocket -> AstrBot -> data/plugins/jiang_*
```

Archived pieces:

- `bot.py` and `nonebot2.toml`: old NoneBot2 process entry/config.
- `pyproject.toml`: old NoneBot2 dependency and plugin registration metadata.
- `ai_plugin/__init__.py`: old NoneBot2 plugin registration entry.
- `ai_plugin/router.py`: old message router and Hermes streaming dispatch.
- `ai_plugin/handler.py`: old `AIHandler` provider fallback path.
- `ai_plugin/hermes_client.py`: old Hermes Agent streaming client.
- `ai_plugin/mcp_tools.py` and `ai_plugin/mcp_history.py`: old MiniMax MCP
  search/VLM/history bridge.
- `ai_plugin/tools.py`: old `CALL:tool:params` tool protocol.

Do not fix active AstrBot behavior here. If a feature is still needed, port it
into an AstrBot plugin under `data/plugins/jiang_*` or a side-effect-free helper
module under `ai_plugin/ai_plugin`.
