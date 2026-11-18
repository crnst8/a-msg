"""Browser access: sign-in routes, the built interface, response headers, and rules for the proxy listener.

The browser holds a device credential in a `__Host-` cookie (Secure, HttpOnly, SameSite=Strict, path /). Requests
that change anything must also carry the configured Origin. The listener for the HTTPS proxy refuses connector
routes, which only Hermes and runners use, over loopback or their SSH tunnel.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete
from starlette.responses import FileResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .. import schemas
from ..config import Settings
from ..db.models import Meta
from ..domain.auth import Principal
from ..domain.web import WEB_SESSION_DAYS
from ..errors import ApiError
from ..push import PREFIX, valid_endpoint
from .deps import SESSION_COOKIE, Services, device, same_origin, services

log = logging.getLogger("amsg.web")
router = APIRouter(prefix="/v1/web")
HASHED = re.compile(r"-[A-Za-z0-9_-]{8,}\.(?:js|css|woff2|png|svg)$")
INLINE_STYLE = re.compile(r"<style>(.*?)</style>", re.S)


def client_address(request: Request) -> str:
    """The caller's address. Forwarded headers count only when the peer is a configured proxy."""
    peer = request.client.host if request.client else "unknown"
    settings: Settings = request.app.state.services.domain.settings
    if peer in settings.trusted_proxies:
        forwarded = request.headers.get("x-real-ip") or request.headers.get("x-forwarded-for", "").split(",")[-1]
        if forwarded.strip():
            return forwarded.strip()
    return peer


class AttemptLimiter:
    """Counts sign-in attempts per address and in total. A success forgives that address's attempts.

    Attempts are counted before the check runs, so parallel requests cannot all slip in under the limit.
    """

    def __init__(self, per_client: int = 5, overall: int = 30, window: float = 900.0) -> None:
        self.per_client = per_client
        self.overall = overall
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._all: deque[float] = deque()
        self._lock = threading.Lock()

    def _trim(self, hits: deque[float], moment: float) -> None:
        while hits and hits[0] < moment - self.window:
            hits.popleft()

    def attempt(self, key: str) -> None:
        moment = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            self._trim(hits, moment)
            self._trim(self._all, moment)
            if len(hits) >= self.per_client or len(self._all) >= self.overall:
                raise ApiError(429, "rate_limited", "Too many sign-in attempts. Try again in 15 minutes.")
            hits.append(moment)
            self._all.append(moment)

    def forgive(self, key: str) -> None:
        with self._lock:
            for moment in self._hits.pop(key, deque()):
                try:
                    self._all.remove(moment)
                except ValueError:
                    pass


def _enabled(app: Services) -> Settings:
    settings = app.domain.settings
    if not settings.web_enabled:
        raise ApiError(404, "web_disabled", "Browser sign-in is not turned on for this server.")
    return settings


@router.get("/session", response_model=schemas.WebSession)
def web_session(request: Request, app: Services = Depends(services)) -> schemas.WebSession:
    _enabled(app)
    token = request.cookies.get(SESSION_COOKIE)
    found = app.domain.authenticate(token) if token else None
    signed_in = found is not None and found.client == "web"
    return schemas.WebSession(
        configured=app.domain.web_configured(),
        signed_in=signed_in,
        service_id=app.domain.service_id() if signed_in else None,
        device_id=found.id if signed_in and found else None,
    )


@router.post("/sign-in", response_model=schemas.WebSignInResult, responses={401: {"model": schemas.ErrorEnvelope}})
def web_sign_in(
    body: schemas.WebSignIn, request: Request, response: Response, app: Services = Depends(services)
) -> schemas.WebSignInResult:
    settings = _enabled(app)
    if not same_origin(request, settings.web_origin):
        raise ApiError(403, "cross_origin", "This request did not come from the a-msg page.")
    if not app.domain.web_configured():
        raise ApiError(409, "web_not_configured", "Run `amsg web` on the host to set a password first.")
    limiter: AttemptLimiter = request.app.state.web_limiter
    client = client_address(request)
    limiter.attempt(client)
    try:
        token, device_id = app.domain.web_sign_in(body.password, body.code, body.device_name)
    except ApiError as error:
        log.warning("web sign-in refused", extra={"client": client, "code": error.code})
        raise
    limiter.forgive(client)
    log.info("web sign-in", extra={"client": client, "device_id": device_id})
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=WEB_SESSION_DAYS * 86400,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return schemas.WebSignInResult(service_id=app.domain.service_id(), device_id=device_id)


@router.post("/sign-out", status_code=204)
def web_sign_out(request: Request, app: Services = Depends(services)) -> Response:
    settings = _enabled(app)
    if not same_origin(request, settings.web_origin):
        raise ApiError(403, "cross_origin", "This request did not come from the a-msg page.")
    token = request.cookies.get(SESSION_COOKIE)
    found = app.domain.authenticate(token) if token else None
    if found is not None and found.client == "web":
        app.domain.revoke(found.id)
    response = Response(status_code=204)
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return response


# Interface ----------------------------------------------------------------------------------------------
class Assets(StaticFiles):
    """Hashed build files are cached for a year; the fixed-name ones for a day."""

    def file_response(self, full_path, stat_result, scope, status_code=200):  # type: ignore[no-untyped-def]
        response = super().file_response(full_path, stat_result, scope, status_code)
        name = str(full_path)
        response.headers["Cache-Control"] = (
            "public, max-age=31536000, immutable" if HASHED.search(name) else "public, max-age=86400"
        )
        return response


