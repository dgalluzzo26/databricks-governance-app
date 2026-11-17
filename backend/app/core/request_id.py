"""Per-request correlation ID shared by logs, error responses, request metrics, and tasks.

The request middleware sets the ID for each ``/api`` request (reusing a well-formed incoming
``X-Request-ID`` header) and echoes it back on the response, so a UI error can be matched to
the server log line and the stored request metric.
"""

from __future__ import annotations

import re
import uuid
from contextvars import ContextVar

HEADER = "x-request-id"

_VALID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_current: ContextVar[str | None] = ContextVar("request_id", default=None)


def new_request_id(incoming: str | None = None) -> str:
    """Return ``incoming`` when it is a safe ID, otherwise a fresh random one.

    Args:
        incoming: Value of the caller's ``X-Request-ID`` header, if any.

    Returns:
        The ID to use for this request.
    """
    if incoming and _VALID.match(incoming):
        return incoming
    return uuid.uuid4().hex[:16]


def set_request_id(request_id: str | None) -> None:
    """Bind ``request_id`` to the current context (None clears it)."""
    _current.set(request_id)


def current_request_id() -> str | None:
    """Return the request ID bound to the current context, or None outside a request."""
    return _current.get()
