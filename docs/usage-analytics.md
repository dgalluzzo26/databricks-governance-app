# Usage analytics (Phases 1–3)

Daily rollup of Unity Catalog lineage into small summary tables and an admin Usage dashboard
to answer **who has access and how access is used** without scanning `system.access` on every page load.

Phase 4 (Genie Ask) builds directly on these tables and endpoints.

## What runs

| Piece | Role |
|---|---|
| Lakeflow Job `${app_name}-usage-rollup` | Daily 06:00 UTC (+ manual trigger). Runs as the Account Admin SP. |
| `backend/jobs/run_usage_rollup.py` | Job entrypoint. |
| `backend/app/services/usage_rollup.py` | SQL builders + run loop. |
| `backend/app/services/group_membership.py` | Account SCIM group → user expansion (nested groups included). |
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
| `group_member_snapshot` | Users inside each group that holds a `data_access` attribute (nesting expanded). |
| `governed_table_snapshot` | Enrolled tables carrying a `data_access_control` tag, with the tag value (path). |
| `table_access_snapshot` | Who holds access to each governed table, directly or through a group (current). |

Enrollment perimeter: catalogs tagged `data_access_controlled=true` (or the Data Access settings enrollment tag).

`access_mode` for sensitive columns mirrors the `has_sensitivity` mask UDF: a read is **clear** when the user (or a group they belong to) holds a matching sensitivity path; **exempt** when they are on a mask policy `EXCEPT` list; otherwise **masked**.

### Access holders (Phase 3)

A holder is a USER or GROUP assignment whose value covers the table's `data_access_control` tag value: equal to it, or a dotted prefix of it (`finance` covers `finance.budget`). Wildcard (`*`) assignments are excluded. Group holders expand to members in `group_member_snapshot` via Account SCIM (member display names are resolved to `userName` by SCIM id).

**Unused access**: a holder with zero reads on that table in the window.  
**Reads without assigned access**: a user with reads in the window and no holder row on that table (often owners, admins, or grants made outside the app)—reported, not judged.

Column-level sensitivity holder reporting is **not** in Phase 3.

## Prerequisites

1. **Account Admin SP** is the job `run_as` (`sync_job_run_as_sp`) and owns the usage schema after setup.
2. **System table grants** (metastore admin), if setup could not apply them:

```sql
GRANT USE CATALOG ON CATALOG system TO `<account-admin-sp-application-id>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.access TO `<account-admin-sp-application-id>`;
GRANT USE SCHEMA, SELECT ON SCHEMA system.query TO `<account-admin-sp-application-id>`;
```

3. **Identity mirror** `{app_catalog}.{app_schema}.identity_attributes` (and definitions) exists so clear vs masked labelling and access-holder matching work.
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

## Dashboard & API

The **Data access analytics** page (`/data-access/analytics`) has three tabs:

- **Data usage** (`?tab=usage`): Headline KPIs (table reads, active users, tables accessed, sensitive reads, clear vs masked), daily trend charts, sensitive column usage breakdown, top tables accessed (with click-to-inspect table drilldown), top users, on-demand "Refresh usage" trigger, and CSV exports.
- **Access reports** (`?tab=access`): Governed tables with holder / used / unused / no-access counts, per-table holder drilldown (grants + reads), unused access list, reads without assigned access, and CSV exports for auditors.
- **Sync health** (`?tab=sync`): Sync run outcomes, planned grant and membership diffs, run duration, fingerprints, and paged run/grant history.

### Endpoints (`/api/usage-analytics/*`, Admin only)

| Method | Path | Description |
|---|---|---|
| `GET` | `/overview?days=30` | Overview KPIs, daily trends, top tables/users, sensitive column breakdown, and last rollup status. |
| `GET` | `/tables?days=30&q=...&limit=50` | Filtered list of tables accessed with read counts and distinct users. |
| `GET` | `/users?days=30&q=...&limit=50` | Filtered list of users with read counts and distinct tables accessed. |
| `GET` | `/table-detail?table_full_name=...&days=30` | Single-table drilldown: readers, column access modes, and recent read statements. |
| `POST` | `/refresh` | Triggers the Lakeflow Job (`USAGE_ROLLUP_JOB_ID`) via the Databricks Jobs API. |
| `GET` | `/export?kind=tables\|users\|sensitive&days=30` | Downloads usage CSV export. |
| `GET` | `/access/tables?days=30&q=...&limit=50` | Governed tables with holder vs usage counts. |
| `GET` | `/access/table-detail?table_full_name=...&days=30` | Holders (with grants and reads) and readers who hold no grant. |
| `GET` | `/access/unused?days=30&limit=200` | Holders with zero reads in the window. |
| `GET` | `/access/no-access?days=30&limit=200` | Readers with no assigned grant on the table. |
| `GET` | `/access/export?kind=tables\|holders\|unused\|no-access&days=30` | Downloads access-report CSV export. |
