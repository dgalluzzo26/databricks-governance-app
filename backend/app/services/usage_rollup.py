"""Daily usage rollup: who read which tables and sensitive columns in enrolled catalogs.

Reads Unity Catalog lineage (``system.access.table_lineage`` / ``column_lineage``) for
catalogs carrying the enrollment tag and writes small summary tables to
``{app_catalog}.usage`` so the dashboard and Genie never scan system tables.

Each run re-processes a short lookback window (lineage lands late), so every statement is
"delete the window, insert the window" and safe to re-run. Sensitive-column reads are
labelled ``clear`` / ``masked`` / ``exempt`` using the same rule as the ``has_sensitivity``
mask UDF, evaluated against assignments and group memberships at rollup time.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol

from backend.app.clients.sql import quote_identifier, string_literal
from backend.app.services.catalog_enrollment import enrolled_catalogs_sql

logger = logging.getLogger(__name__)

USAGE_SCHEMA = "usage"
DEFAULT_BACKFILL_DAYS = 90
DEFAULT_LOOKBACK_DAYS = 3
EVENT_RETENTION_DAYS = 90
SUMMARY_RETENTION_DAYS = 400
_INSERT_BATCH = 200

# One "read" is one statement (query, notebook command, or job step) touching the object.
_READ_KEY = "coalesce(statement_id, record_id)"


class Sql(Protocol):
    """The slice of :class:`~backend.app.clients.sql.SqlExecutor` the rollup uses."""

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a statement and return dict rows."""
        ...

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
        """Run a statement for its side effects."""
        ...


GroupResolver = Callable[[str], list[str]]


@dataclass(frozen=True)
class UsageTables:
    """Fully qualified names of the usage schema and the identity mirror it joins."""

    catalog: str
    identity_table: str
    schema: str = USAGE_SCHEMA

    def fqn(self, table: str) -> str:
        """Backtick-quoted ``catalog.schema.table`` for a usage table."""
        return f"{quote_identifier(self.catalog)}.{quote_identifier(self.schema)}.{quote_identifier(table)}"


@dataclass
class RollupResult:
    """Outcome of one rollup run (also persisted to ``rollup_runs``)."""

    run_id: str
    status: str
    window_start: date | None = None
    window_end: date | None = None
    catalogs: list[str] = field(default_factory=list)
    read_events: int = 0
    column_read_events: int = 0
    users_resolved: int = 0
    data_as_of: str | None = None
    error: str | None = None


def table_ddl(tables: UsageTables) -> list[str]:
    """CREATE TABLE IF NOT EXISTS statements for every usage table."""
    t = tables.fqn
    return [
        f"""CREATE TABLE IF NOT EXISTS {t("rollup_runs")} (
  run_id STRING, trigger STRING, actor STRING, status STRING,
  started_at TIMESTAMP, finished_at TIMESTAMP,
  window_start DATE, window_end DATE, catalogs STRING,
  read_events BIGINT, column_read_events BIGINT, users_resolved INT,
  data_as_of TIMESTAMP, error STRING
) COMMENT 'One row per usage rollup run'""",
        f"""CREATE TABLE IF NOT EXISTS {t("read_events")} (
  event_date DATE, event_time TIMESTAMP, user_name STRING,
  table_full_name STRING, catalog_name STRING, schema_name STRING, table_name STRING,
  entity_type STRING, entity_id STRING, statement_id STRING, record_id STRING,
  target_table_full_name STRING, direct_access BOOLEAN
) CLUSTER BY (event_date)
COMMENT 'Table reads from lineage for enrolled catalogs (90 days)'""",
        f"""CREATE TABLE IF NOT EXISTS {t("column_read_events")} (
  event_date DATE, event_time TIMESTAMP, user_name STRING,
  table_full_name STRING, column_name STRING,
  entity_type STRING, statement_id STRING, record_id STRING
) CLUSTER BY (event_date)
COMMENT 'Column reads from lineage for enrolled catalogs (90 days)'""",
        f"""CREATE TABLE IF NOT EXISTS {t("table_usage_daily")} (
  event_date DATE, table_full_name STRING, catalog_name STRING, schema_name STRING,
  table_name STRING, reads BIGINT, distinct_users BIGINT, last_read_at TIMESTAMP
) CLUSTER BY (event_date) COMMENT 'Reads and distinct users per table per day'""",
        f"""CREATE TABLE IF NOT EXISTS {t("user_table_usage_daily")} (
  event_date DATE, user_name STRING, table_full_name STRING, catalog_name STRING,
  reads BIGINT, last_read_at TIMESTAMP
) CLUSTER BY (event_date) COMMENT 'Reads per user per table per day'""",
        f"""CREATE TABLE IF NOT EXISTS {t("column_usage_daily")} (
  event_date DATE, user_name STRING, table_full_name STRING, column_name STRING,
  sensitivity_category STRING, sensitivity STRING, sensitivity_path STRING,
  access_mode STRING, reads BIGINT, last_read_at TIMESTAMP
) CLUSTER BY (event_date)
COMMENT 'Column reads per user per day; access_mode is clear, masked, exempt, or not_sensitive'""",
        f"""CREATE TABLE IF NOT EXISTS {t("user_group_membership")} (
  snapshot_date DATE, user_name STRING, group_name STRING
) COMMENT 'Account groups (nesting expanded) for users seen in usage or holding attributes'""",
        f"""CREATE TABLE IF NOT EXISTS {t("mask_exempt_principals")} (
  snapshot_date DATE, principal STRING, policy_name STRING
) COMMENT 'Principals in the EXCEPT list of a column-mask policy'""",
        f"""CREATE TABLE IF NOT EXISTS {t("access_snapshot")} (
  snapshot_date DATE, user_name STRING, attribute_key STRING, attribute_value STRING,
  via_principal_type STRING, via_principal_name STRING, expires_at TIMESTAMP
) CLUSTER BY (snapshot_date)
COMMENT 'Attributes each user held on the snapshot date, directly or through a group'""",
    ]


