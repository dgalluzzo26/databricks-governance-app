"""Server-side sort and column filters for paged tables.

Paged endpoints accept three query parameters:

- ``sort``: a column key from the endpoint's whitelist (unknown keys fall back to the default order).
- ``direction``: ``asc`` or ``desc`` (default ``asc``).
- ``filters``: a JSON object mapping column keys to text. Each filter is a case-insensitive
  "contains" match on that column's text, and all filters must match.

Each endpoint declares its columns once. SQL-backed endpoints map a key to a trusted SQL
expression (never user input); in-memory endpoints map a key to a function that reads the
value from a row. Only whitelisted keys are used, so user input never reaches SQL text.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from backend.app.core.errors import InvalidRequestError

T = TypeVar("T")

MAX_FILTER_TEXT = 200


@dataclass(frozen=True)
class SqlColumn:
    """SQL expressions for one column.

    Attributes:
        sort: Expression used in ``ORDER BY``.
        text: Expression matched by the column filter. Defaults to ``sort``.
    """

    sort: str
    text: str | None = None


@dataclass(frozen=True)
class TableQuery:
    """Parsed sort and filter request.

    Attributes:
        sort: Requested column key, or None for the endpoint default.
        descending: True when sorting high to low.
        filters: Non-empty filter text by column key.
    """

    sort: str | None = None
    descending: bool = False
    filters: dict[str, str] = field(default_factory=dict)


def parse_table_query(
    sort: str | None = None,
    direction: str | None = None,
    filters: str | None = None,
) -> TableQuery:
    """Build a :class:`TableQuery` from raw query-string values.

    Args:
        sort: Column key or None.
        direction: ``asc``, ``desc``, or None (ascending).
        filters: JSON object text mapping column keys to filter text, or None.

    Returns:
        The parsed query. Blank filter text is dropped.

    Raises:
        InvalidRequestError: For an unknown direction or malformed filters JSON.
    """
    normalized = (direction or "asc").strip().lower()
    if normalized not in {"asc", "desc"}:
        raise InvalidRequestError("direction must be asc or desc")
    parsed: dict[str, str] = {}
    if filters:
        try:
            raw = json.loads(filters)
        except json.JSONDecodeError as exc:
            raise InvalidRequestError("filters must be a JSON object") from exc
        if not isinstance(raw, dict):
            raise InvalidRequestError("filters must be a JSON object")
        for key, value in raw.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise InvalidRequestError("filters must map column names to text")
            text = value.strip()[:MAX_FILTER_TEXT]
            if text:
                parsed[key] = text
    sort_key = (sort or "").strip() or None
    return TableQuery(sort=sort_key, descending=normalized == "desc", filters=parsed)


def escape_like(text: str) -> str:
    """Escape ``%``, ``_``, and the backslash escape character for an ILIKE pattern."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def order_sql(query: TableQuery, columns: Mapping[str, SqlColumn], default: str) -> str:
    """Return an ``ORDER BY`` clause for the request.

    Args:
        query: Parsed request.
        columns: Whitelisted columns by key.
        default: Tie-breaking or default ordering, for example ``started_at DESC``.

    Returns:
        A complete ``ORDER BY ...`` clause.
    """
    column = columns.get(query.sort or "")
    if column is None:
        return f"ORDER BY {default}"
    direction = "DESC" if query.descending else "ASC"
    return f"ORDER BY {column.sort} {direction} NULLS LAST, {default}"


def filter_sql(query: TableQuery, columns: Mapping[str, SqlColumn], params: dict[str, Any]) -> list[str]:
    """Return ``WHERE`` clauses for the column filters, adding their parameters to ``params``.

    Args:
        query: Parsed request.
        columns: Whitelisted columns by key.
        params: Query parameter dict to extend (mutated).

    Returns:
        One clause per active filter on a whitelisted column.
    """
    clauses: list[str] = []
    known = [(key, text) for key, text in sorted(query.filters.items()) if key in columns]
    for index, (key, text) in enumerate(known):
        column = columns[key]
        name = f"tq_{index}"
        expression = column.text or column.sort
        clauses.append(f"CAST({expression} AS TEXT) ILIKE %({name})s ESCAPE '\\'")
        params[name] = f"%{escape_like(text)}%"
    return clauses


def sort_and_filter(
    rows: Sequence[T],
    query: TableQuery,
    columns: Mapping[str, Callable[[T], Any]],
    *,
    default_key: Callable[[T], Any] | None = None,
    default_descending: bool = False,
) -> list[T]:
    """Filter and sort in-memory rows.

    Args:
        rows: Rows to process (not mutated).
        query: Parsed request.
        columns: Value reader by column key. Filters match ``str(value)``.
        default_key: Sort key used when the request names no sortable column.
        default_descending: Direction of the default sort.

    Returns:
        A new list of matching rows in the requested order.
    """
    active = [(columns[key], text.lower()) for key, text in sorted(query.filters.items()) if key in columns]
    matched = [row for row in rows if all(needle in _text(reader(row)).lower() for reader, needle in active)]
    reader = columns.get(query.sort or "")
    if reader is not None:
        # Empty values always sort last, in both directions.
        present = [(reader(row), row) for row in matched]
        filled = [pair for pair in present if not _is_empty(pair[0])]
        empty = [row for value, row in present if _is_empty(value)]
        filled.sort(key=lambda pair: _natural(pair[0]), reverse=query.descending)
        return [row for _, row in filled] + empty
    if default_key is not None:
        return sorted(matched, key=default_key, reverse=default_descending)
    return matched


def _text(value: Any) -> str:
    if value is None:
        return ""
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value)


def _is_empty(value: Any) -> bool:
    return value is None or value == ""


def _natural(value: Any) -> Any:
    """Natural sort value: case-insensitive for text, otherwise the value itself."""
    if isinstance(value, str):
        return value.lower()
    return value
