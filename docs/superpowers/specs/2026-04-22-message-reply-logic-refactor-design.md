# 消息回复逻辑重构设计

## 背景

当前项目存在以下问题：
1. 两阶段决策架构混乱（BrainRuntime → FriendAdapter 重复判断）
2. 群聊消息被直接丢弃，无法收集上下文
3. 异常时返回固定文本，用户体验差
4. 历史检索用短文本搜索效果差
5. Fallback 搜索退化到时间倒序
6. 无规则引擎兜底

## 设计决策

| # | 问题 | 选择 |
|---|------|------|
| 1 | 架构 | 移除 BrainRuntime，一阶段决策 |
| 2 | 群聊策略 | 被@回复 + 潜伏模式收集上下文 |
| 3 | 异常处理 | 重试 + 友好错误 + 规则引擎兜底 |
| 4 | API 降级 | MiniMax → fallback 双 API |
| 5 | 历史检索 | 对话轮次 + 短文本改进 + 关键词 fallback |
| 6 | 规则引擎 | 完整规则引擎（意图识别+关键词+画像+上下文） |
| 7 | 用户画像 | 跨群聊共享 |
| 8 | NoneBot 集成 | 独立服务 + NoneBot 插件两种模式 |
| 9 | 消息入口 | Matcher 监听 |

## 新消息流程

```
微信消息
    ↓
HokAdapter.normalize() → EventEnvelope
    ↓
FriendAdapter.decide() 【统一入口】
    ├── 命令检测 (/开头) → NoneBotBridge → 命令处理
    ├── 自消息检测 → 跳过
    ├── 群聊未@ → 存入上下文，不回复
    ├── 群聊被@ / 私聊 → AI 回复流程
    │       ├── MiniMax API
    │       ├── 失败 → Fallback API
    │       ├── 失败 → 规则引擎兜底
    │       └── 失败 → 友好错误
    ↓
HokSendBridge.execute() → 发送到微信
```

## 核心模块变更

### 1. 删除 BrainRuntime

删除 `hok_brain/brain/runtime.py` 和 `hok_brain/brain/decision.py`。

所有决策逻辑集中在 `FriendAdapter.decide()`。

### 2. FriendAdapter.decide() 职责扩展

```python
async def decide(self, event: EventEnvelope) -> ReplyAction:
    # 1. 命令检测
    if event.text and event.text.strip().startswith('/'):
        return await self._handle_command(event)

    # 2. 自消息过滤
    if event.sender_id == self.self_user_id:
        return ReplyAction(decision="silent", ...)

    # 3. 群聊策略
    is_private = event.chat_id == event.sender_id
    is_mentioned = bool(event.mentioned_user_ids) or "@姜小妹" in (event.text or "")

    if not is_private and not is_mentioned:
        # 潜伏模式：存入上下文，不回复
        self.store_conversation(event)
        return ReplyAction(decision="silent", ...)

    # 4. AI 回复流程
    return await self._generate_reply(event)
```

### 3. AI 回复流程（重试 + 降级）

```python
async def _generate_reply(self, event: EventEnvelope) -> ReplyAction:
    # 获取上下文
    user_profile = self.get_user_profile(event.sender_id)
    conversation_history = self._get_conversation_turns(event)
    relevant_history = self._search_relevant(event)

    # 尝试 MiniMax
    try:
        reply = await self.ai_client.chat(...)
        self.store_conversation(event, reply)
        return ReplyAction(decision="reply", kind="text", text=reply, ...)
    except Exception as e:
        logger.warning(f"MiniMax failed: {e}")

    # 尝试 Fallback
    if self.fallback_client:
        try:
            reply = await self.fallback_client.chat(...)
            self.store_conversation(event, reply)
            return ReplyAction(decision="reply", kind="text", text=reply, ...)
        except Exception as e:
            logger.warning(f"Fallback failed: {e}")

    # 规则引擎兜底
    reply = self.rule_engine.generate(event, user_profile, conversation_history)
    if reply:
        self.store_conversation(event, reply)
        return ReplyAction(decision="reply", kind="text", text=reply, ...)

    # 最终兜底：友好错误
    return ReplyAction(
        decision="reply",
        kind="text",
        text="抱歉，我现在有点忙，稍后再回复你~",
        reasons=["fallback_error"],
        confidence=0.3,
    )
```