def catalog_filter(column: str, catalogs: Iterable[str]) -> str:
    """``column IN (...)`` over quoted catalog literals; matches nothing when empty."""
    names = sorted({c for c in catalogs if c})
    if not names:
        return "FALSE"
    return f"{column} IN ({', '.join(string_literal(c) for c in names)})"


def refresh_read_events_sql(tables: UsageTables, catalogs: list[str]) -> list[str]:
    """Replace ``read_events`` rows from ``:start`` with fresh table lineage."""
    target = tables.fqn("read_events")
    return [
        f"DELETE FROM {target} WHERE event_date >= :start",
        f"""INSERT INTO {target}
SELECT event_date, event_time, created_by,
  source_table_full_name, source_table_catalog, source_table_schema, source_table_name,
  entity_type, entity_id, statement_id, record_id, target_table_full_name, direct_access
FROM system.access.table_lineage
WHERE event_date >= :start
  AND source_table_full_name IS NOT NULL
  AND {catalog_filter("source_table_catalog", catalogs)}""",
    ]


def refresh_column_events_sql(tables: UsageTables, catalogs: list[str]) -> list[str]:
    """Replace ``column_read_events`` rows from ``:start`` with fresh column lineage."""
    target = tables.fqn("column_read_events")
    return [
        f"DELETE FROM {target} WHERE event_date >= :start",
        f"""INSERT INTO {target}
SELECT event_date, event_time, created_by, source_table_full_name, source_column_name,
  entity_type, statement_id, record_id
FROM system.access.column_lineage
WHERE event_date >= :start
  AND source_table_full_name IS NOT NULL
  AND source_column_name IS NOT NULL
  AND {catalog_filter("source_table_catalog", catalogs)}""",
    ]


def refresh_exempt_sql(tables: UsageTables) -> list[str]:
    """Snapshot every principal excepted from a column-mask policy."""
    target = tables.fqn("mask_exempt_principals")
    return [
        f"DELETE FROM {target}",
        f"""INSERT INTO {target}
SELECT DISTINCT current_date(), principal, policy_name
FROM system.information_schema.abac_policy_definitions
LATERAL VIEW explode(except_principals) e AS principal
WHERE policy_type = 'COLUMN_MASK'""",
    ]


def _principals_cte(tables: UsageTables, users_sql: str) -> str:
    """CTE ``principals(user_name, principal_type, principal)``: each user and their groups."""
    groups = tables.fqn("user_group_membership")
    return f"""principals AS (
  SELECT DISTINCT user_name, 'USER' AS principal_type, lower(user_name) AS principal
  FROM ({users_sql}) u
  UNION
  SELECT DISTINCT g.user_name, 'GROUP', lower(g.group_name)
  FROM {groups} g JOIN ({users_sql}) u ON u.user_name = g.user_name
)"""


def _active_assignments(alias: str = "a") -> str:
    return (
        f"{alias}.revoked_at IS NULL "
        f"AND ({alias}.expires_at IS NULL OR {alias}.expires_at > current_timestamp())"
    )


