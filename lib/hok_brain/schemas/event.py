from dataclasses import dataclass, field
from typing import Any, Literal


EventType = Literal[
    "group_message",
    "private_message",
    "group_mention",
    "image_message",
    "video_message",
    "quote_text_message",
    "quote_image_message",
    "quote_unknown_message",
    "system_event",
]

AttachmentType = Literal["image", "video", "audio", "file", "text", "unknown"]


@dataclass
class Attachment:
    type: AttachmentType
    url: str | None = None
    thumbnail_url: str | None = None
    local_path: str | None = None
    thumbnail_local_path: str | None = None
    mime_type: str | None = None
    name: str | None = None
    raw_xml: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class EventEnvelope:
    platform: str
    adapter: str
    event_type: EventType
    message_id: str
    chat_id: str
    sender_id: str
    chat_name: str | None = None
    sender_name: str | None = None
    mentioned_user_ids: list[str] = field(default_factory=list)
    reply_to_message_id: str | None = None
    text: str = ""
    attachments: list[Attachment] = field(default_factory=list)
    timestamp: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict = field(default_factory=dict)
