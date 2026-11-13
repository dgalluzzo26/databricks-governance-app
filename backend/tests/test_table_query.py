from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest

from backend.app.core.errors import InvalidRequestError
from backend.app.services.table_query import (
    SqlColumn,
    TableQuery,
    escape_like,
    filter_sql,
    order_sql,
    parse_table_query,
    sort_and_filter,
)

SQL_COLUMNS = {
    "name": SqlColumn("name"),
    "change": SqlColumn("concat_ws(' ', group_name, full_name)"),
    "count": SqlColumn("row_count"),
}


def test_parse_defaults_to_ascending_with_no_filters() -> None:
    query = parse_table_query()
    assert query == TableQuery(sort=None, descending=False, filters={})


def test_parse_reads_sort_direction_and_filters() -> None:
    query = parse_table_query(sort="name", direction="DESC", filters='{"name": " alpha ", "count": ""}')
    assert query.sort == "name"
    assert query.descending is True
    # Blank filter text is dropped; text is trimmed.
    assert query.filters == {"name": "alpha"}


@pytest.mark.parametrize(
    ("direction", "filters"),
    [
        ("sideways", None),
        (None, "not-json"),
        (None, "[1, 2]"),
        (None, '{"name": 3}'),
    ],
)
def test_parse_rejects_bad_input(direction: str | None, filters: str | None) -> None:
    with pytest.raises(InvalidRequestError):
        parse_table_query(direction=direction, filters=filters)


def test_escape_like_escapes_wildcards_and_backslash() -> None:
    assert escape_like("50%_off\\x") == "50\\%\\_off\\\\x"


def test_order_sql_uses_whitelisted_expression_and_default_tiebreak() -> None:
    query = TableQuery(sort="change", descending=True)
    sql = order_sql(query, SQL_COLUMNS, "detected_at DESC")
    assert sql == "ORDER BY concat_ws(' ', group_name, full_name) DESC NULLS LAST, detected_at DESC"


def test_order_sql_falls_back_to_default_for_unknown_key() -> None:
    assert (
        order_sql(TableQuery(sort="drop_table"), SQL_COLUMNS, "started_at DESC") == "ORDER BY started_at DESC"
    )
    assert order_sql(TableQuery(), SQL_COLUMNS, "started_at DESC") == "ORDER BY started_at DESC"


def test_filter_sql_binds_values_and_ignores_unknown_keys() -> None:
    params: dict[str, object] = {}
    query = TableQuery(filters={"name": "a_b%", "drop_table": "x", "change": "fin"})
    clauses = filter_sql(query, SQL_COLUMNS, params)
    # Filters are applied in sorted key order; unknown keys never reach SQL.
    assert clauses == [
        "CAST(concat_ws(' ', group_name, full_name) AS TEXT) ILIKE %(tq_0)s ESCAPE '\\'",
        "CAST(name AS TEXT) ILIKE %(tq_1)s ESCAPE '\\'",
    ]
    assert params == {"tq_0": "%fin%", "tq_1": "%a\\_b\\%%"}
    assert "drop_table" not in " ".join(clauses)


@dataclass
class Row:
    name: str | None
    count: int
    when: datetime | None = None


ROWS = [
    Row(name="beta", count=2, when=datetime(2026, 1, 2, tzinfo=UTC)),
    Row(name="Alpha", count=10, when=None),
    Row(name=None, count=1, when=datetime(2026, 1, 1, tzinfo=UTC)),
    Row(name="gamma", count=3, when=datetime(2026, 1, 3, tzinfo=UTC)),
]
READERS = {
    "name": lambda row: row.name,
    "count": lambda row: row.count,
    "when": lambda row: row.when,
}


def _names(rows: list[Row]) -> list[str | None]:
    return [row.name for row in rows]


def test_sort_and_filter_keeps_order_without_a_sort() -> None:
    assert _names(sort_and_filter(ROWS, TableQuery(), READERS)) == ["beta", "Alpha", None, "gamma"]


def test_sort_text_is_case_insensitive_and_empties_last_both_ways() -> None:
    ascending = sort_and_filter(ROWS, TableQuery(sort="name"), READERS)
    descending = sort_and_filter(ROWS, TableQuery(sort="name", descending=True), READERS)
    assert _names(ascending) == ["Alpha", "beta", "gamma", None]
    assert _names(descending) == ["gamma", "beta", "Alpha", None]


def test_sort_numbers_numerically() -> None:
    result = sort_and_filter(ROWS, TableQuery(sort="count"), READERS)
    assert [row.count for row in result] == [1, 2, 3, 10]


def test_sort_datetimes_with_missing_values_last() -> None:
    result = sort_and_filter(ROWS, TableQuery(sort="when", descending=True), READERS)
    assert [row.name for row in result] == ["gamma", "beta", None, "Alpha"]


def test_filter_is_case_insensitive_contains_and_combines() -> None:
    result = sort_and_filter(ROWS, TableQuery(filters={"name": "AL"}), READERS)
    assert _names(result) == ["Alpha"]
    combined = sort_and_filter(ROWS, TableQuery(filters={"name": "a", "count": "3"}), READERS)
    assert _names(combined) == ["gamma"]


def test_filter_treats_missing_values_as_empty_text() -> None:
    result = sort_and_filter(ROWS, TableQuery(filters={"name": "none"}), READERS)
    assert _names(result) == []


def test_default_key_orders_when_no_sort_is_requested() -> None:
    result = sort_and_filter(
        ROWS,
        TableQuery(),
        READERS,
        default_key=lambda row: row.count,
        default_descending=True,
    )
    assert [row.count for row in result] == [10, 3, 2, 1]


def test_api_rejects_bad_sort_input(client, container) -> None:  # noqa: ANN001
    from backend.tests.conftest import headers_for

    admin = headers_for("admin@example.com")
    bad_direction = client.get("/api/performance/slow-requests?direction=sideways", headers=admin)
    assert bad_direction.status_code == 422
    bad_filters = client.get("/api/data-access/pending/items?filters=not-json", headers=admin)
    assert bad_filters.status_code == 422
    ok = client.get(
        '/api/data-access/pending/items?sort=kind&direction=desc&filters={"kind":"member"}', headers=admin
    )
    assert ok.status_code == 200
    assert all("member" in item["kind"] for item in ok.json()["items"])