def aggregate_sql(tables: UsageTables, catalogs: list[str]) -> list[str]:
    """Rebuild the daily summary tables for ``event_date >= :start``."""
    t = tables.fqn
    identity = tables.identity_table
    column_users = f"SELECT DISTINCT user_name FROM {t('column_read_events')} WHERE event_date >= :start"
    return [
        f"DELETE FROM {t('table_usage_daily')} WHERE event_date >= :start",
        f"""INSERT INTO {t("table_usage_daily")}
SELECT event_date, table_full_name, catalog_name, schema_name, table_name,
  count(DISTINCT {_READ_KEY}), count(DISTINCT user_name), max(event_time)
FROM {t("read_events")}
WHERE event_date >= :start
GROUP BY event_date, table_full_name, catalog_name, schema_name, table_name""",
        f"DELETE FROM {t('user_table_usage_daily')} WHERE event_date >= :start",
        f"""INSERT INTO {t("user_table_usage_daily")}
SELECT event_date, user_name, table_full_name, catalog_name,
  count(DISTINCT {_READ_KEY}), max(event_time)
FROM {t("read_events")}
WHERE event_date >= :start
GROUP BY event_date, user_name, table_full_name, catalog_name""",
        f"DELETE FROM {t('column_usage_daily')} WHERE event_date >= :start",
        f"""INSERT INTO {t("column_usage_daily")}
WITH reads AS (
  SELECT event_date, user_name, table_full_name, column_name,
    count(DISTINCT {_READ_KEY}) AS reads, max(event_time) AS last_read_at
  FROM {t("column_read_events")}
  WHERE event_date >= :start
  GROUP BY event_date, user_name, table_full_name, column_name
),
tags AS (
  SELECT concat_ws('.', catalog_name, schema_name, table_name) AS table_full_name, column_name,
    max(CASE WHEN tag_name = 'sensitivity_category' THEN tag_value END) AS sensitivity_category,
    max(CASE WHEN tag_name = 'sensitivity' THEN tag_value END) AS sensitivity
  FROM system.information_schema.column_tags
  WHERE {catalog_filter("catalog_name", catalogs)}
    AND tag_name IN ('sensitivity_category', 'sensitivity')
  GROUP BY 1, 2
),
{_principals_cte(tables, column_users)},
held AS (
  SELECT DISTINCT p.user_name, lower(a.attribute_value) AS held_path
  FROM {identity} a
  JOIN principals p
    ON p.principal = lower(a.principal_name) AND p.principal_type = upper(a.principal_type)
  WHERE a.attribute_key = 'sensitivity' AND {_active_assignments()}
),
exempt AS (
  SELECT DISTINCT p.user_name
  FROM {t("mask_exempt_principals")} e JOIN principals p ON p.principal = lower(e.principal)
),
labelled AS (
  SELECT r.*, g.sensitivity_category, g.sensitivity,
    CASE WHEN g.sensitivity_category IS NOT NULL AND g.sensitivity IS NOT NULL
      THEN concat_ws('.', g.sensitivity_category, g.sensitivity) END AS sensitivity_path
  FROM reads r
  LEFT JOIN tags g ON g.table_full_name = r.table_full_name AND g.column_name = r.column_name
),
clear AS (
  SELECT DISTINCT l.user_name, l.sensitivity_path
  FROM labelled l JOIN held h ON h.user_name = l.user_name
  WHERE lower(l.sensitivity_path) = h.held_path
     OR lower(l.sensitivity_path) LIKE concat(h.held_path, '.%')
)
SELECT l.event_date, l.user_name, l.table_full_name, l.column_name,
  l.sensitivity_category, l.sensitivity, l.sensitivity_path,
  CASE
    WHEN l.sensitivity_path IS NULL THEN 'not_sensitive'
    WHEN x.user_name IS NOT NULL THEN 'exempt'
    WHEN c.user_name IS NOT NULL THEN 'clear'
    ELSE 'masked'
  END,
  l.reads, l.last_read_at
FROM labelled l
LEFT JOIN exempt x ON x.user_name = l.user_name
LEFT JOIN clear c ON c.user_name = l.user_name AND c.sensitivity_path = l.sensitivity_path""",
    ]


def access_snapshot_sql(tables: UsageTables) -> list[str]:
    """Rewrite today's ``access_snapshot`` for every user in ``user_group_membership`` scope."""
    t = tables.fqn
    users = (
        f"SELECT DISTINCT user_name FROM {t('read_events')} "
        f"UNION SELECT DISTINCT user_name FROM {t('user_group_membership')}"
    )
    return [
        f"DELETE FROM {t('access_snapshot')} WHERE snapshot_date = current_date()",
        f"""INSERT INTO {t("access_snapshot")}
WITH {_principals_cte(tables, users)}
SELECT DISTINCT current_date(), p.user_name, a.attribute_key, a.attribute_value,
  upper(a.principal_type), a.principal_name, a.expires_at
FROM {tables.identity_table} a
JOIN principals p
  ON p.principal = lower(a.principal_name) AND p.principal_type = upper(a.principal_type)
WHERE {_active_assignments()}""",
    ]


