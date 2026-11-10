from __future__ import annotations

import re

from backend.app.clients.sql import SqlExecutor
from backend.app.core.errors import InvalidRequestError, NotConfiguredError

MAX_SEED_VALUES = 500

_LEADING = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def validate_seed_sql(statement: str) -> str:
    """Return the seed query if it is one SELECT/WITH statement, else raise."""
    text = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", statement or "")).strip()
    text = text.rstrip(";").strip()
    if not text:
        raise InvalidRequestError("Seed query is empty")
    if ";" in text:
        raise InvalidRequestError("Seed query must be a single statement")
    if not _LEADING.match(text):
        raise InvalidRequestError("Seed query must be a SELECT (or WITH ... SELECT) statement")
    return text


def run_seed_sql(sql: SqlExecutor | None, statement: str) -> list[str]:
    """Run a seed query and return distinct non-empty first-column values (sorted)."""
    if sql is None or not sql.configured:
        raise NotConfiguredError("A SQL warehouse is required to run seed queries")
    query = validate_seed_sql(statement)
    # Wrapping as a subquery keeps the statement read-only and bounded.
    rows = sql.query(f"SELECT * FROM ({query}) AS seed LIMIT {MAX_SEED_VALUES + 1}")
    values: set[str] = set()
    for row in rows:
        if not row:
            continue
        first = next(iter(row.values()))
        if first is None:
            continue
        text = str(first).strip()
        if text:
            values.add(text)
    if len(rows) > MAX_SEED_VALUES:
        raise InvalidRequestError(
            f"Seed query returned more than {MAX_SEED_VALUES} rows; narrow it with DISTINCT or a filter"
        )
    if not values:
        # An empty result usually means the source table is row-filtered for the app; never
        # overwrite a vocabulary with nothing.
        raise InvalidRequestError(
            "Seed query returned no values. If the source table is row-filtered, sync the "
            "row filter policy pack so the app service principals are exempt."
        )
    return sorted(values, key=str.lower)
