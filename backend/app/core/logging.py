"""Process-wide logging setup for the Databricks Apps runtime."""

from __future__ import annotations

import logging
import sys

from backend.app.core.request_id import current_request_id


class RequestIdFilter(logging.Filter):
    """Adds ``request_id`` (``-`` outside a request) to every record for the log format."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Attach the current request ID and keep the record."""
        record.request_id = current_request_id() or "-"
        return True


def configure_logging(level: str = "INFO") -> None:
    """Databricks Apps only keep stdout/stderr, so log there in a grep-friendly format.

    Replaces any existing root handlers with a single stdout handler and quiets chatty
    third-party loggers (Databricks SDK, urllib3, httpx, psycopg pool) to WARNING. Each line
    carries the request ID (``rid=``) so UI errors can be matched to log lines.

    Args:
        level: Root log level name (case-insensitive), e.g. ``INFO`` or ``debug``.
    """
    handler = logging.StreamHandler(sys.stdout)
    fmt = "%(asctime)s %(levelname)s %(name)s rid=%(request_id)s %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("databricks.sdk", "urllib3", "httpx", "psycopg.pool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
