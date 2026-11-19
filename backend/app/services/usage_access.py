"""Access reports: who holds access to governed tables, and how that access is used.

Reads the rollup snapshots in ``{app_catalog}.usage``:

- ``governed_table_snapshot``: enrolled tables carrying an ``access_path`` tag.
- ``table_access_snapshot``: (table, user) holders, direct or through a group, with the grant behind it.
- ``user_table_usage_daily``: reads per user per table per day (the usage window).

Definitions used throughout:

- **Holder**: a user who holds a data-access grant on a governed table.
- **Unused access**: a holder with zero reads on that table in the window.
- **Reads without assigned access**: a user with reads in the window and no holder row on that table.
  These are typically owners, admins, or grants made outside the app. They are reported, not judged.
"""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from backend.app.clients.cache import TtlCache
from backend.app.clients.sql import SqlExecutor
from backend.app.core.config import AppConfig
from backend.app.core.errors import InvalidRequestError
from backend.app.models.usage_analytics import (
    AccessGrant,
    AccessHolder,
    AccessNoAccessItem,
    AccessNoAccessList,
    AccessTableDetail,
    AccessTableList,
    AccessTableSummary,
    AccessUnusedItem,
    AccessUnusedList,
    UsageUserTableSummary,
)
from backend.app.services.usage_rollup import USAGE_SCHEMA, UsageTables

logger = logging.getLogger(__name__)

DEFAULT_DAYS = 30
MAX_DAYS = 90
CACHE_SECONDS = 60
MAX_EXPORT_ROWS = 20000
EXPORT_KINDS = ("tables", "holders", "unused", "no-access")


def clamp_days(days: int | None) -> int:
    """Clamp the requested window to 1..90 days."""
    if days is None:
        return DEFAULT_DAYS
    return min(max(1, days), MAX_DAYS)


def parse_grants(raw: str | None) -> list[AccessGrant]:
    """Decode the ``via|principal|value`` grant list built by the SQL (``;`` separated)."""
    grants: list[AccessGrant] = []
    for part in (raw or "").split(";"):
        pieces = part.split("|", 2)
        if len(pieces) != 3 or not pieces[0] or not pieces[1]:
            continue
        grants.append(AccessGrant(via_type=pieces[0], via_principal=pieces[1], held_value=pieces[2]))
    return grants


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _int(value: Any) -> int:
    return int(value or 0)


