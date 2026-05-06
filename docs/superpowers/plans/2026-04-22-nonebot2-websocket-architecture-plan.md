# NoneBot2 WebSocket 架构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** main.py 作为 WebSocket server，NoneBot2 作为 reverse WS client，通过 OneBot11 JSON 协议通信，实现完整 NB2 架构。

**Architecture:** main.py 新增 WebSocket server 监听 18765 端口，NB2 onebot11 适配器配置为 reverse_ws client 连接该端口。微信消息通过 WS 转发给 NB2，NB2 回复通过 WS 回调 main.py 发送。

**Tech Stack:** Python websockets 库（main.py 端），nonebot-adapter-onebot11（NB2 端）

---

## 文件结构

| 操作 | 文件 |
|------|------|
| 修改 | `Py/main.py` — 新增 WsServer 组件，改造消息处理流程 |
| 修改 | `ai_plugin/nonebot2.toml` — 新增 reverse_ws 配置 |
| 修改 | `ai_plugin/__init__.py` — 移除 HTTP /decide 端点 |
| 删除 | `ai_plugin/start_nonebot.py` |
| 删除 | `ai_plugin/standalone_server.py` |

---

## Task 1: 安装 websockets 依赖

**Files:**
- Modify: `ai_plugin/pyproject.toml`

- [ ] **Step 1: 添加 websockets 依赖到 pyproject.toml**

```toml
[project]
name = "ai_plugin"
version = "0.1.0"
description = "AI Chat Plugin for NoneBot"
requires-python = ">=3.10"
dependencies = ["websockets>=12.0"]
```

- [ ] **Step 2: 安装依赖**

```bash
cd E:\Project\ai_plugin
pip install websockets>=12.0
```

---

## Task 2: 修改 nonebot2.toml 配置

**Files:**
- Modify: `ai_plugin/nonebot2.toml:1-5`

- [ ] **Step 1: 更新 nonebot2.toml**

```toml
fastapi_host = "127.0.0.1"
fastapi_port = 8765

[adapter]
type = "onebot11"
reverse_ws_urls = ["ws://127.0.0.1:18765/onebot_ws"]
```

---

## Task 3: 修改 ai_plugin/__init__.py — 移除 HTTP /decide 端点

**Files:**
- Modify: `ai_plugin/ai_plugin/__init__.py`

- [ ] **Step 1: 读取当前 ai_plugin/__init__.py 并移除 HTTP 端点相关代码**

删除以下代码块（第 100-156 行附近）：
```python
# ============================================================================
# HTTP API 端点（用于被 Py/main.py 调用）
# ============================================================================
# 注意: NoneBot 本身是机器人框架，不直接暴露 HTTP API。
# ...

from nonebot import on_raw_http_request

@on_raw_http_request(method="POST", path="/decide")
async def handle_decide_request(request):
    ...
```

保留 matcher 和 handler 相关代码。

---

## Task 4: 修改 Py/main.py — 新增 WebSocket Server

**Files:**
- Modify: `Py/main.py` — 新增 `NoneBotWsServer` 类和消息转发逻辑

- [ ] **Step 1: 添加 import**

在 `Py/main.py` 顶部添加：
```python
import websockets
import asyncio
import json as _json
```

- [ ] **Step 2: 新增 NoneBotWsServer 类（在 `_nonebot_process = None` 之前，约第 67 行）**

