import tempfile
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


@dataclass
class SendResult:
    success: bool
    message_id: str | None = None
    error: str | None = None


def cache_image_from_bytes(data: bytes, ext: str = ".jpg") -> str:
    with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as file:
        file.write(data)
        return file.name


def cache_document_from_bytes(data: bytes, filename: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=f"-{filename}", delete=False) as file:
        file.write(data)
        return file.name


class BasePlatformAdapter:
    def __init__(self, config: Any, platform: Any) -> None:
        self.config = config
        self.platform = platform
        self.is_connected = False
        self.on_event = None

    def _mark_connected(self) -> None:
        self.is_connected = True

    def _mark_disconnected(self) -> None:
        self.is_connected = False

    def build_source(self, chat_id: str, chat_name: str | None = None, chat_type: str = "dm", **kw: Any) -> Any:
        return SimpleNamespace(chat_id=str(chat_id), chat_name=chat_name, chat_type=chat_type, **kw)

    async def handle_message(self, event: Any) -> None:
        await self.on_event(event)
