"""
AI Handler - 处理 AI 聊天逻辑
"""
import re
import sys
from pathlib import Path
from typing import Optional, Union

lib_path = Path(__file__).parent.parent.parent / "lib"
if str(lib_path) not in sys.path:
    sys.path.insert(0, str(lib_path))

from hok_brain import MiniMaxClient, ContextBuilder, EmbeddingStore, ProfileExtractor, RuleEngine
from nonebot import logger
from . import runtime
from .tools import TOOL_PROMPT, ToolResult, parse_tool_calls, strip_tool_calls, execute_tool


def strip_thinking(text: str) -> str:
    """移除 AI 回复中的 <think>...</think> 标签及其内容"""
    return re.sub(r"<think>[\s\S]*?</think>\s*", "", text).strip()


def clean_reply(text: str) -> str:
    """清洗 AI 回复：去 emoji、去 AI 套话"""
    # 去除 emoji（精确范围，避免误删中文）
    emoji_pattern = re.compile(
        "["
        "\U0001F600-\U0001F64F"  # emoticons
        "\U0001F300-\U0001F5FF"  # symbols & pictographs
        "\U0001F680-\U0001F6FF"  # transport & map
        "\U0001F900-\U0001F9FF"  # supplemental symbols
        "\U0001FA00-\U0001FA6F"  # chess symbols
        "\U0001FA70-\U0001FAFF"  # symbols extended-A
        "\U00002702-\U000027B0"  # dingbats
        "\U0000FE00-\U0000FE0F"  # variation selectors
        "\U0000200D"             # zero width joiner
        "\U00002600-\U000026FF"  # misc symbols
        "\U00002700-\U000027BF"  # dingbats
        "]+",
        flags=re.UNICODE,
    )
    text = emoji_pattern.sub("", text)
    # 去掉"作为AI助手/模型"之类的开头套话
    text = re.sub(r"^(作为.{0,10}(助手|AI|模型|人工智能)[，,]?\s*)", "", text).strip()
    # 多个换行合并为一个空格（微信聊天不需要段落）
    text = re.sub(r"\n{2,}", " ", text)
    text = re.sub(r"\n", " ", text)
    return text