def content_policy(index: str) -> str:
    """The page's policy. Inline style blocks (the splash) are allowed by hash, not by 'unsafe-inline'."""
    hashes = " ".join(
        f"'sha256-{base64.b64encode(hashlib.sha256(block.encode()).digest()).decode()}'"
        for block in INLINE_STYLE.findall(index)
    )
    return (
        "default-src 'none'; script-src 'self'; "
        f"style-src 'self' {hashes}; "
        "img-src 'self' blob: data:; media-src 'self' blob:; frame-src blob:; font-src 'self'; "
        "worker-src 'self'; connect-src 'self'; manifest-src 'self'; "
        "object-src 'none'; base-uri 'none'; form-action 'self'; "
        "frame-ancestors 'none'"
    )


def mount_interface(app: FastAPI, root: Path) -> None:
    index_file = root / "index.html"
    policy = content_policy(index_file.read_text())
    headers = {"Content-Security-Policy": policy, "Cache-Control": "no-cache"}

    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def interface() -> FileResponse:
        return FileResponse(index_file, media_type="text/html", headers=headers)

    if (root / "sw.js").is_file():
        app.add_api_route(
            "/sw.js",
            lambda: FileResponse(
                root / "sw.js",
                media_type="application/javascript",
                headers={"Cache-Control": "no-store", "CDN-Cache-Control": "no-store"},
            ),
            methods=["GET", "HEAD"],
            include_in_schema=False,
        )

    for extra in ("manifest.webmanifest", "favicon.ico", "apple-touch-icon.png", "robots.txt"):
        path = root / extra
        if path.is_file():
            app.add_api_route(
                f"/{extra}",
                lambda path=path: FileResponse(path, headers={"Cache-Control": "public, max-age=86400"}),
                methods=["GET", "HEAD"],
                include_in_schema=False,
            )
    if (root / "assets").is_dir():
        app.mount("/assets", Assets(directory=root / "assets"), name="assets")


# Headers and listener rules ------------------------------------------------------------------------------
SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"x-frame-options", b"DENY"),
    (b"cross-origin-opener-policy", b"same-origin"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"permissions-policy", b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
]


def parse_listen(value: str) -> tuple[str, int]:
    host, _, port = value.rpartition(":")
    if not host or not port.isdigit():
        raise SystemExit(f"web_listen must be address:port, not {value!r}.")
    return host.strip("[]"), int(port)


class WebGuard:
    """Adds security headers everywhere. On the proxy listener, also accepts only the configured proxies (when any are
    set), checks Host, and hides connector routes and the API documents."""

    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.web_port = parse_listen(settings.web_listen)[1] if settings.web_listen else None
        self.web_host = urlsplit(settings.web_origin).netloc.encode() if settings.web_origin else None
        self.proxies = set(settings.trusted_proxies)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path: str = scope["path"]
        server = scope.get("server")
        if self.web_port is not None and server and server[1] == self.web_port:
            client = scope.get("client")
            if self.proxies and (not client or client[0] not in self.proxies):
                await _refuse(send, 403, b"forbidden")
                return
            host = dict(scope["headers"]).get(b"host")
            if host != self.web_host:
                await _refuse(send, 421, b"misdirected")
                return
            if path.startswith("/v1/connector") or path.startswith("/docs") or path.startswith("/openapi"):
                await _refuse(send, 404, b"not found")
                return
        api = path.startswith("/v1/")

        async def tagged(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                headers.extend(item for item in SECURITY_HEADERS if item[0] not in present)
                if api and b"cache-control" not in present:
                    headers.append((b"cache-control", b"no-store"))
            await send(message)

        await self.app(scope, receive, tagged)


async def _refuse(send: Send, status: int, body: bytes) -> None:
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": body})


class PushKeys(BaseModel):
    p256dh: str = Field(min_length=87, max_length=88, pattern=r"^[A-Za-z0-9_=-]+$")
    auth: str = Field(min_length=22, max_length=24, pattern=r"^[A-Za-z0-9_=-]+$")


class PushSubscription(BaseModel):
    endpoint: str = Field(max_length=2048)
    keys: PushKeys

    @field_validator("endpoint")
    @classmethod
    def endpoint_allowed(cls, value: str) -> str:
        try:
            if valid_endpoint(value):
                return value
        except ValueError:
            pass
        raise ValueError("Unsupported push service endpoint")


class PushPreferences(BaseModel):
    notifyCompleted: bool = True
    notifyFailures: bool = True
    notifyReplies: bool = True


class PushRegistration(BaseModel):
    subscription: PushSubscription
    preferences: PushPreferences


def push_device(who: Principal = Depends(device)) -> Principal:
    if who.client != "web":
        raise ApiError(403, "forbidden", "Web Push requires a browser session.")
    return who


@router.get("/push")
def push_status(request: Request, who: Principal = Depends(push_device), app: Services = Depends(services)) -> dict:
    _enabled(app)
    with app.domain.db.read() as session:
        enabled = app.domain.meta_get(session, PREFIX + who.id) is not None
    return {"public_key": request.app.state.push.public, "enabled": enabled}


@router.post("/push", status_code=204)
def push_register(
    body: PushRegistration, who: Principal = Depends(push_device), app: Services = Depends(services)
) -> Response:
    _enabled(app)
    with app.domain.db.write() as session:
        app.domain.meta_set(session, PREFIX + who.id, body.model_dump_json())
    return Response(status_code=204)


@router.delete("/push", status_code=204)
def push_remove(who: Principal = Depends(push_device), app: Services = Depends(services)) -> Response:
    with app.domain.db.write() as session:
        session.execute(delete(Meta).where(Meta.key == PREFIX + who.id))
    return Response(status_code=204)
