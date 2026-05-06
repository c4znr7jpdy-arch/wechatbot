# 消息回复逻辑重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 NoneBot 集成问题，实现 hok_brain 与微信服务的正确集成

**Architecture:**
- `lib/hok_brain/` - 核心AI库（已存在，AIHandler已在ai_plugin中）
- `ai_plugin/` - NoneBot插件，作为子进程运行
- `Py/main.py` - 微信服务，启动时拉起NoneBot子进程，通过HTTP API通信

**启动流程（集成后）:**
```bash
# 只需启动微信服务，NoneBot自动跟随
cd E:/Project/Py
python main.py
```

**Tech Stack:** Python asyncio, SQLite, MiniMax API, NoneBot, httpx, subprocess

---

## 阶段零：诊断与架构确认

### Task 0: 确认当前代码状态

**Files:**
- Read: `ai_plugin/ai_plugin/handler.py`
- Read: `lib/hok_brain/brain/reply_context.py`
- Read: `lib/hok_brain/brain/rule_engine.py`
- Read: `lib/hok_brain/memory/embedding_store.py`

- [ ] **Step 1: 确认 ReplyContext 和 RuleEngine 已存在**

Run: `ls -la lib/hok_brain/brain/`
Expected: `reply_context.py` 和 `rule_engine.py` 都存在

- [ ] **Step 2: 确认 AIHandler 已实现核心逻辑**

Run: `grep -n "def generate_reply" ai_plugin/ai_plugin/handler.py`
Expected: 找到 `generate_reply` 方法

- [ ] **Step 3: 确认 NoneBot 配置**

Run: `cat ai_plugin/pyproject.toml`
Expected: 包含 `[tool.nonebot]` 配置

---

## 阶段一：修复 NoneBot 集成问题

### Task 1: Py/main.py 启动时拉起 NoneBot 子进程

**问题分析:**
```
Py/main.py 收到微信消息 (类型 11046, /登录)
    ↓
尝试 import ai_plugin  ❌ 失败 - NoneBot未运行
    ↓
处理聊天消息失败: No module named 'ai_plugin'
```

**解决方案:** Py/main.py 使用 subprocess.Popen 启动 NoneBot 作为子进程，通过 HTTP API 通信。

**Files:**
- Modify: `Py/main.py` - 添加 NoneBot 子进程管理

- [ ] **Step 1: 在 Py/main.py 顶部添加 NoneBot 子进程管理**

在 `Py/main.py` 的 `get_ai_handler()` 函数附近添加：

```python
# ============================ NoneBot 子进程管理 ============================

import subprocess
import threading
import time
import httpx
import json

_nonebot_process = None
_nonebot_ready = threading.Event()

def start_nonebot_subprocess():
    """启动 NoneBot 子进程"""
    global _nonebot_process, _nonebot_ready

    if _nonebot_process is not None:
        return  # 已启动

    nonebot_path = Path(__file__).parent.parent / "ai_plugin"
    if not nonebot_path.exists():
        logger.warning(f"NoneBot path not found: {nonebot_path}")
        return

    logger.info("Starting NoneBot subprocess...")

    _nonebot_process = subprocess.Popen(
        [sys.executable, "-m", "nonebot", "run", "--host", "127.0.0.1", "--port", "8765"],
        cwd=str(nonebot_path),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    # 启动日志读取线程
    def read_logs():
        if _nonebot_process.stdout:
            for line in _nonebot_process.stdout:
                if "Uvicorn running on" in line or "Application startup complete" in line:
                    _nonebot_ready.set()
                    logger.info("NoneBot subprocess ready")
                    break

    threading.Thread(target=read_logs, daemon=True).start()

    # 等待最多30秒
    if not _nonebot_ready.wait(timeout=30):
        logger.warning("NoneBot subprocess may not be ready yet")

def stop_nonebot_subprocess():
    """停止 NoneBot 子进程"""
    global _nonebot_process

    if _nonebot_process:
        logger.info("Stopping NoneBot subprocess...")
        _nonebot_process.terminate()
        _nonebot_process.wait(timeout=10)
        _nonebot_process = None
        _nonebot_ready.clear()

def call_nonebot_api(endpoint: str, data: dict, timeout: float = 10.0) -> dict:
    """调用 NoneBot HTTP API"""
    if not _nonebot_ready.is_set():
        logger.warning("NoneBot not ready, API call skipped")
        return {}

    try:
        response = httpx.post(
            f"http://127.0.0.1:8765{endpoint}",
            json=data,
            timeout=timeout
        )
        if response.status_code == 200:
            return response.json()
        logger.warning(f"NoneBot API {endpoint} returned {response.status_code}")
        return {}
    except Exception as e:
        logger.error(f"NoneBot API call failed: {e}")
        return {}
```

