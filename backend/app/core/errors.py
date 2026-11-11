"""Application error hierarchy and FastAPI handlers that map it to JSON responses.

Every error is rendered as ``{"error": {"code", "message", "details"}}`` with the HTTP status
of the error class.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


class AppError(Exception):
    """Base for errors that are safe to show to API clients; maps to HTTP 500.

    Raise a subclass to choose the status; the message and details are returned verbatim.

    Args:
        message: Human-readable error message returned to the client.
        details: Optional JSON-serializable context returned under ``details``.

    Attributes:
        status_code: HTTP status code of the response.
        code: Stable machine-readable error code.
        message: Human-readable error message.
        details: Extra context; empty dict when none was given.
    """

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidRequestError(AppError):
    """Input failed business validation; maps to HTTP 422 ``invalid_request``.

    Raised for invalid identifiers, values outside a vocabulary, bad state transitions
    (e.g. deciding a non-pending request), or SQL / expressions the app refuses to run.
    """

    status_code = 422
    code = "invalid_request"


class UnauthorizedError(AppError):
    """Caller identity could not be established; maps to HTTP 401 ``unauthorized``.

    Raised when the request carries no user identity, or when on-behalf-of-user auth is
    enabled but no user access token was forwarded by the Databricks Apps proxy.
    """

    status_code = 401
    code = "unauthorized"


class ForbiddenError(AppError):
    """Caller is authenticated but not allowed; maps to HTTP 403 ``forbidden``.

    Raised when a non-admin calls an admin endpoint or a user tries to decide or cancel a
    request they are not entitled to.
    """

    status_code = 403
    code = "forbidden"


class NotFoundError(AppError):
    """Requested entity does not exist; maps to HTTP 404 ``not_found``."""

    status_code = 404
    code = "not_found"


class UpstreamError(AppError):
    """A Databricks API or SQL statement failed or timed out; maps to HTTP 502 ``upstream_error``."""

    status_code = 502
    code = "upstream_error"


class NotConfiguredError(AppError):
    """A required resource is not configured; maps to HTTP 503 ``not_configured``.

    Raised when, for example, no SQL warehouse ID is set, Lakebase settings are missing or
    invalid, or the storage backend is unavailable.
    """

    status_code = 503
    code = "not_configured"


def _body(code: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build the standard JSON error envelope.

    Args:
        code: Machine-readable error code.
        message: Human-readable error message.
        details: Optional extra context; defaults to an empty dict.

    Returns:
        The ``{"error": {...}}`` response body.
    """
    return {"error": {"code": code, "message": message, "details": details or {}}}


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers that render ``AppError`` and unexpected exceptions as JSON.

    ``AppError`` subclasses keep their status, code, message and details (5xx are logged at
    WARNING). Any other exception becomes a generic 500 ``internal_error`` without leaking
    internals, and is logged with its traceback.

    Args:
        app: FastAPI application to register the handlers on.
    """

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        """Render an ``AppError`` with its own status code and envelope."""
        if exc.status_code >= 500:
            logger.warning("%s: %s", exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=_body(exc.code, exc.message, exc.details))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        """Log an unexpected exception and return a generic 500 response."""
        logger.exception("Unhandled error")
        return JSONResponse(status_code=500, content=_body("internal_error", "Unexpected server error"))
