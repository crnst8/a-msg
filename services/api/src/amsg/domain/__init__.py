"""Domain service. The HTTP API and agent tools call these same methods."""

from __future__ import annotations

from ..config import Settings
from ..db import Database
from ..storage.objects import ObjectStore
from .artifacts import ArtifactsMixin
from .auth import AuthMixin, Principal
from .automations import AutomationsMixin
from .base import Notifier
from .connections import ConnectionsMixin
from .events import EventsMixin
from .messages import MessagesMixin
from .search import SearchMixin
from .spaces import SpacesMixin
from .threads import ThreadsMixin
from .web import WebMixin


class Domain(
    AuthMixin,
    SpacesMixin,
    ThreadsMixin,
    MessagesMixin,
    ArtifactsMixin,
    AutomationsMixin,
    ConnectionsMixin,
    SearchMixin,
    EventsMixin,
    WebMixin,
):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        settings.ensure_dirs()
        self.db = Database(settings.db_path)
        self.store = ObjectStore(settings.object_dir, settings.tmp_dir)
        self.notifier = Notifier()
        self.db.on_commit(self.notifier.notify)

    def close(self) -> None:
        self.db.dispose()


__all__ = ["Domain", "Principal"]