def retention_sql(tables: UsageTables) -> list[str]:
    """Drop raw events after 90 days and summaries after ~13 months."""
    t = tables.fqn
    return [
        f"DELETE FROM {t('read_events')} WHERE event_date < date_sub(current_date(), {EVENT_RETENTION_DAYS})",
        f"DELETE FROM {t('column_read_events')} "
        f"WHERE event_date < date_sub(current_date(), {EVENT_RETENTION_DAYS})",
        *(
            f"DELETE FROM {t(name)} WHERE {col} < date_sub(current_date(), {SUMMARY_RETENTION_DAYS})"
            for name, col in (
                ("table_usage_daily", "event_date"),
                ("user_table_usage_daily", "event_date"),
                ("column_usage_daily", "event_date"),
                ("access_snapshot", "snapshot_date"),
            )
        ),
    ]


def plan_window(
    today: date, last_window_end: date | None, *, backfill_days: int, lookback_days: int
) -> date:
    """Return the first event date to (re)process.

    First run backfills ``backfill_days``; later runs restart ``lookback_days`` before the
    last successful window end so late-arriving lineage is picked up.
    """
    earliest = today - timedelta(days=max(1, min(backfill_days, EVENT_RETENTION_DAYS)))
    if last_window_end is None:
        return earliest
    return max(earliest, min(today, last_window_end) - timedelta(days=max(0, lookback_days)))


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value)[:10])


def _count(rows: list[dict[str, Any]]) -> int:
    if not rows:
        return 0
    value = next(iter(rows[0].values()))
    return int(value or 0)