class AIHandler:
    """AI 聊天处理器"""

    MODELS = ("minimax", "gk", "ds")

    def __init__(self, config):
        self.config = config
        self.self_user_id = config.self_user_id

        db_path = str(Path(config.data_dir) / "embeddings.db")
        self.embedding_store = EmbeddingStore(
            db_path=db_path,
            api_key=config.minimax_api_key or None
        )
        self.context_builder = ContextBuilder(self.embedding_store)
        self.rule_engine = RuleEngine()
        self.profile_extractor = ProfileExtractor()

        # 模型客户端
        self._clients = {
            "minimax": MiniMaxClient(
                api_key=config.minimax_api_key,
                base_url=config.minimax_api_base,
                model=config.minimax_api_model
            ),
            "gk": MiniMaxClient(
                api_key=config.fallback_api_key,
                base_url=config.fallback_api_base,
                model=config.fallback_api_model
            ) if config.fallback_api_key else None,
            "ds": MiniMaxClient(
                api_key=config.deepseek_api_key,
                base_url=config.deepseek_api_base,
                model=config.deepseek_api_model
            ) if config.deepseek_api_key else None,
        }
        self.current_model = "minimax"

    @property
    def active_client(self):
        return self._clients.get(self.current_model) or self._clients["minimax"]

    def switch_model(self, name: str) -> str:
        """切换模型，返回当前模型名"""
        name = name.lower().strip()
        if name in ("gk", "grok"):
            name = "gk"
        elif name in ("ds", "deepseek"):
            name = "ds"
        elif name in ("minimax", "mm"):
            name = "minimax"
        if name in self._clients and self._clients[name] is not None:
            self.current_model = name
            logger.info(f"[MODEL] 切换到 {name}")
            return f"已切换到 {name}"
        return f"模型 {name} 不可用，当前: {self.current_model}"

    def handle_admin_command(self, text: str) -> Optional[str]:
        """处理管理员指令，返回回复文本；非指令返回 None"""
        if text.startswith("\\切换模型"):
            target = text[5:].strip()
            return self.switch_model(target) if target else f"当前模型: {self.current_model}"
        return None

    async def generate_reply(
        self,
        user_id: str,
        text: str,
        chat_id: str,
        is_group: bool = False,
        self_user_id: str = None,
        quoted_text: str = None,
        extra_context: dict = None,
    ) -> Optional[Union[str, ToolResult]]:
        try:
            return await self._do_reply(user_id, text, chat_id, is_group, self_user_id, quoted_text, extra_context)
        except Exception as e:
            logger.exception(f"[AI REPLY] 异常: {e}")
            return "抱歉，我现在有点忙，稍后再回复你~"

    async def _do_reply(
        self,
        user_id: str,
        text: str,
        chat_id: str,
        is_group: bool,
        self_user_id: str = None,
        quoted_text: str = None,
        extra_context: dict = None,
    ) -> Optional[Union[str, ToolResult]]:
        if not text.strip():
            return None

        extra = extra_context or {}
        sender_nickname = extra.get("sender_nickname", "")

        if quoted_text:
            text = f"[用户引用的消息]: {quoted_text}\n[用户的回复]: {text}"

        # 群聊时给消息加上昵称前缀，让 AI 知道是谁在说话
        if is_group and sender_nickname:
            text = f"[{sender_nickname}]: {text}"

        # 分离原始查询（用于 embedding 检索）和完整文本（发给 AI）
        search_marker = "\n\n【联网搜索结果】"
        search_idx = text.find(search_marker)
        query_for_embedding = text[:search_idx].strip() if search_idx > 0 else text

        user_profile = self.embedding_store.get_user_profile(user_id)
        conversation_history = self.embedding_store.get_conversation_turns(
            user_id=user_id,
            group_id=chat_id if is_group else None,
            limit=8
        )
        relevant_history = await self.embedding_store.search_conversations(
            query=query_for_embedding,
            user_id=user_id,
            group_id=chat_id if is_group else None,
            limit=5,
            user_profile=user_profile,
        )

        # 群聊：拉取其他人的最近消息，让 AI 理解多人对话上下文
        group_context = []
        if is_group:
            group_context = self.embedding_store.get_group_recent_messages(
                group_id=chat_id,
                limit=6,
                exclude_user_id=user_id,
            )

        # 过滤低相似度结果（阈值 0.3），避免无关对话污染上下文
        MIN_SIMILARITY = 0.3
        relevant_history = [h for h in relevant_history if h.get("similarity", 0) >= MIN_SIMILARITY]

        # 合并并去重，保持时间顺序（旧→新）
        # 优先级：近期对话 > 群聊上下文 > 语义相关历史
        seen_ids = set()
        merged = []
        # 1. 近期对话（必须保留，不可被截断）
        for turn in conversation_history:
            if turn["id"] not in seen_ids:
                merged.append(turn)
                seen_ids.add(turn["id"])
        # 2. 群聊其他人的消息
        for msg in group_context:
            if msg["id"] not in seen_ids:
                merged.append(msg)
                seen_ids.add(msg["id"])
        # 3. 语义相关的长期记忆
        for h in relevant_history:
            if h["id"] not in seen_ids:
                merged.append(h)
                seen_ids.add(h["id"])
        # 按时间排序确保 AI 看到正确的对话顺序
        merged.sort(key=lambda x: x.get("timestamp", ""))
        # 上限 16 条（近期对话不会被截断，因为它们时间最新）
        merged_history = merged[-16:]

        # RAG 检索风格语料（仅闲聊场景注入，技术问题不注入避免干扰）
        style_examples = ""
        try:
            style_results = await self.embedding_store.search_style_corpus(query_for_embedding, limit=3)
            # 质量门控：只保留相似度 >= 0.4 的语料，避免低质量内容污染风格
            style_results = [r for r in style_results if r.get("similarity", 0) >= 0.4]
            if style_results:
                style_examples = "\n".join(f"- {r['content']}" for r in style_results)
        except Exception as e:
            logger.debug(f"[STYLE] 检索风格语料失败: {e}")

        system_prompt = self.context_builder.build_system_prompt(
            user_profile=user_profile,
            topic=None,
            extra_prompt=TOOL_PROMPT,
            style_examples=style_examples,
            admin_wxid=self.config.admin_wxid,
            user_id=user_id,
        )

        # 主模型（history 上限放宽到 16）
        error_msg = None
        client = self.active_client
        try:
            reply = await client.chat(
                message=text,
                conversation_history=merged_history,
                system_prompt=system_prompt,
            )
            reply = clean_reply(strip_thinking(reply))
            logger.info(f"[AI REPLY] {self.current_model}: {reply[:50]}...")
            await self._store(user_id, text, chat_id, is_group, reply)
            return await self._process_tools(reply, extra_context)
        except Exception as e:
            error_msg = str(e)
            logger.warning(f"[AI REPLY] {self.current_model} failed: {error_msg[:80]}")

        # 尝试其他模型作为 fallback
        for name, fb_client in self._clients.items():
            if name == self.current_model or fb_client is None:
                continue
            try:
                reply = await fb_client.chat(
                    message=text,
                    conversation_history=merged_history,
                    system_prompt=system_prompt,
                )
                reply = clean_reply(strip_thinking(reply))
                logger.info(f"[AI REPLY] fallback {name}: {reply[:50]}...")
                await self._store(user_id, text, chat_id, is_group, reply)
                return await self._process_tools(reply, extra_context)
            except Exception as e:
                logger.warning(f"[AI REPLY] fallback {name} failed: {str(e)[:80]}")

        # RuleEngine 兜底
        rule_reply = self.rule_engine.generate(
            text=text,
            user_profile=user_profile,
            conversation_history=conversation_history,
            error_type=self._classify_error(error_msg),
        )
        if rule_reply:
            logger.info(f"[AI REPLY] RuleEngine: {rule_reply}")
            await self._store(user_id, text, chat_id, is_group, rule_reply)
            return rule_reply

        return "抱歉，我现在有点忙，稍后再回复你~"

    async def _process_tools(self, reply: str, extra_context: dict = None) -> Union[str, ToolResult]:
        """解析 AI 回复中的工具调用标记并执行"""
        calls = parse_tool_calls(reply)
        if not calls:
            return reply

        clean_text = strip_tool_calls(reply)
        context = extra_context or {}
        all_results: list[ToolResult] = []

        for name, args in calls:
            logger.info(f"[TOOL] 调用 {name}({args})")
            result = await execute_tool(name, args, context)
            all_results.append(result)

        # 合并文本：AI 的自然语言 + 所有工具结果
        text_parts = [clean_text] if clean_text else []
        media_all = []
        media_type = ""
        for r in all_results:
            if r.text:
                text_parts.append(r.text)
            if r.media:
                media_all.extend(r.media)
                media_type = r.media_type

        combined_text = "\n\n".join(text_parts) if text_parts else ""
        if media_all:
            return ToolResult(text=combined_text, media=media_all, media_type=media_type)
        return combined_text

    async def _store(self, user_id: str, text: str, chat_id: str, is_group: bool, reply: str):
        group_id = chat_id if is_group else None
        await self.embedding_store.add_conversation(user_id=user_id, group_id=group_id, role="user", content=text)
        await self.embedding_store.add_conversation(user_id=user_id, group_id=group_id, role="assistant", content=reply)

        # LLM 画像提取：缓存消息，每 10 条触发一次
        self.profile_extractor.buffer_message(user_id, text)
        if self.profile_extractor.should_extract(user_id):
            buffered = self.profile_extractor.get_buffered_text(user_id)
            try:
                extracted = await self.profile_extractor.extract_with_llm(buffered)
                if extracted and any(extracted.values()):
                    existing = self.embedding_store.get_user_profile(user_id)
                    merged = self.profile_extractor.merge_profiles(existing, extracted)
                    await self.embedding_store.set_user_profile(user_id, **merged)
                    logger.debug(f"[PROFILE] 更新画像 {user_id}: {merged}")
            except Exception as e:
                logger.debug(f"[PROFILE] 画像提取失败 {user_id}: {e}")

    def _classify_error(self, error_msg: str) -> str:
        if not error_msg:
            return "unknown"
        el = error_msg.lower()
        if "timeout" in el or "timed out" in el:
            return "api_timeout"
        if "429" in el or "rate" in el:
            return "rate_limit"
        if "500" in el or "502" in el or "503" in el:
            return "api_error"
        return "unknown"
