"""SQL views over the data-access sync run history stored in Lakebase.

Run history lives as one JSONB array in ``{schema}.app_settings`` (key ``data_access_runs``,
newest first). These views unnest it so analysts and the Lakebase SQL editor can query it
without parsing JSON:

- ``data_access_runs_v``: one row per run, with full summary counts.
- ``data_access_run_grants_v``: one row per planned GRANT/REVOKE sampled on a run.
- ``data_access_run_membership_v``: one row per planned membership change sampled on a run.
- ``data_access_daily_v``: one row per UTC day and mode, summed from successful runs.
- ``data_access_failures_v``: one row per failed run with its error.

Grant and membership rows come from the plan, which stores at most ``PLAN_ITEM_LIMIT`` sample
rows per list. Use ``data_access_runs_v`` and ``data_access_daily_v`` for totals.

Statements use a ``{schema}`` placeholder and must not contain other curly braces or ``%``.
Views are dropped and recreated on each startup so column changes deploy cleanly; the caller
re-grants SELECT to the Admin SP afterwards.
"""

from __future__ import annotations

_RUN_ROWS = (
    "FROM {schema}.app_settings AS s "
    "CROSS JOIN LATERAL jsonb_array_elements(s.setting_value) AS run "
    "WHERE s.setting_key = 'data_access_runs'"
)

# Dependents are listed before the views they read, so DROP succeeds in this order.
VIEW_DROP_ORDER: tuple[str, ...] = (
    "data_access_failures_v",
    "data_access_daily_v",
    "data_access_run_membership_v",
    "data_access_run_grants_v",
    "data_access_runs_v",
)

_RUNS_V = f"""
CREATE VIEW {{schema}}.data_access_runs_v AS
SELECT
    run->>'run_id' AS run_id,
    run->>'status' AS status,
    run->>'mode' AS mode,
    run->>'trigger' AS trigger,
    run->>'triggered_by' AS triggered_by,
    (run->>'started_at')::timestamptz AS started_at,
    (run->>'finished_at')::timestamptz AS finished_at,
    EXTRACT(EPOCH FROM (
        (run->>'finished_at')::timestamptz - (run->>'started_at')::timestamptz
    )) AS duration_seconds,
    run->>'fingerprint' AS fingerprint,
    run->>'error' AS error,
    (run->'summary'->>'eligible_objects')::int AS eligible_objects,
    (run->'summary'->>'skipped_unreviewed')::int AS skipped_unreviewed,
    (run->'summary'->>'unique_values')::int AS unique_values,
    (run->'summary'->>'groups_to_create')::int AS groups_to_create,
    (run->'summary'->>'groups_existing')::int AS groups_existing,
    (run->'summary'->>'grants_to_add')::int AS grants_to_add,
    (run->'summary'->>'grants_to_revoke')::int AS grants_to_revoke,
    (run->'summary'->>'grants_unchanged')::int AS grants_unchanged,
    (run->'summary'->>'members_to_add')::int AS members_to_add,
    (run->'summary'->>'members_to_remove')::int AS members_to_remove,
    (run->'summary'->>'invalid_values')::int AS invalid_values,
    (run->'plan'->>'truncated')::boolean AS plan_truncated,
    CASE WHEN jsonb_typeof(run->'plan'->'apply_errors') = 'array'
         THEN jsonb_array_length(run->'plan'->'apply_errors') ELSE 0 END AS apply_error_count
{_RUN_ROWS}
"""


def _unnest(array_path: str, alias: str) -> str:
    """Return the FROM/WHERE clause that unnests one plan array per run.

    Args:
        array_path: JSON key under ``plan`` holding the array, e.g. ``grants_to_add``.
        alias: Lateral alias for each element.

    Returns:
        SQL fragment starting at FROM and ending with the run-key filter.
    """
    return (
        "FROM {schema}.app_settings AS s "
        "CROSS JOIN LATERAL jsonb_array_elements(s.setting_value) AS run "
        "CROSS JOIN LATERAL jsonb_array_elements("
        f"COALESCE(run->'plan'->'{array_path}', '[]'::jsonb)) AS {alias} "
        "WHERE s.setting_key = 'data_access_runs'"
    )


_GRANTS_V = (
    "CREATE VIEW {schema}.data_access_run_grants_v AS "
    "SELECT run->>'run_id' AS run_id, (run->>'started_at')::timestamptz AS started_at, "
    "run->>'mode' AS mode, 'add' AS change, g->>'securable_type' AS securable_type, "
    "g->>'full_name' AS full_name, g->>'principal' AS principal, g->>'privilege' AS privilege, "
    "g->>'reason' AS reason " + _unnest("grants_to_add", "g") + " UNION ALL "
    "SELECT run->>'run_id', (run->>'started_at')::timestamptz, run->>'mode', 'revoke', "
    "g->>'securable_type', g->>'full_name', g->>'principal', g->>'privilege', g->>'reason' "
    + _unnest("grants_to_revoke", "g")
)

_MEMBERSHIP_V = (
    "CREATE VIEW {schema}.data_access_run_membership_v AS "
    "SELECT run->>'run_id' AS run_id, (run->>'started_at')::timestamptz AS started_at, "
    "run->>'mode' AS mode, m->>'action' AS action, m->>'group' AS group_name, "
    "m->>'principal_name' AS principal_name, m->>'principal_type' AS principal_type, "
    "m->>'reason' AS reason " + _unnest("membership_changes", "m")
)

_DAILY_V = """
CREATE VIEW {schema}.data_access_daily_v AS
SELECT
    (started_at AT TIME ZONE 'UTC')::date AS day,
    mode,
    COUNT(*) AS runs,
    COUNT(*) FILTER (WHERE status = 'success') AS success_runs,
    COUNT(*) FILTER (WHERE status = 'failed') AS failed_runs,
    COALESCE(SUM(grants_to_add) FILTER (WHERE status = 'success'), 0) AS grants_added,
    COALESCE(SUM(grants_to_revoke) FILTER (WHERE status = 'success'), 0) AS grants_revoked,
    COALESCE(SUM(members_to_add) FILTER (WHERE status = 'success'), 0) AS members_added,
    COALESCE(SUM(members_to_remove) FILTER (WHERE status = 'success'), 0) AS members_removed,
    AVG(duration_seconds) FILTER (WHERE status IN ('success', 'failed')) AS avg_duration_seconds
FROM {schema}.data_access_runs_v
GROUP BY 1, 2
"""

_FAILURES_V = """
CREATE VIEW {schema}.data_access_failures_v AS
SELECT run_id, mode, started_at, triggered_by, error
FROM {schema}.data_access_runs_v
WHERE status = 'failed'
"""

# Creation order matters: runs_v is referenced by daily_v and failures_v.
VIEW_DEFINITIONS: tuple[str, ...] = (
    _RUNS_V,
    _GRANTS_V,
    _MEMBERSHIP_V,
    _DAILY_V,
    _FAILURES_V,
)


def drop_statements() -> list[str]:
    """Return DROP VIEW statements in dependency-safe order (dependents first).

    Returns:
        One ``DROP VIEW IF EXISTS`` statement per view, using the ``{schema}`` placeholder.
    """
    return [f"DROP VIEW IF EXISTS {{schema}}.{name}" for name in VIEW_DROP_ORDER]