class UsageRollup:
    """Runs the rollup statements in order and records the run.

    Args:
        sql: Warehouse executor running as the rollup identity (Account Admin SP).
        tables: Usage schema and identity mirror names.
        enrollment_tag: ``(key, value)`` of the catalog enrollment tag.
        resolve_groups: Returns account group names (nesting expanded) for a user name.
    """

    def __init__(
        self,
        sql: Sql,
        tables: UsageTables,
        enrollment_tag: tuple[str, str],
        resolve_groups: GroupResolver,
        *,
        group_concurrency: int = 8,
    ) -> None:
        self._sql = sql
        self._tables = tables
        self._enrollment_tag = enrollment_tag
        self._resolve_groups = resolve_groups
        self._group_concurrency = group_concurrency

    def run(
        self,
        *,
        trigger: str = "schedule",
        actor: str = "scheduler",
        backfill_days: int = DEFAULT_BACKFILL_DAYS,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        run_id: str | None = None,
        today: date | None = None,
    ) -> RollupResult:
        """Execute one rollup; failures are recorded on the run row and returned."""
        result = RollupResult(run_id=run_id or str(uuid.uuid4()), status="running")
        started = datetime.now(UTC)
        for statement in table_ddl(self._tables):
            self._sql.execute(statement)
        self._record_start(result, trigger, actor, started)
        try:
            self._run(result, today or started.date(), backfill_days, lookback_days)
            result.status = "success"
        except Exception as exc:  # noqa: BLE001 - recorded on the run row, then re-raised by caller
            logger.exception("Usage rollup %s failed", result.run_id)
            result.status = "failed"
            result.error = str(exc)[:4000]
        self._record_finish(result)
        return result

    def _run(self, result: RollupResult, today: date, backfill_days: int, lookback_days: int) -> None:
        catalogs = self._enrolled_catalogs()
        result.catalogs = catalogs
        start = plan_window(
            today, self._last_window_end(), backfill_days=backfill_days, lookback_days=lookback_days
        )
        result.window_start, result.window_end = start, today
        params = {"start": start.isoformat()}
        logger.info("Usage rollup window %s..%s over %s", start, today, catalogs or "no catalogs")

        for statement in refresh_read_events_sql(self._tables, catalogs):
            self._sql.execute(statement, params)
        for statement in refresh_column_events_sql(self._tables, catalogs):
            self._sql.execute(statement, params)
        result.read_events = _count(
            self._sql.query(
                f"SELECT count(*) FROM {self._tables.fqn('read_events')} WHERE event_date >= :start", params
            )
        )
        result.column_read_events = _count(
            self._sql.query(
                f"SELECT count(*) FROM {self._tables.fqn('column_read_events')} WHERE event_date >= :start",
                params,
            )
        )

        result.users_resolved = self._refresh_group_membership(today)
        for statement in refresh_exempt_sql(self._tables):
            self._sql.execute(statement)
        for statement in aggregate_sql(self._tables, catalogs):
            self._sql.execute(statement, params)
        for statement in access_snapshot_sql(self._tables):
            self._sql.execute(statement)
        for statement in retention_sql(self._tables):
            self._sql.execute(statement)

        rows = self._sql.query(f"SELECT max(event_time) AS data_as_of FROM {self._tables.fqn('read_events')}")
        result.data_as_of = (rows[0].get("data_as_of") if rows else None) or None

    def _enrolled_catalogs(self) -> list[str]:
        key, value = self._enrollment_tag
        rows = self._sql.query(enrolled_catalogs_sql(key, value))
        return sorted({str(r.get("catalog_name") or "").strip() for r in rows} - {""})

    def _last_window_end(self) -> date | None:
        rows = self._sql.query(
            f"SELECT max(window_end) AS window_end FROM {self._tables.fqn('rollup_runs')} "
            "WHERE status = 'success'"
        )
        return _parse_date(rows[0].get("window_end")) if rows else None

    def _users_in_scope(self) -> list[str]:
        """Usage readers plus direct USER assignees; only user names (emails) resolve via SCIM."""
        rows = self._sql.query(
            f"""SELECT DISTINCT user_name FROM {self._tables.fqn("read_events")}
UNION
SELECT DISTINCT principal_name FROM {self._tables.identity_table} a
WHERE upper(a.principal_type) = 'USER' AND {_active_assignments()}"""
        )
        names = {str(r.get("user_name") or "").strip() for r in rows}
        return sorted(name for name in names if "@" in name)

    def _refresh_group_membership(self, today: date) -> int:
        users = self._users_in_scope()

        def resolve(user: str) -> tuple[str, list[str]]:
            try:
                return user, self._resolve_groups(user)
            except Exception as exc:  # noqa: BLE001 - one user must not fail the rollup
                logger.warning("Group lookup failed for %s: %s", user, exc)
                return user, []

        with ThreadPoolExecutor(max_workers=max(1, self._group_concurrency)) as pool:
            memberships = list(pool.map(resolve, users))

        target = self._tables.fqn("user_group_membership")
        self._sql.execute(f"DELETE FROM {target}")
        rows = [(user, group) for user, groups in memberships for group in groups]
        snapshot = string_literal(today.isoformat())
        for offset in range(0, len(rows), _INSERT_BATCH):
            batch = rows[offset : offset + _INSERT_BATCH]
            values = ", ".join(
                f"(DATE {snapshot}, {string_literal(user)}, {string_literal(group)})" for user, group in batch
            )
            self._sql.execute(f"INSERT INTO {target} VALUES {values}")
        logger.info("Resolved groups for %s users (%s memberships)", len(users), len(rows))
        return len(users)

    def _record_start(self, result: RollupResult, trigger: str, actor: str, started: datetime) -> None:
        self._sql.execute(
            f"""INSERT INTO {self._tables.fqn("rollup_runs")}
(run_id, trigger, actor, status, started_at)
VALUES (:run_id, :trigger, :actor, 'running', CAST(:started_at AS TIMESTAMP))""",
            {
                "run_id": result.run_id,
                "trigger": trigger,
                "actor": actor,
                "started_at": started.isoformat(),
            },
        )

    def _record_finish(self, result: RollupResult) -> None:
        self._sql.execute(
            f"""UPDATE {self._tables.fqn("rollup_runs")} SET
  status = :status, finished_at = current_timestamp(),
  window_start = CAST(:window_start AS DATE), window_end = CAST(:window_end AS DATE),
  catalogs = :catalogs, read_events = CAST(:read_events AS BIGINT),
  column_read_events = CAST(:column_read_events AS BIGINT),
  users_resolved = CAST(:users_resolved AS INT),
  data_as_of = CAST(:data_as_of AS TIMESTAMP), error = :error
WHERE run_id = :run_id""",
            {
                "run_id": result.run_id,
                "status": result.status,
                "window_start": result.window_start.isoformat() if result.window_start else None,
                "window_end": result.window_end.isoformat() if result.window_end else None,
                "catalogs": ",".join(result.catalogs),
                "read_events": result.read_events,
                "column_read_events": result.column_read_events,
                "users_resolved": result.users_resolved,
                "data_as_of": result.data_as_of,
                "error": result.error,
            },
        )
