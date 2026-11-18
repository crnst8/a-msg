"""Application factory: one API process, migrations before serving, errors in one envelope."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .. import __version__
from ..config import Settings
from ..db.migrate import upgrade
from ..domain import Domain
from ..errors import ApiError
from ..providers import ProviderRegistry
from ..providers.bridge import BridgeProvider
from ..providers.cli import CliCodingProvider
from ..providers.fake import FakeProvider
from ..push import PushDelivery
from ..workers.runtime import Runtime
from . import connector, routes, web
from .deps import Services

log = logging.getLogger("amsg.api")


class PairLimiter:
    def __init__(self, limit: int = 10, window: float = 60.0) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        moment = time.monotonic()
        hits = self._hits[key]
        while hits and hits[0] < moment - self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            raise ApiError(429, "rate_limited", "Too many pairing attempts. Wait a minute.", retryable=True)
        hits.append(moment)


class RequestContext:
    """Assigns request IDs and bounds JSON bodies without buffering streaming uploads."""

    def __init__(self, app: ASGIApp, json_limit: int) -> None:
        self.app = app
        self.json_limit = json_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = uuid.uuid4().hex[:16]
        scope.setdefault("state", {})["request_id"] = request_id
        headers = dict(scope["headers"])
        is_upload = scope["path"].endswith("/uploads")
        length = headers.get(b"content-length", b"0")
        if not is_upload and length.isdigit() and int(length) > self.json_limit:
            response = envelope(request_id, ApiError(413, "payload_too_large", "This request is too large."))
            await response(scope, receive, send)
            return

        async def tagged(message: Message) -> None:
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        await self.app(scope, receive, tagged)


def envelope(request_id: str | None, error: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=error.status,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
                "retryable": error.retryable,
                "request_id": request_id,
                "details": error.details,
            }
        },
    )


def create_app(settings: Settings) -> FastAPI:
    if settings.provider == "fake" and not settings.dev_mode:
        raise SystemExit("The fake provider is available only in development mode.")
    settings.ensure_dirs()
    upgrade(settings.db_path, settings.backup_dir)
    domain = Domain(settings)
    domain.seed()
    registry = ProviderRegistry()
    bridge = BridgeProvider(domain)
    registry.register("hermes", bridge)
    registry.register("bridge", bridge)
    cli = CliCodingProvider(domain)
    registry.register("codex", cli)
    registry.register("claude", cli)
    if settings.dev_mode:
        registry.register("fake", FakeProvider(domain))
    runtime = Runtime(domain, registry)
    push = PushDelivery(domain)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.stopping = asyncio.Event()
        await runtime.start()
        await push.start()
        yield
        app.state.stopping.set()
        await push.stop()
        await runtime.stop()
        domain.close()

    app = FastAPI(title="a-msg service", version=__version__, lifespan=lifespan)
    app.state.services = Services(domain=domain, runtime=runtime)
    app.state.pair_limiter = PairLimiter()
    app.state.web_limiter = web.AttemptLimiter()
    app.state.push = push
    app.add_middleware(RequestContext, json_limit=settings.json_limit)
    app.add_middleware(web.WebGuard, settings=settings)
    app.include_router(routes.live)
    app.include_router(routes.router)
    app.include_router(connector.router)
    app.include_router(web.router)

    files = settings.web_files if settings.web_enabled else None
    if files is not None:
        web.mount_interface(app, files)

    @app.exception_handler(ApiError)
    async def on_api_error(request: Request, error: ApiError) -> JSONResponse:
        return envelope(getattr(request.state, "request_id", None), error)

    @app.exception_handler(RequestValidationError)
    async def on_validation(request: Request, error: RequestValidationError) -> JSONResponse:
        details = {"fields": [{"path": list(e["loc"]), "message": e["msg"]} for e in error.errors()]}
        problem = ApiError(422, "validation_error", "The request is not valid.", details=details)
        return envelope(getattr(request.state, "request_id", None), problem)

    @app.exception_handler(HTTPException)
    async def on_http(request: Request, error: HTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(error.status_code, "http_error")
        return envelope(
            getattr(request.state, "request_id", None), ApiError(error.status_code, code, str(error.detail))
        )

    @app.exception_handler(Exception)
    async def on_crash(request: Request, error: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        log.exception("unhandled error", extra={"request_id": request_id})
        return envelope(request_id, ApiError(500, "internal_error", "The service hit an unexpected error."))

    return app