- [ ] **Step 2: 在 main() 函数启动时调用 start_nonebot_subprocess()**

找到 `def main():` 函数，在初始化代码附近添加：

```python
def main():
    # ... 现有的初始化代码 ...

    # 启动 NoneBot 子进程
    start_nonebot_subprocess()

    # 注册退出时清理
    atexit.register(stop_nonebot_subprocess)

    # ... 继续启动微信服务 ...
```

- [ ] **Step 3: 验证语法**

Run: `python -m py_compile Py/main.py`
Expected: 无输出（成功）

- [ ] **Step 4: 提交**

```bash
git add Py/main.py
git commit -m "feat: auto-start NoneBot subprocess when WeChat service starts"
```

---

### Task 2: NoneBot HTTP API 端点

**Files:**
- Modify: `ai_plugin/ai_plugin/__init__.py` - 添加 HTTP API 端点

- [ ] **Step 1: 添加 HTTP API 端点用于接收消息**

Note: NoneBot 使用 onebot adapter，需要通过 nonebot.internal.driver 模块添加 HTTP 路由。

```python
# 在 ai_plugin/ai_plugin/__init__.py 末尾添加

# HTTP API 端点（用于被 Py/main.py 调用）
from nonebot import on_request
from nonebot.internal.driver import ReverseDriver

@on_request.handle()
async def handle_http_request(request):
    """处理来自 Py/main.py 的 HTTP 请求"""
    if request.url.path == "/decide":
        data = request.json()
        user_id = data.get("user_id", "")
        text = data.get("text", "")
        chat_id = data.get("chat_id", "")
        is_group = data.get("event_type") == "group_message"

        reply = await ai_handler.generate_reply(
            user_id=user_id,
            text=text,
            chat_id=chat_id,
            is_group=is_group,
        )

        return {"decision": "reply" if reply else "silent", "text": reply or ""}
```

**注意:** 上述代码是概念验证。实际实现需要检查 NoneBot 版本和适配器配置。

- [ ] **Step 2: 验证 NoneBot 可以启动**

Run: `cd ai_plugin && python -m nonebot run --help 2>&1 | head -10`
Expected: 显示帮助信息

- [ ] **Step 3: 提交**

```bash
git add ai_plugin/ai_plugin/__init__.py
git commit -m "feat: add HTTP API endpoint for Py/main.py integration"
```

---

### Task 3: 修改 Py/main.py 消息处理使用 HTTP API

**Files:**
- Modify: `Py/main.py` - 修改消息处理逻辑调用 HTTP API

- [ ] **Step 1: 在 handle_raw 中添加 HTTP API 调用**

找到处理消息类型 11046 的代码块，修改为：

```python
# 处理聊天消息 (11046)
if msg_type == 11046:
    try:
        # 构造事件数据
        event_data = {
            "message_id": data.get("msgid", ""),
            "user_id": data.get("from_wxid", ""),
            "chat_id": data.get("room_wxid") or data.get("to_wxid", ""),
            "sender_id": data.get("from_wxid", ""),
            "text": data.get("msg", ""),
            "event_type": "group_message" if data.get("room_wxid") else "private_message",
        }

        # 调用 NoneBot API
        result = call_nonebot_api("/decide", event_data)

        if result and result.get("decision") == "reply":
            reply_text = result.get("text", "")
            if reply_text:
                self._send_text_message(data.get("from_wxid", ""), reply_text)

    except Exception as e:
        logger.error(f"处理聊天消息异常: {e}")
```

