"""Machine-readable service errors. Every failure leaves through this type or the envelope handlers."""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    def __init__(
        self, status: int, code: str, message: str, *, retryable: bool = False, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}


def missing(what: str) -> ApiError:
    return ApiError(404, "not_found", f"{what} was not found.")


def stale(what: str, current: int | None = None) -> ApiError:
    details = {"current_revision": current} if current is not None else {}
    return ApiError(409, "stale_revision", f"{what} changed since it was read.", details=details)


def conflict(code: str, message: str, **details: Any) -> ApiError:
    return ApiError(409, code, message, details=details)


def invalid(code: str, message: str, **details: Any) -> ApiError:
    return ApiError(422, code, message, details=details)


def unavailable(message: str) -> ApiError:
    return ApiError(503, "provider_unavailable", message, retryable=True)
