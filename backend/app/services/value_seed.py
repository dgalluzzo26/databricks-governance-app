"""SQL-seeded identity attribute vocabularies.

Attribute definitions (typically row-filter dimensions) can source their allowed values from
an admin-supplied read-only query. This module validates that the query is a single
SELECT/WITH statement and runs it on the SQL warehouse, returning the distinct first-column
values.
"""

from __future__ import annotations

import re

from backend.app.clients.sql import SqlExecutor
from backend.app.core.errors import InvalidRequestError, NotConfiguredError

_LEADING = re.compile(r"^\s*(select|with)\b", re.IGNORECASE)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)


def validate_seed_sql(statement: str) -> str:
    """Return the seed query if it is one SELECT/WITH statement, else raise.

    Comments and a trailing semicolon are stripped before checking. This is a structural
    guard; the query is additionally wrapped as a subquery when executed.

    Args:
        statement: Admin-supplied seed query.

    Returns:
        The cleaned query text.

    Raises:
        InvalidRequestError: If the query is empty, has multiple statements, or does not
            start with SELECT or WITH.
    """
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
    """Run a seed query and return distinct non-empty first-column values (sorted).

    Args:
        sql: SQL executor; must be configured with a warehouse.
        statement: Admin-supplied seed query.

    Returns:
        Distinct trimmed first-column values sorted case-insensitively. There is no
        application-imposed row cap; the SQL warehouse timeout is the practical bound.

    Raises:
        NotConfiguredError: If no SQL warehouse is configured.
        InvalidRequestError: If the query is invalid or returns no values (an empty result
            never overwrites a vocabulary).
        UpstreamError: If the warehouse fails to execute the query.
    """
    if sql is None or not sql.configured:
        raise NotConfiguredError("A SQL warehouse is required to run seed queries")
    query = validate_seed_sql(statement)
    # Wrapping as a subquery keeps the statement read-only. Distinct values are collected
    # in process so a seed SELECT of one column can return more than a few hundred rows.
    rows = sql.query(f"SELECT * FROM ({query}) AS seed")
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
    if not values:
        # An empty result usually means the source table is row-filtered for the app; never
        # overwrite a vocabulary with nothing.
        raise InvalidRequestError(
            "Seed query returned no values. If the source table is row-filtered, sync the "
            "row filter policy pack so the app service principals are exempt."
        )
    return sorted(values, key=str.lower)