- [ ] **Step 2: 验证语法**

Run: `python -m py_compile Py/main.py`
Expected: 无输出（成功）

- [ ] **Step 3: 提交**

```bash
git add Py/main.py
git commit -m "fix: use HTTP API to call NoneBot for message handling"
```

---

## 阶段二：验证 ReplyContext 和 RuleEngine

### Task 4: 验证核心组件

**Files:**
- Read: `lib/hok_brain/brain/reply_context.py`
- Read: `lib/hok_brain/brain/rule_engine.py`

- [ ] **Step 1: 验证 ReplyContext.from_event() 正确实现**

```python
# 验证代码
import sys
sys.path.insert(0, "lib")

from hok_brain.brain.reply_context import ReplyContext
from hok_brain.schemas import EventEnvelope

# 创建测试事件（私聊）
event = EventEnvelope(
    platform="test",
    adapter="test",
    event_type="private_message",
    message_id="1",
    chat_id="user123",
    sender_id="user123",
    text="Hello",
    mentioned_user_ids=[],
    attachments=[],
    metadata={},
    raw={},
)

context = ReplyContext.from_event(event, "self_id")
print(f"should_reply: {context.should_reply}")
print(f"should_store_only: {context.should_store_only}")
```

- [ ] **Step 2: 验证 RuleEngine.generate() 正确实现**

```python
# 验证代码
from hok_brain.brain.rule_engine import RuleEngine

engine = RuleEngine()

# 测试关键词匹配
reply = engine.generate("你好")
print(f"'你好' -> '{reply}'")

# 测试友好错误
reply = engine.generate("test", error_type="api_timeout")
print(f"api_timeout error -> '{reply}'")
```

- [ ] **Step 3: 提交**

```bash
git add -A
git commit -m "test: verify ReplyContext and RuleEngine functionality"
```

---

## 阶段三：完整流程测试

### Task 5: 端到端测试

- [ ] **Step 1: 启动微信服务（NoneBot应自动跟随）**

```bash
cd E:/Project/Py
python main.py
```

Expected: 日志中应显示 "Starting NoneBot subprocess..." 和 "NoneBot subprocess ready"

- [ ] **Step 2: 发送测试消息**

在微信中发送 `你好`
Expected: 收到 AI 回复

- [ ] **Step 3: 提交**

```bash
git add -A
git commit -m "test: end-to-end integration test passed"
```

---

## 变更文件清单

| 文件 | 操作 | 描述 |
|------|------|------|
| `Py/main.py` | 修改 | NoneBot子进程管理、HTTP API调用 |
| `ai_plugin/ai_plugin/__init__.py` | 修改 | 添加HTTP API端点 |

---

## 故障排查

### 问题: "NoneBot subprocess may not be ready yet"

**原因:** NoneBot 启动超时

**解决:** 检查 NoneBot 日志，确认 8765 端口未被占用

### 问题: HTTP 连接失败

**原因:** NoneBot 未正确启动

**解决:**
1. `netstat -an | grep 8765` 检查端口
2. 查看 Py/main.py 日志中的 NoneBot 输出

---

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-04-22-message-reply-logic-refactor-plan.md`**

```python
# ai_plugin/ai_plugin/__init__.py
"""
ai_plugin - NoneBot AI Chat Plugin

接收微信消息，使用 AI 生成回复。
"""
import os
import sys
from pathlib import Path

# 添加 lib 目录到 Python 路径
lib_path = Path(__file__).parent.parent.parent / "lib"
if str(lib_path) not in sys.path:
    sys.path.insert(0, str(lib_path))

from nonebot import on_message, on_command, Bot, logger
from nonebot.adapters.onebot.v11 import MessageEvent, GroupMessageEvent, PrivateMessageEvent, MessageSegment
from nonebot.params import Command, ArgStr

from .config import Config
from .handler import AIHandler

# 全局配置
config = Config()
ai_handler = AIHandler(config)

# 命令处理
ai_cmd = on_command("ai")