```python
class NoneBotWsServer:
    """WebSocket Server，接收 NoneBot2 的 reverse_ws 连接"""

    def __init__(self, host="127.0.0.1", port=18765, send_text_fn=None):
        self.host = host
        self.port = port
        self.clients = set()
        self._running = False
        self._send_text_fn = send_text_fn  # helper_send_text 回调

    async def start(self):
        """启动 WebSocket server"""
        self._running = True
        logger.info(f"NoneBot WS Server 启动中 {self.host}:{self.port}")
        async with websockets.serve(self._handle_client, self.host, self.port):
            logger.info(f"NoneBot WS Server 已启动 ws://{self.host}:{self.port}/onebot_ws")
            await asyncio.Future()

    async def _handle_client(self, websocket):
        """处理 NB2 客户端连接"""
        self.clients.add(websocket)
        logger.info(f"NoneBot 已连接，当前客户端数: {len(self.clients)}")
        try:
            async for raw_message in websocket:
                await self._handle_message(raw_message)
        except Exception as e:
            logger.error(f"WS 客户端异常: {e}")
        finally:
            self.clients.discard(websocket)
            logger.info(f"NoneBot 已断开，当前客户端数: {len(self.clients)}")

    async def _handle_message(self, raw_message: str):
        """处理 NB2 发回的 OneBot11 响应"""
        try:
            data = _json.loads(raw_message)
            retcode = data.get("retcode", -1)
            action = data.get("action", "")
            echo = data.get("echo", "")
            msg_data = data.get("data", {})

            if retcode != 0:
                logger.warning(f"NB2 返回错误: retcode={retcode} action={action}")
                return

            # 处理发送消息响应
            if action == "send_msg":
                # 提取实际消息内容
                messages = msg_data.get("message", [])
                if not messages:
                    return
                # 解析消息段（可能是纯文本或 CQ 码）
                text_content = self._extract_text(messages)
                if not text_content:
                    return
                # 根据 message_type 确定发送目标
                message_type = msg_data.get("message_type", "")
                if message_type == "private":
                    to_wxid = str(msg_data.get("user_id", ""))
                elif message_type == "group":
                    to_wxid = str(msg_data.get("user_id", ""))
                else:
                    return
                if to_wxid and self._send_text_fn:
                    self._send_text_fn(to_wxid=to_wxid, content=text_content)
                    logger.info(f"AI 回复: {text_content}")

        except Exception as e:
            logger.error(f"解析 NB2 响应失败: {e}")

    def _extract_text(self, messages) -> str:
        """从 OneBot11 消息段中提取纯文本"""
        if isinstance(messages, str):
            return messages
        parts = []
        for seg in messages:
            if isinstance(seg, dict):
                if seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
                # 忽略其他类型（图片、@等）
            elif isinstance(seg, str):
                parts.append(seg)
        return "".join(parts)

    async def send_to_nb2(self, action: str, params: dict, echo: str = ""):
        """发送 OneBot11 请求到 NB2"""
        if not self.clients:
            logger.warning("无 NB2 客户端连接，消息未发送")
            return
        message = {
            "action": action,
            "params": params,
            "echo": echo,
        }
        for client in self.clients:
            try:
                await client.send(_json.dumps(message))
            except Exception as e:
                logger.error(f"发送消息到 NB2 失败: {e}")

    def stop(self):
        self._running = False
```

- [ ] **Step 3: 修改消息处理流程，找到 `_handle_chat_message` 方法（约第 465 行）**

找到现有的 HTTP 调用逻辑：
```python
# 调用 NoneBot HTTP API
result = call_nonebot_api("/decide", event_data)

if result and result.get("decision") == "reply":
    reply_text = result.get("text", "")
    if reply_text:
        logger.info(f"AI 回复: {reply_text}")
        self.service.helper_send_text(to_wxid=from_wxid, content=reply_text)
elif not result:
    logger.warning("NoneBot API 未返回结果，尝试直接调用 AI Handler")
    self._handle_chat_message_fallback(from_wxid, msg, chat_id, is_group)
```

替换为 WebSocket 转发：
```python
# 通过 WebSocket 转发消息到 NB2
nb2_ws = get_nb2_ws_server()
if nb2_ws and nb2_ws.clients:
    # 构建 OneBot11 消息格式
    if is_group:
        nb_params = {
            "message_type": "group",
            "group_id": chat_id,
            "user_id": from_wxid,
            "message": msg,
        }
    else:
        nb_params = {
            "message_type": "private",
            "user_id": from_wxid,
            "message": msg,
        }
    # 从同步上下文中调度 async 协程
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.call_soon_threadsafe(
                lambda: asyncio.create_task(nb2_ws.send_to_nb2("send_msg", nb_params, echo=data.get("msgid", "")))
            )
        else:
            asyncio.create_task(nb2_ws.send_to_nb2("send_msg", nb_params, echo=data.get("msgid", "")))
    except Exception as e:
        logger.error(f"转发消息到 NB2 失败: {e}")
else:
    logger.warning("NB2 未连接，消息未转发")
```

