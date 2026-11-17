# Usage analytics (Phase 1)

Daily rollup of Unity Catalog lineage into small summary tables so the app can answer
**who has access and how access is used** without scanning `system.access` on every page load.

Phases 2–4 (Usage dashboard, access reports, Genie Ask) read only these tables.

## What runs

| Piece | Role |
|---|---|
| Lakeflow Job `${app_name}-usage-rollup` | Daily 06:00 UTC (+ manual trigger). Runs as the Account Admin SP. |
| `backend/jobs/run_usage_rollup.py` | Job entrypoint. |
| `backend/app/services/usage_rollup.py` | SQL builders + run loop. |
| Setup step `usage_analytics` | Creates `{app_catalog}.usage`, ownership, app SP `SELECT`, best-effort system grants. |

First successful run backfills **90 days**. Later runs re-process the last **3 days** so late lineage lands. Raw events are kept **90 days**; daily summaries ~**13 months**.

## Tables (`{app_catalog}.usage`)

| Table | Contents |
|---|---|
| `rollup_runs` | One row per run (status, window, counts, `data_as_of`). |
| `read_events` | Table reads from `system.access.table_lineage` (enrolled catalogs only). |
| `column_read_events` | Column reads from `system.access.column_lineage`. |
| `table_usage_daily` | Reads + distinct users per table per day. |
| `user_table_usage_daily` | Reads per user × table × day. |
| `column_usage_daily` | Column reads with `sensitivity_path` and `access_mode` (`clear` / `masked` / `exempt` / `not_sensitive`). |
| `user_group_membership` | Account groups (nesting expanded) for users in scope. |
| `mask_exempt_principals` | Principals on column-mask `EXCEPT` lists. |
| `access_snapshot` | Attributes each user held on the snapshot date (direct or via group). |

Enrollment perimeter: catalogs tagged `data_access_controlled=true` (or the Data Access settings enrollment tag).

`access_mode` for sensitive columns mirrors the `has_sensitivity` mask UDF: a read is **clear** when the user (or a group they belong to) holds a matching sensitivity path; **exempt** when they are on a mask policy `EXCEPT` list; otherwise **masked**.

## Prerequisites

1. **Account Admin SP** is the job `run_as` (`sync_job_run_as_sp`) and owns the usage schema after setup.
2. **System table grants** (metastore admin), if setup could not apply them:

```sql
GRANT USE CATALOG ON CATALOG system TO `<account-admin-sp-application-id>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access TO `<account-admin-sp-application-id>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.query TO `<account-admin-sp-application-id>`;
```

3. **Identity mirror** ` {app_catalog}.{app_schema}.identity_attributes` exists (app UC sync) so clear vs masked labelling works.
4. Enrolled catalogs exist; otherwise the rollup succeeds with empty lineage for the window.

## Deploy / run

```bash
./scripts/deploy.sh dev              # creates job + runs setup (usage schema)
# or after code-only changes:
./scripts/deploy.sh dev --skip-setup
databricks bundle run usage_rollup -t dev --params trigger=user,actor=you@example.com
```

Local scratch (CLI identity, not the Admin SP):

```bash
.venv/bin/python backend/jobs/run_usage_rollup.py \
  --profile <PROFILE> --warehouse-id <ID> --usage-schema usage_scratch
```

## App wiring (for Phase 2)

The app receives `USAGE_ROLLUP_JOB_ID` and `CAN_MANAGE` on the job so an admin Refresh button can start a run without a new permission model.