### 4. 历史检索改进

#### 4.1 对话轮次检索

```python
def _get_conversation_turns(self, event: EventEnvelope, limit: int = 6) -> list[dict]:
    """获取最近 N 轮对话（用户+AI 交替）"""
    history = self.embedding_store.get_conversations(
        user_id=event.sender_id,
        group_id=event.chat_id if event.chat_id != event.sender_id else None,
        limit=limit * 2  # 获取双倍，筛选交替
    )
    # 确保是用户和 AI 交替的完整轮次
    turns = []
    for msg in reversed(history):
        if not turns and msg["role"] != "user":
            continue  # 从用户消息开始
        turns.append(msg)
        if len(turns) >= limit * 2:
            break
    return turns[-limit:] if len(turns) >= limit else turns
```

#### 4.2 短文本改进

```python
def _search_relevant(self, event: EventEnvelope) -> list[dict]:
    """改进的检索：对短文本结合上下文"""
    query = event.text or ""

    # 短文本：结合用户画像扩展
    if len(query) < 10 and event.sender_id:
        profile = self.get_user_profile(event.sender_id)
        if profile:
            # 用画像关键词扩展查询
            extra = " ".join([
                " ".join(profile.get("likes", [])),
                " ".join(profile.get("traits", [])),
            ])
            query = f"{query} {extra}" if extra else query

    results = self.embedding_store.search_conversations(
        query=query,
        user_id=event.sender_id,
        group_id=event.chat_id if event.chat_id != event.sender_id else None,
        limit=5
    )
    return results
```

#### 4.3 关键词 fallback

```python
def _keyword_search(self, event: EventEnvelope) -> list[dict]:
    """当 embedding 不可用时的关键词匹配"""
    if not event.text:
        return []

    query_words = set(event.text.lower().split())

    history = self.embedding_store.get_conversations(
        user_id=event.sender_id,
        limit=50
    )

    scored = []
    for msg in history:
        content_words = set(msg["content"].lower().split())
        # Jaccard 相似度
        intersection = query_words & content_words
        if intersection:
            score = len(intersection) / max(len(query_words | content_words), 1)
            scored.append((score, msg))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [msg for _, msg in scored[:5]]
```

### 5. 规则引擎

```python
class RuleEngine:
    """完整规则引擎：意图识别 + 关键词 + 用户画像 + 上下文"""

    # 意图关键词映射
    INTENT_PATTERNS = {
        "greeting": ["hi", "你好", "在吗", "在干嘛", "早上好", "晚安"],
        "question": ["是什么", "为什么", "怎么", "如何", "?", "？"],
        "emotion": ["好累", "好开心", "难过", "生气", "郁闷"],
        "food": ["吃什么", "饿", "美食", "做饭", "外卖"],
        "weather": ["天气", "下雨", "冷", "热", "温度"],
    }

    # 固定回复规则
    REPLY_RULES = [
        {"keywords": ["在干嘛"], "reply": "没干嘛啊 咋了", "context": "idle"},
        {"keywords": ["你好", "hi", "嗨"], "reply": "嗨", "context": "greeting"},
        {"keywords": ["在吗"], "reply": "在的 啥事", "context": "greeting"},
        {"keywords": ["好累"], "reply": "又加班？", "context": "emotion"},
        {"keywords": ["吃什么", "饿"], "reply": "你想吃啥", "context": "food"},
        {"keywords": ["哈哈哈", "笑死"], "reply": "笑啥", "context": "neutral"},
    ]

    def generate(self, event, user_profile, conversation_history) -> str | None:
        # 1. 意图识别
        intent = self._recognize_intent(event.text)

        # 2. 关键词匹配
        for rule in self.REPLY_RULES:
            if any(kw in event.text.lower() for kw in rule["keywords"]):
                return self._apply_rule(rule, user_profile, conversation_history)

        # 3. 基于画像的个性化回复
        if user_profile:
            personalized = self._personalized_reply(intent, user_profile)
            if personalized:
                return personalized

        # 4. 上下文续接
        if conversation_history:
            last_msg = conversation_history[-1]
            return self._context_continuation(last_msg, event.text)

        return None

    def _recognize_intent(self, text: str) -> str | None:
        """识别意图"""
        text_lower = text.lower()
        for intent, keywords in self.INTENT_PATTERNS.items():
            if any(kw in text_lower for kw in keywords):
                return intent
        return None

    def _apply_rule(self, rule, user_profile, history) -> str:
        """应用规则，可根据画像调整"""
        reply = rule["reply"]

        # 如果用户画像有说话风格，应用
        if user_profile and user_profile.get("speaking_style"):
            # 简单处理：保持原回复
            pass

        return reply

    def _personalized_reply(self, intent, user_profile) -> str | None:
        """基于画像的个性化回复"""
        if not intent:
            return None

        # 根据用户喜好调整
        likes = user_profile.get("likes", [])
        if intent == "food" and "火锅" in likes:
            return "火锅！绝对的火锅"
        if intent == "emotion" and "游戏" in likes:
            return "打游戏放松一下？"

        return None

    def _context_continuation(self, last_msg, current_text) -> str | None:
        """基于上下文的回复续接"""
        if not last_msg:
            return None

        # 如果上条是 AI 说的，用户没回复就继续
        if last_msg.get("role") == "assistant":
            # 检查是否是问句
            if "？" in last_msg["content"] or "?" in last_msg["content"]:
                return "还没想好"  # 简单续接

        return None
```

