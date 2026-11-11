"""Process-wide logging setup for the Databricks Apps runtime."""

from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Databricks Apps only keep stdout/stderr, so log there in a grep-friendly format.

    Replaces any existing root handlers with a single stdout handler and quiets chatty
    third-party loggers (Databricks SDK, urllib3, httpx, psycopg pool) to WARNING.

    Args:
        level: Root log level name (case-insensitive), e.g. ``INFO`` or ``debug``.
    """
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("databricks.sdk", "urllib3", "httpx", "psycopg.pool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