class UsageAccessService:
    """Access-vs-usage queries over the rollup snapshots.

    Args:
        sql: Warehouse executor running queries.
        config: Application configuration (app catalog).
        cache: Optional TTL cache for query results.
    """

    def __init__(self, sql: SqlExecutor, config: AppConfig, cache: TtlCache | None = None) -> None:
        self._sql = sql
        self._tables = UsageTables(catalog=config.app_catalog, schema=USAGE_SCHEMA, identity_table="")
        self._cache = cache or TtlCache(CACHE_SECONDS, maxsize=64)

    # -- shared SQL -------------------------------------------------------------------------

    def _ctes(self) -> str:
        """CTEs: ``win`` (reads in the window), ``pairs`` (holders), ``governed`` (governed tables)."""
        usage = self._tables.fqn("user_table_usage_daily")
        holders = self._tables.fqn("table_access_snapshot")
        governed = self._tables.fqn("governed_table_snapshot")
        return f"""
win AS (
  SELECT lower(table_full_name) AS table_full_name, lower(user_name) AS user_name,
    sum(reads) AS reads, max(last_read_at) AS last_read_at
  FROM {usage}
  WHERE event_date >= date_sub(current_date(), :days)
  GROUP BY 1, 2
),
pairs AS (
  SELECT table_full_name, user_name,
    array_join(collect_set(concat_ws('|', via_type, via_principal, held_value)), ';') AS grants
  FROM {holders}
  GROUP BY table_full_name, user_name
),
governed AS (
  SELECT table_full_name, catalog_name, schema_name, table_name, tag_value
  FROM {governed}
)"""

    def _as_of(self) -> str | None:
        rows = self._sql.query(
            f"SELECT max(snapshot_date) AS as_of FROM {self._tables.fqn('governed_table_snapshot')}"
        )
        return _text(rows[0].get("as_of")) if rows else None

    # -- tables -----------------------------------------------------------------------------

    def tables(self, days: int | None = None, q: str | None = None, limit: int = 50) -> AccessTableList:
        """Governed tables with holder counts, holders who read, unused holders, and reader gaps."""
        window = clamp_days(days)
        cap = max(1, min(limit, 200))
        needle = (q or "").strip().lower()
        return self._cache.get_or_load(
            ("access-tables", window, needle, cap),
            lambda: self._build_tables(window, needle, cap),
        )

    def _build_tables(self, days: int, needle: str, limit: int) -> AccessTableList:
        items, total = self._table_rows(days, needle, limit)
        return AccessTableList(as_of=self._as_of(), days=days, tables=items, total=total)

    def _table_rows(self, days: int, needle: str, limit: int | None) -> tuple[list[AccessTableSummary], int]:
        where = ""
        params: dict[str, Any] = {"days": days}
        if needle:
            where = "WHERE lower(g.table_full_name) LIKE :search"
            params["search"] = f"%{needle}%"
        base = f"""
WITH {self._ctes()}
SELECT g.table_full_name, g.catalog_name, g.schema_name, g.table_name, g.tag_value,
  coalesce(h.holders, 0) AS holders,
  coalesce(hw.holders_with_reads, 0) AS holders_with_reads,
  coalesce(rg.readers_without_access, 0) AS readers_without_access,
  coalesce(tr.reads, 0) AS reads,
  tr.last_read_at
FROM governed g
LEFT JOIN (
  SELECT table_full_name, count(*) AS holders FROM pairs GROUP BY table_full_name
) h ON h.table_full_name = g.table_full_name
LEFT JOIN (
  SELECT p.table_full_name, count(*) AS holders_with_reads
  FROM pairs p JOIN win w ON w.table_full_name = p.table_full_name AND w.user_name = p.user_name
  GROUP BY p.table_full_name
) hw ON hw.table_full_name = g.table_full_name
LEFT JOIN (
  SELECT w.table_full_name, count(*) AS readers_without_access
  FROM win w
  LEFT JOIN pairs p ON p.table_full_name = w.table_full_name AND p.user_name = w.user_name
  WHERE p.user_name IS NULL
  GROUP BY w.table_full_name
) rg ON rg.table_full_name = g.table_full_name
LEFT JOIN (
  SELECT table_full_name, sum(reads) AS reads, max(last_read_at) AS last_read_at
  FROM win GROUP BY table_full_name
) tr ON tr.table_full_name = g.table_full_name
{where}
ORDER BY readers_without_access DESC, reads DESC, g.table_full_name"""
        count_rows = self._sql.query(f"SELECT count(*) AS n FROM ({base}) t", params)
        total = _int(count_rows[0].get("n") if count_rows else 0)
        sql = base if limit is None else f"{base}\nLIMIT {int(limit)}"
        rows = self._sql.query(sql, params)
        items = []
        for r in rows:
            holders = _int(r.get("holders"))
            with_reads = _int(r.get("holders_with_reads"))
            items.append(
                AccessTableSummary(
                    table_full_name=str(r.get("table_full_name") or ""),
                    catalog_name=str(r.get("catalog_name") or ""),
                    schema_name=str(r.get("schema_name") or ""),
                    table_name=str(r.get("table_name") or ""),
                    tag_value=str(r.get("tag_value") or ""),
                    holders=holders,
                    holders_with_reads=with_reads,
                    unused_holders=max(0, holders - with_reads),
                    readers_without_access=_int(r.get("readers_without_access")),
                    reads=_int(r.get("reads")),
                    last_read_at=_text(r.get("last_read_at")),
                )
            )
        return items, total

    # -- table drilldown --------------------------------------------------------------------

    def table_detail(self, table_full_name: str, days: int | None = None) -> AccessTableDetail:
        """Holders of one table (with their grants and reads) and readers who hold no grant."""
        window = clamp_days(days)
        name = table_full_name.strip().lower()
        if not name:
            raise InvalidRequestError("table_full_name is required")
        return self._cache.get_or_load(
            ("access-table-detail", name, window),
            lambda: self._build_table_detail(name, window),
        )

    def _build_table_detail(self, table: str, days: int) -> AccessTableDetail:
        params: dict[str, Any] = {"days": days, "table": table}
        ctes = self._ctes()
        tag_rows = self._sql.query(
            f"WITH {ctes} SELECT tag_value FROM governed WHERE table_full_name = :table", params
        )
        holder_rows = self._sql.query(
            f"""WITH {ctes}
SELECT p.user_name, p.grants, coalesce(w.reads, 0) AS reads, w.last_read_at
FROM pairs p
LEFT JOIN win w ON w.table_full_name = p.table_full_name AND w.user_name = p.user_name
WHERE p.table_full_name = :table
ORDER BY reads ASC, p.user_name""",
            params,
        )
        reader_rows = self._sql.query(
            f"""WITH {ctes}
SELECT w.user_name, w.reads, w.last_read_at
FROM win w
LEFT JOIN pairs p ON p.table_full_name = w.table_full_name AND p.user_name = w.user_name
WHERE w.table_full_name = :table AND p.user_name IS NULL
ORDER BY w.reads DESC, w.user_name""",
            params,
        )
        holders = [
            AccessHolder(
                user_name=str(r.get("user_name") or ""),
                grants=parse_grants(_text(r.get("grants"))),
                reads=_int(r.get("reads")),
                last_read_at=_text(r.get("last_read_at")),
                used=_int(r.get("reads")) > 0,
            )
            for r in holder_rows
        ]
        readers = [
            UsageUserTableSummary(
                user_name=str(r.get("user_name") or ""),
                reads=_int(r.get("reads")),
                last_read_at=_text(r.get("last_read_at")),
            )
            for r in reader_rows
        ]
        return AccessTableDetail(
            table_full_name=table,
            tag_value=str(tag_rows[0].get("tag_value") or "") if tag_rows else "",
            days=days,
            as_of=self._as_of(),
            holders=holders,
            readers_without_access=readers,
        )

    # -- unused access and reads without access ---------------------------------------------

    def unused(self, days: int | None = None, limit: int = 200) -> AccessUnusedList:
        """Holders (per table and user) with no reads in the window."""
        window = clamp_days(days)
        cap = max(1, min(limit, 1000))
        return self._cache.get_or_load(
            ("access-unused", window, cap),
            lambda: self._build_unused(window, cap),
        )

    def _unused_rows(self, days: int, limit: int | None) -> tuple[list[AccessUnusedItem], int]:
        base = f"""WITH {self._ctes()}
SELECT p.table_full_name, p.user_name, p.grants, g.tag_value
FROM pairs p
JOIN governed g ON g.table_full_name = p.table_full_name
LEFT JOIN win w ON w.table_full_name = p.table_full_name AND w.user_name = p.user_name
WHERE w.user_name IS NULL
ORDER BY p.table_full_name, p.user_name"""
        return self._run_listing(
            base,
            days,
            limit,
            lambda r: AccessUnusedItem(
                table_full_name=str(r.get("table_full_name") or ""),
                user_name=str(r.get("user_name") or ""),
                tag_value=str(r.get("tag_value") or ""),
                grants=parse_grants(_text(r.get("grants"))),
            ),
        )

    def _build_unused(self, days: int, limit: int) -> AccessUnusedList:
        items, total = self._unused_rows(days, limit)
        return AccessUnusedList(days=days, total=total, items=items)

    def no_access(self, days: int | None = None, limit: int = 200) -> AccessNoAccessList:
        """Users with reads in the window on governed tables where they hold no grant."""
        window = clamp_days(days)
        cap = max(1, min(limit, 1000))
        return self._cache.get_or_load(
            ("access-no-access", window, cap),
            lambda: self._build_no_access(window, cap),
        )

    def _no_access_rows(self, days: int, limit: int | None) -> tuple[list[AccessNoAccessItem], int]:
        base = f"""WITH {self._ctes()}
SELECT w.table_full_name, w.user_name, w.reads, w.last_read_at
FROM win w
JOIN governed g ON g.table_full_name = w.table_full_name
LEFT JOIN pairs p ON p.table_full_name = w.table_full_name AND p.user_name = w.user_name
WHERE p.user_name IS NULL
ORDER BY w.reads DESC, w.table_full_name, w.user_name"""
        return self._run_listing(
            base,
            days,
            limit,
            lambda r: AccessNoAccessItem(
                table_full_name=str(r.get("table_full_name") or ""),
                user_name=str(r.get("user_name") or ""),
                reads=_int(r.get("reads")),
                last_read_at=_text(r.get("last_read_at")),
            ),
        )

    def _build_no_access(self, days: int, limit: int) -> AccessNoAccessList:
        items, total = self._no_access_rows(days, limit)
        return AccessNoAccessList(days=days, total=total, items=items)

    def _run_listing(self, base: str, days: int, limit: int | None, build: Any) -> tuple[list, int]:
        params: dict[str, Any] = {"days": days}
        count_rows = self._sql.query(f"SELECT count(*) AS n FROM ({base}) t", params)
        total = _int(count_rows[0].get("n") if count_rows else 0)
        sql = base if limit is None else f"{base}\nLIMIT {int(limit)}"
        rows = self._sql.query(sql, params)
        return [build(r) for r in rows], total

    # -- exports ----------------------------------------------------------------------------

    def export_csv(self, kind: str, days: int | None = None) -> str:
        """CSV for auditors: ``tables``, ``holders`` (every holder), ``unused``, or ``no-access``."""
        window = clamp_days(days)
        normalized = (kind or "").strip().lower()
        if normalized not in EXPORT_KINDS:
            expected = ", ".join(f"'{k}'" for k in EXPORT_KINDS)
            raise InvalidRequestError(f"Unknown export kind '{kind}'. Expected {expected}.")
        out = io.StringIO()
        writer = csv.writer(out)
        if normalized == "tables":
            items, _ = self._table_rows(window, "", None)
            writer.writerow(
                [
                    "Table",
                    "Access Tag Value",
                    "Holders",
                    "Holders With Reads",
                    "Unused Holders",
                    "Readers Without Access",
                    "Reads",
                    "Last Read At",
                ]
            )
            for t in items[:MAX_EXPORT_ROWS]:
                writer.writerow(
                    [
                        t.table_full_name,
                        t.tag_value,
                        t.holders,
                        t.holders_with_reads,
                        t.unused_holders,
                        t.readers_without_access,
                        t.reads,
                        t.last_read_at or "",
                    ]
                )
        elif normalized == "holders":
            writer.writerow(
                [
                    "Table",
                    "Access Tag Value",
                    "User",
                    "Grants",
                    "Reads",
                    "Last Read At",
                    "Status",
                ]
            )
            for h in self._holder_rows(window)[:MAX_EXPORT_ROWS]:
                writer.writerow(
                    [
                        h["table_full_name"],
                        h["tag_value"],
                        h["user_name"],
                        h["grants_text"],
                        h["reads"],
                        h["last_read_at"] or "",
                        "used" if h["reads"] else "unused",
                    ]
                )
        elif normalized == "unused":
            items, _ = self._unused_rows(window, None)
            writer.writerow(["Table", "Access Tag Value", "User", "Grants"])
            for u in items[:MAX_EXPORT_ROWS]:
                writer.writerow([u.table_full_name, u.tag_value, u.user_name, _grants_text(u.grants)])
        else:
            items, _ = self._no_access_rows(window, None)
            writer.writerow(["Table", "User", "Reads", "Last Read At"])
            for n in items[:MAX_EXPORT_ROWS]:
                writer.writerow([n.table_full_name, n.user_name, n.reads, n.last_read_at or ""])
        return out.getvalue()

    def _holder_rows(self, days: int) -> list[dict[str, Any]]:
        rows = self._sql.query(
            f"""WITH {self._ctes()}
SELECT p.table_full_name, g.tag_value, p.user_name, p.grants, coalesce(w.reads, 0) AS reads,
  w.last_read_at
FROM pairs p
JOIN governed g ON g.table_full_name = p.table_full_name
LEFT JOIN win w ON w.table_full_name = p.table_full_name AND w.user_name = p.user_name
ORDER BY p.table_full_name, p.user_name""",
            {"days": days},
        )
        return [
            {
                "table_full_name": str(r.get("table_full_name") or ""),
                "tag_value": str(r.get("tag_value") or ""),
                "user_name": str(r.get("user_name") or ""),
                "grants_text": _grants_text(parse_grants(_text(r.get("grants")))),
                "reads": _int(r.get("reads")),
                "last_read_at": _text(r.get("last_read_at")),
            }
            for r in rows
        ]


def _grants_text(grants: list[AccessGrant]) -> str:
    return "; ".join(f"{g.via_type.lower()} {g.via_principal} -> {g.held_value}" for g in grants)
