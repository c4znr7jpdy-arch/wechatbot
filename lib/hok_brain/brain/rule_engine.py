from typing import Optional

class RuleEngine:
    """完整规则引擎：意图识别 + 关键词 + 用户画像 + 上下文"""

    # 意图关键词映射
    INTENT_PATTERNS = {
        "greeting": ["hi", "你好", "在吗", "在干嘛", "早上好", "晚安", "嗨"],
        "question": ["是什么", "为什么", "怎么", "如何", "?", "？"],
        "emotion_positive": ["好开心", "开心", "高兴", "爽"],
        "emotion_negative": ["好累", "难过", "生气", "郁闷", "烦"],
        "food": ["吃什么", "饿", "美食", "做饭", "外卖", "火锅"],
        "weather": ["天气", "下雨", "冷", "热", "温度"],
    }

    # 固定回复规则 (按优先级排序)
    REPLY_RULES = [
        {"keywords": ["在干嘛"], "reply": "没干嘛啊 咋了"},
        {"keywords": ["你好", "hi", "嗨"], "reply": "嗨"},
        {"keywords": ["在吗"], "reply": "在的 啥事"},
        {"keywords": ["好累"], "reply": "又加班？"},
        {"keywords": ["好开心", "开心"], "reply": "咋了 发生啥好事了"},
        {"keywords": ["难过"], "reply": "咋了 说说是"},
        {"keywords": ["吃什么", "饿"], "reply": "你想吃啥"},
        {"keywords": ["天气"], "reply": "今天咋样"},
        {"keywords": ["哈哈哈", "笑死", "笑死我了"], "reply": "笑啥"},
    ]

    # 友好错误消息
    FRIENDLY_ERRORS = {
        "api_timeout": "网络有点慢，再试一下下？",
        "api_error": "服务好像有点问题，稍等一下~",
        "rate_limit": "发太快了，慢一慢~",
        "unknown": "我现在有点忙，稍后再回复你~",
    }

    def generate(
        self,
        text: str,
        user_profile: dict = None,
        conversation_history: list[dict] = None,
        error_type: str = None
    ) -> Optional[str]:
        """生成规则回复"""
        # 如果是 API 错误，返回友好错误
        if error_type and error_type in self.FRIENDLY_ERRORS:
            return self.FRIENDLY_ERRORS[error_type]

        if not text:
            return None

        text_lower = text.lower()

        # 1. 关键词直接匹配
        for rule in self.REPLY_RULES:
            if any(kw in text_lower for kw in rule["keywords"]):
                return self._apply_rule(rule, user_profile, conversation_history)

        # 2. 意图识别匹配
        intent = self._recognize_intent(text_lower)
        if intent:
            reply = self._reply_by_intent(intent, user_profile)
            if reply:
                return reply

        # 3. 上下文续接
        if conversation_history:
            reply = self._context_continuation(conversation_history, text_lower)
            if reply:
                return reply

        return None

    def _recognize_intent(self, text: str) -> Optional[str]:
        """识别意图"""
        for intent, keywords in self.INTENT_PATTERNS.items():
            if any(kw in text for kw in keywords):
                return intent
        return None

    def _reply_by_intent(self, intent: str, user_profile: dict = None) -> Optional[str]:
        """根据意图回复"""
        # 基于画像的个性化
        if user_profile:
            likes = user_profile.get("likes", [])
            traits = user_profile.get("traits", [])

            if intent == "food" and "火锅" in likes:
                return "火锅！绝对的火锅"
            if intent == "emotion_negative" and "游戏" in likes:
                return "打游戏放松一下？"

        # 默认回复
        default_replies = {
            "greeting": "嗨",
            "question": "咋突然问这个",
            "emotion_positive": "不错啊",
            "emotion_negative": "咋了",
            "food": "随便吃点吧",
            "weather": "今天咋样",
        }
        return default_replies.get(intent)

    def _apply_rule(
        self,
        rule: dict,
        user_profile: dict = None,
        conversation_history: list[dict] = None
    ) -> str:
        """应用规则，根据画像调整回复"""
        reply = rule["reply"]
        if user_profile:
            traits = user_profile.get("traits", [])
            if "急性子" in traits and rule.get("keywords") == ["在吗"]:
                reply = "在在在 快说"
        return reply
