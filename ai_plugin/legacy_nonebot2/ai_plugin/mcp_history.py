"""本地 MCP Server — 暴露聊天记录查询工具给 Hermes Agent

启动一个轻量 HTTP 服务（SSE 或 Streamable HTTP），
让 Hermes 通过 mcp_servers 配置连接并使用工具。
"""
import os
import json
import time
import asyncio
import threading
from pathlib import Path

from nonebot import logger

from .message_buffer import get_buffer

MCP_HOST = os.getenv("NONEBOT_MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("NONEBOT_MCP_PORT", "8650"))
MCP_TOKEN = os.getenv("HERMES_API_KEY", "Abcd1234")

TOOLS = [
    {
        "name": "get_user_history",
        "description": "查询某个用户在群聊中的历史发言记录。可通过 wxid 或昵称查找。返回该用户最近的消息列表，用于了解一个人的说话风格、兴趣和性格。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "string",
                    "description": "用户的 wxid（如 fengchenhao002）。如果不知道 wxid，可以用 nickname 参数按昵称查找。",
                },
                "nickname": {
                    "type": "string",
                    "description": "用户昵称（模糊匹配）。当不知道 wxid 时使用此参数。",
                },
                "group_id": {
                    "type": "string",
                    "description": "限定在某个群内查询（群 ID）。不填则跨所有群查询。",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 30，最大 100。",
                    "default": 30,
                },
            },
        },
    },
    {
        "name": "get_recent_messages",
        "description": "获取某个群聊最近的消息记录。用于了解群里最近在聊什么话题。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "group_id": {
                    "type": "string",
                    "description": "群 ID（如 51632940287@chatroom）",
                },
                "limit": {
                    "type": "integer",
                    "description": "返回条数，默认 20，最大 100。",
                    "default": 20,
                },
            },
            "required": ["group_id"],
        },
    },
]


def _handle_tool_call(name: str, arguments: dict) -> str:
    buf = get_buffer()

    if name == "get_user_history":
        user_id = arguments.get("user_id", "")
        nickname = arguments.get("nickname", "")
        group_id = arguments.get("group_id")
        limit = min(int(arguments.get("limit", 30)), 100)

        if not user_id and nickname:
            user_id = buf.find_user_by_nickname(nickname, group_id) or ""

        if not user_id:
            return json.dumps({"error": "未找到该用户，请提供 wxid 或更准确的昵称"}, ensure_ascii=False)

        messages = buf.get_user_history(user_id, group_id=group_id, limit=limit)
        if not messages:
            return json.dumps({"error": f"未找到用户 {user_id} 的聊天记录"}, ensure_ascii=False)

        result = []
        for m in messages:
            result.append({
                "time": time.strftime("%m-%d %H:%M", time.localtime(m.ts)),
                "nickname": m.nickname,
                "user_id": m.user_id,
                "group_id": m.group_id,
                "content": m.content,
            })
        return json.dumps({"user_id": user_id, "nickname": messages[0].nickname, "count": len(result), "messages": result}, ensure_ascii=False)

    elif name == "get_recent_messages":
        group_id = arguments.get("group_id", "")
        limit = min(int(arguments.get("limit", 20)), 100)

        if not group_id:
            return json.dumps({"error": "请提供 group_id"}, ensure_ascii=False)

        messages = buf.get_recent(group_id, limit=limit)
        if not messages:
            return json.dumps({"error": f"群 {group_id} 暂无聊天记录"}, ensure_ascii=False)

        result = []
        for m in messages:
            result.append({
                "time": time.strftime("%m-%d %H:%M", time.localtime(m.ts)),
                "nickname": m.nickname,
                "user_id": m.user_id,
                "content": m.content,
                "is_bot": m.is_bot,
            })
        return json.dumps({"group_id": group_id, "count": len(result), "messages": result}, ensure_ascii=False)

    return json.dumps({"error": f"未知工具: {name}"}, ensure_ascii=False)


async def _run_server():
    """简易 JSON-RPC over HTTP 实现（兼容 Hermes MCP client）"""
    from aiohttp import web

    async def handle_mcp(request: web.Request):
        auth = request.headers.get("Authorization", "")
        if MCP_TOKEN and auth != f"Bearer {MCP_TOKEN}":
            return web.json_response({"error": "unauthorized"}, status=401)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "invalid json"}, status=400)

        method = body.get("method", "")
        req_id = body.get("id")
        params = body.get("params", {})

        if method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "nonebot-chat-history", "version": "1.0.0"},
            }
        elif method == "tools/list":
            result = {"tools": TOOLS}
        elif method == "tools/call":
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {})
            output = _handle_tool_call(tool_name, arguments)
            result = {"content": [{"type": "text", "text": output}]}
        elif method == "notifications/initialized":
            return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": {}})
        else:
            result = {"error": f"unsupported method: {method}"}

        return web.json_response({"jsonrpc": "2.0", "id": req_id, "result": result})

    app = web.Application()
    app.router.add_post("/mcp", handle_mcp)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, MCP_HOST, MCP_PORT)
    await site.start()
    logger.info(f"[MCP] Chat history MCP server started on {MCP_HOST}:{MCP_PORT}")


def start_mcp_server():
    """在后台线程启动 MCP server"""
    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_server())
        loop.run_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    logger.info("[MCP] Chat history MCP server thread started")
