from __future__ import annotations

import logging
import sys


def configure_logging(level: str = "INFO") -> None:
    """Databricks Apps only keep stdout/stderr, so log there in a grep-friendly format."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("databricks.sdk", "urllib3", "httpx", "psycopg.pool"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