@ai_cmd.handle()
async def handle_ai(bot: Bot, event: MessageEvent, cmd: str = Command(), args: str = ArgStr()):
    """AI 聊天命令"""
    if args:
        reply = await ai_handler.generate_reply(
            user_id=str(event.user_id),
            text=args,
            chat_id=str(event.group_id) if isinstance(event, GroupMessageEvent) else str(event.user_id),
            is_group=isinstance(event, GroupMessageEvent),
        )
    else:
        reply = "用法: /ai <消息>"
    await ai_cmd.finish(reply)

# 消息处理
ai_matcher = on_message()

@ai_matcher.handle()
async def handle_message(bot: Bot, event: MessageEvent):
    """处理消息"""
    text = event.get_plaintext().strip()
    if not text:
        await ai_matcher.finish()

    is_private = isinstance(event, PrivateMessageEvent)

    # 私聊直接处理
    if is_private:
        reply = await ai_handler.generate_reply(
            user_id=str(event.user_id),
            text=text,
            chat_id=str(event.user_id),
            is_group=False,
        )
        if reply:
            await ai_matcher.finish(reply)
    else:
        # 群聊：检查是否被@ 或者以 /ai 开头
        if text.startswith("/ai "):
            actual_text = text[4:].strip()
            if actual_text:
                reply = await ai_handler.generate_reply(
                    user_id=str(event.user_id),
                    text=actual_text,
                    chat_id=str(event.group_id),
                    is_group=True,
                )
                if reply:
                    await ai_matcher.finish(reply)
        # 其他群聊消息潜伏模式

    await ai_matcher.finish()

__nonebot_plugin_name__ = "ai_plugin"
```

- [ ] **Step 3: 验证 NoneBot 可以启动**

Run: `cd ai_plugin && python -m nonebot run --help 2>&1 | head -20`
Expected: 显示帮助信息

- [ ] **Step 4: 提交**

```bash
git add scripts/run_nonebot.ps1 ai_plugin/ai_plugin/__init__.py
git commit -m "fix: proper NoneBot plugin initialization"
```

---

### Task 2: 修改 Py/main.py 支持 HTTP API 调用

**问题:** Py/main.py 直接 import ai_plugin，但 ai_plugin 需要 NoneBot 运行

**解决方案:** Py/main.py 通过 HTTP 调用运行中的 NoneBot 服务

**Files:**
- Modify: `Py/main.py:30-50` - 添加 HTTP 客户端初始化
- Modify: `Py/main.py` - 修改消息处理逻辑调用 HTTP API

- [ ] **Step 1: 添加 HTTP 客户端到 Py/main.py**

在 `Py/main.py` 中添加：

```python
# ============================ HTTP API 客户端 ============================

import httpx
import json
import asyncio

_nonebot_api_base = "http://127.0.0.1:8765"

