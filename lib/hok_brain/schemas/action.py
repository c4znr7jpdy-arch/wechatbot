from dataclasses import dataclass, field
from typing import Literal


DecisionType = Literal["reply", "interject", "silent", "defer"]
ReplyKind = Literal["text", "image", "video", "file", "none"]


@dataclass
class ReplyAction:
    decision: DecisionType
    kind: ReplyKind = "text"
    target_chat_id: str = ""
    target_user_id: str | None = None
    reply_to_message_id: str | None = None
    text: str = ""
    media_paths: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    confidence: float = 0.0
