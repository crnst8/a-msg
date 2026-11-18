from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(Enum):
    TEXT = "text"
    PHOTO = "photo"
    DOCUMENT = "document"
    COMMAND = "command"


class ProcessingOutcome(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"


@dataclass
class MessageEvent:
    text: str
    message_type: MessageType = MessageType.TEXT
    user_id: str | None = None
    user_name: str | None = None
    source: Any = None
    message_id: str | None = None
    media_urls: list[str] = field(default_factory=list)
    media_types: list[str] = field(default_factory=list)
    channel_prompt: str | None = None
