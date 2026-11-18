"""Request-scoped access to the application and its authenticated principal."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request

from ..domain import Domain, Principal
from ..errors import ApiError
from ..workers.runtime import Runtime


@dataclass
class Services:
    domain: Domain
    runtime: Runtime


def services(request: Request) -> Services:
    return request.app.state.services


SESSION_COOKIE = "__Host-amsg"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def same_origin(request: Request, origin: str | None) -> bool:
    """Browsers send Origin on every non-GET fetch, so a missing or foreign one is refused."""
    return origin is not None and request.headers.get("origin") == origin


def principal(request: Request, app: Services = Depends(services)) -> Principal:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    settings = app.domain.settings
    from_cookie = False
    if scheme.lower() != "bearer" or not token:
        token = request.cookies.get(SESSION_COOKIE, "") if settings.web_enabled else ""
        from_cookie = bool(token)
    if not token:
        raise ApiError(401, "unauthenticated", "A device or connector credential is required.")
    if from_cookie and request.method not in SAFE_METHODS and not same_origin(request, settings.web_origin):
        raise ApiError(403, "cross_origin", "This request did not come from the a-msg page.")
    found = app.domain.authenticate(token.strip())
    if found is None or (from_cookie and found.client != "web"):
        raise ApiError(401, "unauthenticated", "This credential is invalid or revoked.")
    return found


def device(who: Principal = Depends(principal)) -> Principal:
    if who.scope != "device":
        raise ApiError(403, "forbidden", "This route requires a device credential.")
    return who


def connector(who: Principal = Depends(principal)) -> Principal:
    if who.scope != "connector" or who.connection_id is None:
        raise ApiError(403, "forbidden", "This route requires a connector credential.")
    return who
