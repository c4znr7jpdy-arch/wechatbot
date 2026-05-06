# NoneBot2 WebSocket 架构设计

## 背景

当前 main.py 通过 HTTP POST 调用独立 FastAPI 服务（`start_nonebot.py`），这不是真正的 NoneBot2 架构。目标：main.py 作为 WebSocket server，NB2 作为 reverse WS client 连接，实现完整 OneBot11 协议通信。

## 架构决策

| 项目 | 选择 |
|------|------|
| 传输层 | OneBot11 JSON over WebSocket（逆向连接） |
| WS Server | main.py（新增 WebSocket server） |
| WS Client | NB2 onebot11 适配器（reverse_ws 模式） |
| WS 端口 | `18765`（HTTP 8765 保持不变） |
| WS 路径 | `/onebot_ws` |
| 认证 | 无 token（同机直连） |

## 整体流程

```
微信 → DLL → main.py (WS Server) ← NB2 onebot11 (WS Client)
                         ↓
                   消息转发至 NB2
                         ↓
                   ai_plugin + 市场插件处理
                         ↓
                   OneBot11 回复 → WS → main.py → helper_send_text → DLL → 微信
```

## 组件职责

### main.py（新增）

| 组件 | 职责 |
|------|------|
| `WsServer` | WebSocket server，监听 18765，接收 NB2 连接 |
| `ws_to_nonebot()` | 将微信消息转发给 NB2（OneBot11 格式） |
| `handle_nb2_reply()` | 接收 NB2 回复，解析并调用发送 |

### NB2 配置变更

| 文件 | 变更 |
|------|------|
| `nonebot2.toml` | 新增 reverse_ws 适配器配置 |
| `pyproject.toml` | 无变更 |
| `ai_plugin/__init__.py` | 消息 matcher 保持，移除 HTTP /decide 端点 |

## 数据流

### 1. 微信消息 → NB2

```python
# main.py 收到微信消息（type 11046）
event_data = {
    "message_id": msgid,
    "user_id": from_wxid,
    "chat_id": chat_id,
    "sender_id": from_wxid,
    "text": msg,
    "event_type": "group_message",  # 或 "private_message"
}

# 转为 OneBot11 格式，通过 WS 发送给 NB2
ws_message = {
    "action": "send_msg",
    "params": {
        "message_type": "group" if is_group else "private",
        "group_id": chat_id if is_group else None,
        "user_id": from_wxid if not is_group else None,
        "message": msg,
    },
    "echo": msgid,
}
```

### 2. NB2 回复 → 微信

NB2 通过 WS 发送 OneBot11 响应，main.py 接收后解析：

```python
# NB2 回复格式
{
    "action": "send_msg",
    "retcode": 0,
    "data": {...},
    "echo": msgid,
}
```

main.py 从 `data` 中提取消息内容，调用 `helper_send_text` 发送。

## WebSocket Server 实现（main.py）

```python
# 新增 WebSocket server 组件
import websockets
import asyncio

class NoneBotWsServer:
    def __init__(self, host="127.0.0.1", port=18765):
        self.host = host
        self.port = port
        self.clients = set()
        self._running = False

    async def start(self):
        """启动 WebSocket server"""
        self._running = True
        async with websockets.serve(self._handle_client, self.host, self.port):
            logger.info(f"NoneBot WS Server 已启动 {self.host}:{self.port}")
            await asyncio.Future()  # 永久运行

    async def _handle_client(self, websocket):
        """处理 NB2 客户端连接"""
        self.clients.add(websocket)
        logger.info(f"NoneBot 已连接，当前客户端数: {len(self.clients)}")
        try:
            async for message in websocket:
                await self._handle_message(message)
        except Exception as e:
            logger.error(f"WS 客户端异常: {e}")
        finally:
            self.clients.remove(websocket)

    async def _handle_message(self, raw_message):
        """处理 NB2 发回的消息（如 send_msg 响应）"""
        import json
        try:
            data = json.loads(raw_message)
            # 解析 OneBot11 响应，提取实际消息内容
            action = data.get("action", "")
            if action == "send_msg" and data.get("retcode") == 0:
                msg_data = data.get("data", {})
                # 根据 message_type 决定发送目标
                # 调用 helper_send_text
        except Exception as e:
            logger.error(f"解析 NB2 响应失败: {e}")

    async def send_to_nb2(self, message: dict):
        """发送消息给 NB2"""
        if not self.clients:
            logger.warning("无 NB2 客户端连接")
            return
        import json
        for client in self.clients:
            try:
                await client.send(json.dumps(message))
            except Exception as e:
                logger.error(f"发送消息到 NB2 失败: {e}")

    def stop(self):
        self._running = False
```

## NB2 适配器配置

### nonebot2.toml

```toml
fastapi_host = "127.0.0.1"
fastapi_port = 8765

[adapter]
type = "onebot11"
reverse_ws_urls = ["ws://127.0.0.1:18765/onebot_ws"]
```

### 依赖安装

```bash
pip install nonebot-adapter-onebot11
```

## 错误处理

| 场景 | 处理 |
|------|------|
| NB2 未连接 | 日志警告，消息不转发 |
| WS 消息解析失败 | 记录错误，继续处理其他消息 |
| NB2 回复 retcode != 0 | 记录错误日志 |
| NB2 连接断开 | 自动重连（适配器自带） |

## 文件变更清单

| 操作 | 文件 |
|------|------|
| 修改 | `Py/main.py` — 新增 WebSocket server 组件 |
| 修改 | `ai_plugin/nonebot2.toml` — 新增 reverse_ws 配置 |
| 删除 | `ai_plugin/start_nonebot.py` — 不再需要 |
| 删除 | `ai_plugin/standalone_server.py` — 不再需要 |
| 修改 | `ai_plugin/__init__.py` — 移除 HTTP /decide 端点 |

## 启动顺序

1. 先启动 NoneBot2：`cd ai_plugin && nb run`
2. 再启动 main.py：`cd Py && python main.py`

NB2 的 onebot11 适配器会自动连接 main.py 的 WS server。

## 市场插件兼容性

所有 NoneBot2 市场插件通过 NB2 内部事件总线接收消息，与传输层无关。ai_plugin 和市场插件共存于 NB2 事件总线，互不干扰。