### 6. 双运行模式

#### 6.1 独立服务模式（当前）

```python
# run_hok_brain_live.py
class LiveBrainHandler:
    def handle_raw(self, raw: dict) -> None:
        event = self.adapter.normalize(raw)
        action = asyncio.run(self.brain_client.decide(event))
        # 发送...
```

#### 6.2 NoneBot 插件模式

```python
# nonebot_plugin_hok_brain/
# __init__.py
from nonebot import on_message, on_command
from nonebot.adapters.onebot.v11 import MessageEvent

# Matcher 监听
hok_matcher = on_message()

@hok_matcher.handle()
async def handle_hok(event: MessageEvent):
    # 转发给主服务
    result = await http_post("http://127.0.0.1:8766/decide", {
        "message_id": str(event.message_id),
        "user_id": str(event.user_id),
        "chat_id": str(event.group_id) if event.group_id else str(event.user_id),
        "text": event.get_plaintext(),
    })

    if result.get("decision") == "reply":
        await hok_matcher.finish(result.get("text"))

# 命令处理
login_cmd = on_command("登录")
@login_cmd.handle()
async def handle_login(event: MessageEvent):
    result = await http_post("http://127.0.0.1:8766/command", {
        "command": "登录",
        "user_id": str(event.user_id),
    })
    await login_cmd.finish(result.get("message"))
```

## 文件变更清单

| 操作 | 文件 |
|------|------|
| 删除 | `hok_brain/brain/runtime.py` |
| 删除 | `hok_brain/brain/decision.py` |
| 修改 | `hok_brain/adapters/friend_adapter.py` |
| 修改 | `hok_brain/memory/embedding_store.py` |
| 新增 | `hok_brain/brain/rule_engine.py` |
| 新增 | `nonebot_plugin_hok_brain/__init__.py` |

## API 端点（独立服务模式）

| 端点 | 方法 | 描述 |
|------|------|------|
| `/decide` | POST | 消息决策入口 |
| `/command` | POST | 命令执行 |
| `/health` | GET | 健康检查 |

## 错误处理

| 场景 | 处理 |
|------|------|
| MiniMax API 失败 | 尝试 Fallback API |
| Fallback API 失败 | 规则引擎兜底 |
| 规则引擎无匹配 | 返回友好错误 |
| Embedding API 失败 | 关键词匹配 fallback |
| 网络超时 | 重试 2 次，间隔 1s |

## 友好错误消息

```python
FRIENDLY_ERRORS = {
    "api_timeout": "网络有点慢，再试一下下？",
    "api_error": "服务好像有点问题，稍等一下~",
    "rate_limit": "发太快了，慢一慢~",
    "unknown": "我现在有点忙，稍后再回复你~",
}
```
