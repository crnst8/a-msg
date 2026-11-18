"""Opt-in Web Push. Secrets and per-device subscriptions stay in the private database."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, func, select

from .db.models import DeviceCredential, Event, Meta
from .domain.auth import _web_expired

log = logging.getLogger("amsg.push")
PREFIX = "push.device."
# Push services cap the encrypted payload near 4 KB; lock screens show a few lines.
BODY_LIMIT = 400


def plain_text(markdown: str) -> str:
    """Reduce Markdown to the one-paragraph text a lock-screen notification can show."""
    text = re.sub(r"```[^\n]*\n?", "", markdown)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"^\s{0,3}(#{1,6}\s+|>\s?|[-*+]\s+(\[[ xX]\]\s+)?|\d+[.)]\s+)", "", text, flags=re.M)
    text = re.sub(r"^\s*([-*_]\s*){3,}$|^\s*\|?[\s:|-]+\|[\s:|-]*$", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__|~~|`)", "", text)
    text = re.sub(r"(?<!\w)[*_](\S(?:.*?\S)?)[*_](?!\w)", r"\1", text)
    text = " ".join(text.replace("|", " ").split())
    return text if len(text) <= BODY_LIMIT else text[: BODY_LIMIT - 1].rstrip() + "…"


def notification(event) -> dict[str, str] | None:  # type: ignore[no-untyped-def]
    """Title is the space, or the app name for General; the body is the reply itself."""
    space = event.payload.get("space") or {}
    if not space.get("id"):
        return None
    message = event.payload.get("message") or {}
    failed = event.type == "run.failed"
    body = plain_text(message.get("markdown") or "")
    if failed:
        reason = plain_text(message.get("error_message") or (event.payload.get("run") or {}).get("error_message") or "")
        body = f"Reply failed: {reason}" if reason else "Reply failed."
    elif not body:
        files = len(message.get("artifacts") or [])
        body = f"Sent {files} file{'s' if files != 1 else ''}." if files else "Your agent has replied."
    title = "a-msg" if space.get("kind") == "general" else space.get("title") or "a-msg"
    return {"title": title, "body": body, "spaceId": space["id"], "tag": f"run-{event.seq}"}


def valid_endpoint(endpoint: str) -> bool:
    url = urlsplit(endpoint)
    host = url.hostname or ""
    allowed = host in {"fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com"}
    allowed = allowed or host.endswith(".push.apple.com") or host.endswith(".push.services.mozilla.com")
    return bool(allowed and url.scheme == "https" and url.port in (None, 443) and not url.username and not url.fragment)


class NoRedirects(requests.Session):
    def request(self, method, url, **kwargs):  # type: ignore[no-untyped-def]
        kwargs["allow_redirects"] = False
        return super().request(method, url, **kwargs)


class PushDelivery:
    def __init__(self, domain):  # type: ignore[no-untyped-def]
        self.domain = domain
        self.task: asyncio.Task | None = None
        self.stopping = asyncio.Event()
        self.private = ""
        self.public = ""
        if not domain.settings.web_enabled:
            return
        with domain.db.write() as session:
            pem = domain.meta_get(session, "push.vapid")
            if pem is None:
                key = ec.generate_private_key(ec.SECP256R1())
                pem = key.private_bytes(
                    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
                ).decode()
                domain.meta_set(session, "push.vapid", pem)
            key = serialization.load_pem_private_key(pem.encode(), password=None)
            public = key.public_key().public_bytes(
                serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
            )
            self.public = base64.urlsafe_b64encode(public).rstrip(b"=").decode()
            self.private = pem
            if domain.meta_get(session, "push.cursor") is None:
                top = session.scalar(select(func.max(Event.seq))) or 0
                domain.meta_set(session, "push.cursor", str(top))

    async def start(self) -> None:
        if self.public:
            self.task = asyncio.create_task(self.run(), name="web-push")

    async def stop(self) -> None:
        if self.task:
            self.stopping.set()
            await self.task

    async def run(self) -> None:
        while not self.stopping.is_set():
            try:
                await asyncio.to_thread(self.deliver)
            except Exception:
                log.warning("Web Push batch failed; retrying", exc_info=False)
            try:
                await asyncio.wait_for(self.stopping.wait(), timeout=2)
            except TimeoutError:
                pass

    def deliver(self) -> None:
        from datetime import UTC, datetime, timedelta

        d = self.domain
        with d.db.read() as session:
            cursor = int(d.meta_get(session, "push.cursor") or 0)
        for event in d.events_after(cursor):
            if event.type in ("run.completed", "run.failed"):
                # Never surprise a returning device with days of old notifications.
                cutoff = (datetime.now(UTC) - timedelta(hours=1)).isoformat().replace("+00:00", "Z")
                if event.created_at > cutoff:
                    self.send_event(event)
            with d.db.write() as session:
                d.meta_set(session, "push.cursor", str(event.seq))

    def send_event(self, event):  # type: ignore[no-untyped-def]
        from datetime import UTC, datetime

        d = self.domain
        with d.db.read() as session:
            subscriptions = [
                (r.key, json.loads(r.value)) for r in session.scalars(select(Meta).where(Meta.key.startswith(PREFIX)))
            ]
        for key, saved in subscriptions:
            with d.db.read() as session:
                credential = session.get(DeviceCredential, key.removeprefix(PREFIX))
                active = (
                    credential is not None
                    and credential.client == "web"
                    and not credential.revoked_at
                    and not _web_expired(credential.created_at, credential.last_seen_at, datetime.now(UTC))
                )
            if not active:
                self.remove(key)
                continue
            failed = event.type == "run.failed"
            prefs = saved["preferences"]
            if not (prefs["notifyFailures"] if failed else prefs["notifyCompleted"] or prefs["notifyReplies"]):
                continue
            payload = notification(event)
            if payload is None:
                continue
            try:
                from py_vapid import Vapid

                webpush(
                    saved["subscription"],
                    data=json.dumps(payload),
                    vapid_private_key=Vapid.from_pem(self.private.encode()),
                    vapid_claims={"sub": d.settings.web_origin},
                    ttl=3600,
                    timeout=10,
                    requests_session=NoRedirects(),
                )
            except WebPushException as error:
                if error.response is not None and error.response.status_code in (404, 410):
                    self.remove(key)
                else:
                    # A failed endpoint must not block other devices or the chat service.
                    log.warning("Web Push rejected for device %s", key.removeprefix(PREFIX))
            except (ValueError, TypeError):
                self.remove(key)
                log.warning("Removed invalid Web Push subscription for device %s", key.removeprefix(PREFIX))
            except requests.RequestException:
                log.warning("Web Push unavailable for device %s", key.removeprefix(PREFIX))

    def remove(self, key: str) -> None:
        with self.domain.db.write() as session:
            session.execute(delete(Meta).where(Meta.key == key))
