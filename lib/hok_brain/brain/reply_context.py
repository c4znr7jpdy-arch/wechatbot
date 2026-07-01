from dataclasses import dataclass, field

@dataclass
class ReplyContext:
    """消息决策上下文"""
    self_user_id: str
    is_private: bool = False
    is_mentioned: bool = False
    should_store_only: bool = False
    should_reply: bool = False
    reasons: list[str] = field(default_factory=list)

    @classmethod
    def from_event(cls, event, self_user_id: str) -> "ReplyContext":
        """从 EventEnvelope 构建上下文"""
        is_private = event.chat_id == event.sender_id
        is_mentioned = bool(event.mentioned_user_ids)

        if is_private or is_mentioned:
            return cls(
                self_user_id=self_user_id,
                is_private=is_private,
                is_mentioned=is_mentioned,
                should_reply=True,
                reasons=["mentioned_or_private"]
            )

        return cls(
            self_user_id=self_user_id,
            is_private=False,
            is_mentioned=False,
            should_store_only=True,
            reasons=["stealth_mode"]
        )