async def call_nonebot_api(endpoint: str, data: dict) -> dict:
    """调用 NoneBot API"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_nonebot_api_base}{endpoint}",
                json=data,
            )
            if resp.status_code == 200:
                return resp.json()
            logger.warning(f"NoneBot API {endpoint} returned {resp.status_code}")
            return {}
    except Exception as e:
        logger.error(f"NoneBot API call failed: {e}")
        return {}

def sync_call_nonebot(endpoint: str, data: dict) -> dict:
    """同步调用 NoneBot API"""
    try:
        import requests
        resp = requests.post(
            f"{_nonebot_api_base}{endpoint}",
            json=data,
            timeout=10
        )
        if resp.status_code == 200:
            return resp.json()
        return {}
    except Exception as e:
        logger.error(f"NoneBot API call failed (sync): {e}")
        return {}
```

- [ ] **Step 2: 修改 handle_raw 中的消息处理逻辑**

找到 `handle_raw` 方法（约 350-400 行），在处理消息类型 11046 的地方修改：

```python
# 原代码（需要修改的部分）:
def handle_raw(self, raw: dict) -> None:
    # ... 其他代码 ...

    # 处理聊天消息 (11046)
    if msg_type == 11046:
        try:
            from ai_plugin.config import Config
            from ai_plugin.handler import AIHandler
            handler = AIHandler(Config())
            # ... 使用 handler ...
        except ImportError as e:
            logger.error(f"处理聊天消息失败: {e}")
            return

# 修改后:
def handle_raw(self, raw: dict) -> None:
    # ... 其他代码 ...

    # 处理聊天消息 (11046)
    if msg_type == 11046:
        try:
            # 构造事件数据
            event_data = {
                "message_id": data.get("msgid", ""),
                "user_id": data.get("from_wxid", ""),
                "chat_id": data.get("room_wxid") or data.get("to_wxid", ""),
                "sender_id": data.get("from_wxid", ""),
                "text": data.get("msg", ""),
                "event_type": "group_message" if data.get("room_wxid") else "private_message",
            }

            # 尝试 HTTP 调用（推荐方式）
            result = sync_call_nonebot("/decide", event_data)
            if result and result.get("decision") == "reply":
                reply_text = result.get("text", "")
                if reply_text:
                    self._send_text_message(data.get("from_wxid", ""), reply_text)
                    return

            # 如果 NoneBot 未运行，尝试直接导入（仅用于开发调试）
            if not result:
                try:
                    import sys
                    from pathlib import Path
                    lib_path = Path(__file__).parent.parent / "lib"
                    if str(lib_path) not in sys.path:
                        sys.path.insert(0, str(lib_path))

                    from ai_plugin.config import Config
                    from ai_plugin.handler import AIHandler

                    # 同步运行异步代码（仅开发模式）
                    import asyncio
                    handler = AIHandler(Config())

                    # 判断是群聊还是私聊
                    is_group = bool(data.get("room_wxid"))
                    chat_id = data.get("room_wxid") or data.get("to_wxid", "")
                    user_id = data.get("from_wxid", "")

                    reply = asyncio.run(handler.generate_reply(
                        user_id=user_id,
                        text=data.get("msg", ""),
                        chat_id=chat_id,
                        is_group=is_group,
                    ))

                    if reply:
                        self._send_text_message(user_id, reply)
                except ImportError as e:
                    logger.error(f"ai_plugin 未安装: {e}")
                except Exception as e:
                    logger.error(f"AI 处理失败: {e}")

        except Exception as e:
            logger.error(f"处理聊天消息异常: {e}")
```

- [ ] **Step 3: 验证语法**

Run: `python -m py_compile Py/main.py`
Expected: 无输出（成功）

- [ ] **Step 4: 提交**

```bash
git add Py/main.py
git commit -m "fix: add HTTP API client for NoneBot integration"
```

---

## 阶段二：确认现有功能完整

### Task 3: 验证 ReplyContext 和 RuleEngine

**Files:**
- Read: `lib/hok_brain/brain/reply_context.py`
- Read: `lib/hok_brain/brain/rule_engine.py`

- [ ] **Step 1: 验证 ReplyContext.from_event() 正确实现**

```python
# 验证代码
from lib.hok_brain.brain.reply_context import ReplyContext
from lib.hok_brain.schemas import EventEnvelope

# 创建测试事件
event = EventEnvelope(
    platform="test",
    adapter="test",
    event_type="private_message",
    message_id="1",
    chat_id="user123",
    sender_id="user123",
    text="Hello",
    # ... 其他必需字段
)

context = ReplyContext.from_event(event, "self_id")
assert context.should_reply == True, "私聊应该回复"
assert context.should_store_only == False
print("ReplyContext 验证通过")
```

- [ ] **Step 2: 验证 RuleEngine.generate() 正确实现**

```python
# 验证代码
from lib.hok_brain.brain.rule_engine import RuleEngine

engine = RuleEngine()

# 测试关键词匹配
reply = engine.generate("你好")
assert reply is not None, "应该匹配到'你好'"
print(f"RuleEngine 测试: '你好' -> '{reply}'")

# 测试友好错误
reply = engine.generate("test", error_type="api_timeout")
assert reply == "网络有点慢，再试一下下？"
print(f"RuleEngine 错误处理: {reply}")

print("RuleEngine 验证通过")
```

- [ ] **Step 3: 提交**

```bash
git add -A
git commit -m "test: verify ReplyContext and RuleEngine"
```

---

## 阶段三：NoneBot 服务模式完善

### Task 4: 添加 NoneBot HTTP API 端点

NoneBot 需要暴露 HTTP API 供 Py/main.py 调用。

**Files:**
- Modify: `ai_plugin/ai_plugin/__init__.py` - 添加 API 路由

- [ ] **Step 1: 使用 nonebot 插件的 on HTTP 装饰器**

```python
# ai_plugin/ai_plugin/__init__.py

# 添加 HTTP API 路由
from nonebot import on_request, on_raw_http_request
import httpx

# 注意：NoneBot 的 adapter 需要支持 HTTP 路由
# 如果使用 onebot adapter，可以这样：

@on_request.handle()
async def handle_http_request(request):
    """处理 HTTP 请求"""
    if request.url.path == "/decide":
        data = request.json()
        user_id = data.get("user_id", "")
        text = data.get("text", "")
        chat_id = data.get("chat_id", "")
        is_group = data.get("event_type") == "group_message"

        reply = await ai_handler.generate_reply(
            user_id=user_id,
            text=text,
            chat_id=chat_id,
            is_group=is_group,
        )

        if reply:
            await request.send_json({"decision": "reply", "text": reply})
        else:
            await request.send_json({"decision": "silent"})
```

**注意:** NoneBot 的 HTTP 处理需要配置 `driver` 和 adapter。检查 `pyproject.toml` 配置。

- [ ] **Step 2: 验证 NoneBot 可以启动**

Run: `cd ai_plugin && python -m nonebot run 2>&1 | head -30`
Expected: NoneBot 启动成功

- [ ] **Step 3: 提交**

```bash
git add ai_plugin/ai_plugin/__init__.py
git commit -m "feat: add HTTP API endpoints to NoneBot plugin"
```

---

## 阶段四：完整流程测试

### Task 5: 端到端测试

- [ ] **Step 1: 启动 NoneBot**

Terminal 1:
```bash
cd E:/Project/ai_plugin
python -m nonebot run --host 127.0.0.1 --port 8765
```

- [ ] **Step 2: 测试 HTTP API**

Terminal 2:
```bash
curl -X POST http://127.0.0.1:8765/decide \
  -H "Content-Type: application/json" \
  -d '{"user_id":"test","text":"你好","chat_id":"test","event_type":"private_message"}'
```

Expected: 返回 `{"decision": "reply", "text": "..."}`

- [ ] **Step 3: 测试 Py/main.py 发送消息**

在微信中发送 `/登录` 或 `你好`
Expected: 收到 AI 回复

- [ ] **Step 4: 提交**

```bash
git add -A
git commit -m "test: end-to-end integration test passed"
```

---

## 变更文件清单

| 文件 | 操作 | 描述 |
|------|------|------|
| `scripts/run_nonebot.ps1` | 新增 | NoneBot 启动脚本 |
| `ai_plugin/ai_plugin/__init__.py` | 修改 | NoneBot 插件初始化 |
| `Py/main.py` | 修改 | 添加 HTTP API 客户端 |

---

## 启动流程（修复后）

```bash
# Terminal 1: 启动 NoneBot
cd E:/Project/ai_plugin
python -m nonebot run --host 127.0.0.1 --port 8765

# Terminal 2: 启动微信服务
cd E:/Project/Py
python main.py
```

---

## 故障排查

### 问题: "No module named 'ai_plugin'"

**原因:** NoneBot 未启动，或 Python 路径未包含 ai_plugin

**解决:**
1. 先启动 NoneBot: `cd ai_plugin && python -m nonebot run`
2. 确保 Py/main.py 的 sys.path 包含 ai_plugin 目录

### 问题: HTTP 连接失败

**原因:** NoneBot 未运行在 8765 端口

**解决:**
1. 检查 NoneBot 是否启动: `netstat -an | grep 8765`
2. 检查防火墙设置

---

## 执行选项

**Plan complete and saved to `docs/superpowers/plans/2026-04-22-message-reply-logic-refactor-plan.md`**

**Two execution options:**

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**