- [ ] **Step 4: 添加全局 WS Server 实例和获取函数（在 `get_ai_handler` 之后，约第 175 行）**

```python
_nb2_ws_server = None

def get_nb2_ws_server() -> NoneBotWsServer:
    """获取 NB2 WebSocket Server 单例"""
    global _nb2_ws_server
    return _nb2_ws_server

def set_nb2_ws_server(server: NoneBotWsServer):
    """设置 NB2 WebSocket Server 实例"""
    global _nb2_ws_server
    _nb2_ws_server = server
```

- [ ] **Step 5: 修改 main() 函数中 NoneBot 检查逻辑（约第 855 行）**

删除 `start_nonebot_subprocess()` 调用，替换为：
```python
# 启动 NB2 WebSocket Server
nb2_ws = NoneBotWsServer(
    host="127.0.0.1",
    port=18765,
    send_text_fn=lambda to_wxid, content: service.helper_send_text(to_wxid, content)
)
set_nb2_ws_server(nb2_ws)

# 在独立线程中运行 WS Server
def run_ws_server():
    asyncio.run(nb2_ws.start())

ws_thread = threading.Thread(target=run_ws_server, daemon=True)
ws_thread.start()

logger.info("NB2 WebSocket Server 已启动，等待连接...")
```

- [ ] **Step 6: 删除 `start_nonebot_subprocess` 和 `stop_nonebot_subprocess` 函数**

删除约第 72-113 行的这两个函数。

- [ ] **Step 7: 删除 `call_nonebot_api` 函数**

删除约第 205-221 行的 `call_nonebot_api` 函数。

- [ ] **Step 8: 删除 `_handle_chat_message_fallback` 方法**

删除约第 493-517 行的 `self._handle_chat_message_fallback` 方法。

---

## Task 5: 删除废弃文件

**Files:**
- Delete: `ai_plugin/start_nonebot.py`
- Delete: `ai_plugin/standalone_server.py`

- [ ] **Step 1: 删除 start_nonebot.py**

```bash
rm E:/Project/ai_plugin/start_nonebot.py
```

- [ ] **Step 2: 删除 standalone_server.py**

```bash
rm E:/Project/ai_plugin/standalone_server.py
```

---

## Task 6: 验证

**Files:**
- None（手动测试）

- [ ] **Step 1: 确认依赖已安装**

```bash
pip show websockets
```

预期：显示 websockets 版本信息

- [ ] **Step 2: 检查 NoneBot2 适配器已安装**

```bash
pip show nonebot-adapter-onebot11
```

预期：显示适配器版本信息（如未安装则 `pip install nonebot-adapter-onebot11`）

- [ ] **Step 3: 启动 NoneBot2**

```bash
cd E:\Project\ai_plugin
nb run
```

预期：NB2 启动，无报错

- [ ] **Step 4: 启动 main.py**

```bash
cd E:\Project\Py
python main.py
```

预期：
- "NB2 WebSocket Server 已启动，等待连接..."
- "NoneBot 已连接，当前客户端数: 1"（NB2 连上后）

- [ ] **Step 5: 发送测试消息**

在微信群中 @机器人 发消息，预期 NB2 收到并回复。

---

## 启动顺序

1. 先启动 NoneBot2：
```bash
cd E:\Project\ai_plugin
nb run
```

2. 再启动 main.py：
```bash
cd E:\Project\Py
python main.py
```

---

## 架构总览（完成后）

```
微信 → DLL → main.py (WS Server :18765) → NB2 onebot11 (WS Client)
                                              ↓
                                        ai_plugin 处理
                                              ↓
                                        WS 回复 → main.py → helper_send_text → DLL → 微信
```
