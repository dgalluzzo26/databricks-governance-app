# Backend & APIs

How the FastAPI backend is put together: request path, identities, storage, services, and which
Databricks APIs each feature calls. The **API reference** tab on this page is generated from the
live route table, so it always matches the deployed code.

---

## Layers

```mermaid
flowchart TD
  browser["React UI (frontend/src)"] -->|fetch /api/...| proxy["Databricks Apps proxy (adds x-forwarded-* headers)"]
  proxy --> routes["Routes: backend/app/api/routes/*.py"]
  routes --> deps["Dependencies: backend/app/api/deps.py (Context, User, Admin, Container)"]
  routes --> services["Services: backend/app/services/*.py"]
  services --> repos["Repositories: backend/app/repositories (Lakebase / Delta / memory)"]
  services --> clients["Clients: backend/app/clients (workspace, sql, auth, cache, secrets)"]
  clients --> dbx["Databricks REST APIs + SQL warehouse"]
  repos --> stateStore["App state (Lakebase Postgres or UC Delta)"]
```

| Layer | Responsibility | Rule |
| --- | --- | --- |
| Routes | HTTP shape, auth dependency, call one service method | No business logic, no SDK calls |
| Services | Business rules, orchestration, SQL generation | Storage-agnostic (use repository interfaces) |
| Repositories | Persist app state | Same interface for every backend (`repositories/base.py`) |
| Clients | Databricks SDK clients, SQL execution, caching | Identity choice lives here |
| Models | Pydantic `ApiModel`: snake_case in Python, camelCase on the wire | Shared by routes and services |

`ServiceContainer` (`backend/app/core/container.py`) builds every client and service once per
process, then `startup()` loads admin credentials, initializes storage, mirrors identity and mask
registries to UC, and starts the grants-sync scheduler.

---

## Request path

```mermaid
sequenceDiagram
  participant UI as Browser
  participant P as Apps proxy
  participant R as Route
  participant D as deps.py
  participant S as Service
  UI->>P: GET /api/... (session cookie)
  P->>R: request + x-forwarded-email / preferred-username / access-token
  R->>D: Context: RequestContextResolver.resolve(headers)
  D->>D: User: IdentityService.current_user (admin group check) + is_approver
  D->>D: Admin: 403 unless is_admin
  R->>S: container.<service>.<method>(...)
  S-->>R: ApiModel
  R-->>UI: JSON (camelCase) or error envelope
```

Errors raise `AppError` subclasses (`backend/app/core/errors.py`) and render as
`{"error": {"code", "message", "details", "requestId"}}`:

| Error | HTTP | Typical cause |
| --- | --- | --- |
| `InvalidRequestError` | 422 | Bad value, wrong state, lock held, apply not ready |
| `UnauthorizedError` | 401 | No forwarded identity (local CLI identity only with `ALLOW_LOCAL_IDENTITY`) |
| `ForbiddenError` | 403 | Non-admin on admin route, not an approver |
| `NotFoundError` | 404 | Unknown id / slug |
| `UpstreamError` | 502 | Databricks API or SQL failed |
| `WarehouseUnavailableError` | 503 | SQL warehouse stopped, starting, or recovering |
| `StatementTimeoutError` | 504 | SQL statement or followed Job ran past its time limit |
| `NotConfiguredError` | 503 | Warehouse, Lakebase, or admin SP missing |

Every request gets an ID (the incoming `x-request-id`, or a new one) that is returned in the
`x-request-id` response header, put in the error envelope and log lines, and stored with the
request metric together with the error code and message. The UI shows it as **Reference** so a
user report can be matched to the request history and logs.

### Tasks and steps

Long actions are recorded as tasks (`services/tasks.py`, `app_tasks` table in Lakebase, memory
otherwise) so the UI can show what is running and which steps finished:

- Routes wrap the work in `container.tasks.track(kind, title, actor, link=...)`. A failure marks
  the task failed with the error code and message, then re-raises.
- Services mark steps with `step("...")` (a context manager that also works as a decorator),
  report progress with `step_detail(...)`, and set a one-line outcome with `task_summary(...)`.
  These are no-ops outside a tracked task, so jobs and tests need no changes. A `track` inside a
  tracked task becomes a nested step.
- Grants sync and usage rollup runs are followed on a background thread
  (`services/job_trackers.py`) that polls the Job run every 10 seconds, for up to 4 hours.
- Running tasks heartbeat every 30 seconds. Tasks without a heartbeat for 3 minutes (for example
  after an instance restart) are marked failed. Tasks older than 30 days are pruned.

`GET /api/activity` returns the caller's own tasks and failed requests for the Activity panel;
`GET /api/performance/tasks` and `/api/performance/requests` return everyone's for admins.

---

## Identities

The backend calls Databricks as one of three identities.

```mermaid
flowchart TD
  call["Backend needs a Databricks call"] --> kind{"What kind of work?"}
  kind -->|UC metadata, policies, GRANT, tags, SCIM| admin["Account Admin SP: workspace.admin()"]
  kind -->|App-owned state: Lakebase, UC mirror tables| app["Databricks Apps SP: workspace.app()"]
  kind -->|Who is the caller| user["Caller token: workspace.for_user() (on-behalf-of-user mode)"]
  admin --> creds{"Admin client id / secret in secret scope or env?"}
  creds -->|no| fallback["Falls back to the Apps SP (metastore ABAC unavailable)"]
  creds -->|yes| m2m["OAuth M2M client"]
```

| Identity | Built by | Used for |
| --- | --- | --- |
| Account Admin SP | `WorkspaceClientFactory.admin()` from `ADMIN_SECRET_SCOPE` | `SqlExecutor` (container `sql`), tag policies, ABAC policies, Account SCIM, model serving |
| Apps SP | `WorkspaceClientFactory.app()` (default SDK auth) | App-state SQL (`app_sql`), UC mirror writes, local dev identity |
| Caller | `WorkspaceClientFactory.for_user(token)` | Current-user lookup when `AUTH_MODE=on_behalf_of_user` |

---

## Storage

`build_storage` picks one backend from `STORAGE_BACKEND`; services only see `StorageProvider`.

| Backend | Use | Notes |
| --- | --- | --- |
| `lakebase` | Production OLTP | Identity and mask registries are also mirrored to UC Delta so UDFs can read them |
| `delta` | Simple deployments | UDFs read the same Delta tables directly |
| `memory` | Tests, local dev | Lost on restart |

Repositories: settings (including grants-sync settings, runs, lock), audit, identity attributes,
access requests, category approvers, mask rules, policy exceptions, request metrics.

Lakebase also owns the `request_metrics` table (one row per `/api` request: activity, status,
total milliseconds, and warehouse and Lakebase split) and the `data_access_*_v` SQL views over the
stored sync runs. Other backends keep request metrics in process memory only.

Grants-sync state lives in four more Lakebase tables, written by both the app SP (immediate
membership on approval) and the Admin SP (sync Job): `data_access_gap_items` and
`data_access_gap_meta` (full outstanding work from the last run), `data_access_tag_snapshot` and
`data_access_tag_changes` (table tagging and changes between runs), and
`data_access_membership_events` (approval-time adds and removes). They use text keys rather than
serial ids; the Admin SP is granted table DML and sequence usage. Other backends keep this state
in process memory.

---

## Services and the Databricks APIs they call

| Service | File | Databricks APIs |
| --- | --- | --- |
| `IdentityService` | `services/identity.py` | `current_user.me`, `users.list`, `groups.list` (admin group, directory search). `CurrentUser.attribute_groups` adds account groups reached by nesting (see `AccountGroupResolver`). |
| `AccountGroupResolver` | `services/account_groups.py` | Account SCIM `Users/{id}?attributes=groups` (Account Admin SP, cached with identity TTL): returns nested account groups already expanded so a `dac_finance` member also gets `dac_finance_budget` attributes |
| `IdentityAttributeService` | `services/identity_attributes.py` | Tag policies (governed values), SQL seed queries; UC mirror via `IdentityUcSync` |
| `IdentityUcSync` | `services/identity_uc_sync.py` | Statement Execution: MERGE into `…app.identity_attributes` |
| `AccessRequestService` | `services/access_requests.py` | None directly (storage + `IdentityAttributeService.assign`) |
| `GovernedTagService` | `services/governed_tags.py` | `tag_policies.list_tag_policies / get_tag_policy / update_tag_policy` |
| `MaskRuleService` | `services/mask_rules.py` | Statement Execution (registry mirror, `CREATE FUNCTION`, `CREATE POLICY`), `serving_endpoints.query` (LLM draft) |
| `RowFilterService` | `services/row_filters.py` | Statement Execution (`CREATE FUNCTION has_row_access`, `CREATE POLICY rf_by_dimension`, `ALTER TABLE … SET TAGS`), `information_schema.column_tags` |
| `PolicyExceptionService` | `services/policy_exceptions.py` | Directory lookups; triggers mask + row-filter pack regeneration |
| `PolicyService` | `services/policies.py` | `policies.get_policy`, `information_schema` reads (read-only explorer) |
| `AccessExplorerService` | `services/access_explorer.py` | `information_schema` columns / tags / catalogs / schemas / tables |
| `AttributeExplorerService` | `services/attribute_explorer.py` | Storage + grants-sync tag inventory; sensitivity/row-filter column tags via Statement Execution / `RowFilterService` |
| `DataAccessService` | `services/data_access.py` | `information_schema` tags and privileges, Account SCIM Groups and Users, `users.list` (fallback), `service_principals.list`, SQL `GRANT` / `REVOKE` (parallel, `grant_concurrency`) |
| `WarehouseScaler` | `services/warehouse_scaling.py` | `warehouses.get` / `warehouses.edit` (Admin SP): raises `max_num_clusters` for the grants step, then restores it |
| `RunProfiler` | `services/data_access_profile.py` | None (in-process timers and counters per sync run; thread-local, so worker threads report through the run thread) |
| `PerformanceService` | `services/performance.py` | `apps.get`, `postgres.get_endpoint`, `warehouses.get` (Admin SP) for capacity; request metrics from storage |
| `AppCostService` | `services/app_cost.py` | `system.billing.usage` and `system.billing.list_prices` (Admin SP, billing admin); `apps.get` for the app ID |
| `AppInfoService` | `services/app_info.py` | Storage health, deployment info |
| `AppSettingsService` | `services/app_settings.py` | App-wide runtime switches (`/api/app-settings`), currently the shared metadata-cache bypass; applied at startup and memoized per instance |

All SQL goes through `SqlExecutor` (`clients/sql.py`): Statement Execution API on
`DATABRICKS_WAREHOUSE_ID`, polling until done, then fetching every result chunk. Identifiers go
through `quote_identifier` and literals through `string_literal`; never interpolate raw input.

---

## Feature map: UI page to API to service

| UI page | API prefix | Service | Guide |
| --- | --- | --- | --- |
| Access explorer | `/api/access-explorer` | `AccessExplorerService` | Masking & row filters |
| Attribute explorer | `/api/attribute-explorer` | `AttributeExplorerService` | Masking & row filters |
| Identity, Approvals | `/api/identity` | `IdentityAttributeService`, `AccessRequestService`, `MyAccessService` | Masking & row filters |
| Policies | `/api/policies` | `PolicyService` | |
| Exceptions | `/api/policy-exceptions` | `PolicyExceptionService` | Masking & row filters |
| Row filters | `/api/row-filters` | `RowFilterService` | Masking & row filters |
| Mask rules | `/api/mask-rules` | `MaskRuleService` | Masking & row filters |
| Data access | `/api/data-access` | `DataAccessService` | Grants sync |
| Data access analytics | `/api/data-access/analytics*` | `DataAccessService`, `lakebase_views` | Grants sync |
| Data access outstanding work | `/api/data-access/pending*`, `/api/data-access/tag-changes` | `DataAccessService`, `data_access_gap` | Grants sync |
| Identity access status | `/api/data-access/access-status*` | `DataAccessService`, `data_access_status` | Grants sync |
| Performance | `/api/performance` | `PerformanceService`, `performance_analytics`, `AppCostService` | Performance and app cost (below) |
| App status | `/api/app-info`, `/api/health` | `AppInfoService` | |
| Help | `/api/help` | Route only (guides tagged `user` / `approver` / `admin`; the API returns only guides the caller's role may read, admins get all for View as; API reference is admin-only; FastAPI `/docs` and `/openapi.json` are off) | This page |

---

## Performance and app cost

- **Request timing.** `RequestTimingMiddleware` (`core/perf.py`) times every API request and names it
  with an activity from `core/activities.py`, which has an expected time. `MetricsRecorder` queues
  the samples and writes them to storage every 5 seconds, in batches of 200.
  `performance_analytics` builds the summary, hourly roll-up, and slow-request pages from them. A
  request slower than its expected time is flagged for review; it is not an error.
- **Performance page.** Tabs: **Overview** (KPIs, recommendations, charts), **Latency** (by
  activity, slow requests), **Requests** (every user's requests with success and failure filters,
  error, and reference), **Tasks** (every tracked task with status, outcome, and steps), and
  **Sizing & cost**. The selected tab is kept in `?tab=`.
- **Capacity.** `PerformanceService.capacity()` reads the app's compute size, the Lakebase endpoint,
  and the SQL warehouse size through the Account Admin SP. Each read fails separately.
- **App telemetry.** Not managed by the bundle. Export of OpenTelemetry logs to
  ``${app_catalog}.${app_schema}.otel_{logs,metrics,spans}`` is configured manually on the app.
  The app code does not read those tables; usage analytics reads the ``usage`` schema instead.
- **Scaling.** The bundle runs the app on Medium instances with horizontal scaling
  (`app_min_instances` / `app_max_instances`, fixed at 2; min and max must match unless the
  workspace has app autoscaling) and the warehouse at 1 to 4 clusters. Each
  instance serves blocking handlers from a `REQUEST_THREADS` pool (default 100, since handlers
  mostly wait on the warehouse) and opens up to `LAKEBASE_POOL_MAX` Lakebase connections (default 20).
- **Caching.** Caches are per instance. Slow metadata is served stale-while-revalidate
  (`TtlCache.get_or_refresh`): ABAC policy definitions (about 15 s to read; refreshed after 5 minutes
  and preloaded at start-up), policy affected assets (2 minutes), enrolled catalogs and the
  Attribute Explorer tag inventory (1 minute), and Access Explorer tree listings, search hits, and
  per-table analyze metadata (2 minutes). Analyze reads the table, its columns, and its own tags
  through the Unity Catalog REST APIs, so only the column-tag lookup uses the warehouse. Sync
  runs always read live inventory. After a policy change on one instance, other instances can show
  the old policy list until their next refresh. Admins can turn the cache off in **App settings**
  (`GET/PUT /api/app-settings`, `AppSettingsService`, stored under its own `app_runtime` key). With
  `disable_metadata_cache` on, the shared metadata `TtlCache` is bypassed and cleared so tag edits
  outside the app show up immediately. The switch is memoized for 5 seconds per instance so cache
  reads do not each query Lakebase; identity lookups keep their own cache.
- **Parallel lookups.** `core/concurrency.run_parallel` runs independent warehouse queries for one
  request at once (Access Explorer analyze, the catalog/schema/table tag reads) while keeping
  their time attributed to that request.
- **App cost.** `AppCostService` (`services/app_cost.py`) reports app compute per UTC day, Lakebase
  Postgres spend for this app's endpoint, and the warehouse and job cost of each sync. App compute
  is matched by `usage_metadata.app_id`, Lakebase by `usage_metadata.endpoint_id` (from `PGHOST` /
  `LAKEBASE_ENDPOINT`), warehouse cost by the dedicated warehouse, and job cost by the sync job.
  Warehouse and job cost count toward a sync only inside that run's start and finish time,
  pro-rated by time; Lakebase is always-on and is not attributed to syncs. Costs use list prices,
  not invoice amounts, and billing data lags by up to about a day. Reports are cached for 15
  minutes; failed reads are reported as unavailable and are not cached.

---

## Background work

| Task | Where | Trigger |
| --- | --- | --- |
| Identity UC mirror resync | `ServiceContainer.startup` (background thread `uc-mirror-resync`) | App start (Lakebase backend); each table is replaced with one `INSERT OVERWRITE` (staged for large sources) under a lock shared with incremental writes. The sync Job does not run it (`mirror_uc=False`) |
| Mask registry mirror | `ServiceContainer.startup` | App start when rules exist |
| Grants sync scheduler | `DataAccessService.start_scheduler` | Every `interval_minutes` when `schedule_enabled` |
| Grants sync Job | Lakeflow Job `data-governance-app-data-access-sync` (`backend/jobs/run_data_access_sync.py`) | Schedule and manual runs; the Job preflights Lakebase and the Admin SP, then runs the sync with `mirror_uc=False` |
| Usage rollup Job | Lakeflow Job `data-governance-app-usage-rollup` (`backend/jobs/run_usage_rollup.py`) | Daily 06:00 UTC (+ manual); Account Admin SP rolls lineage for enrolled catalogs into `{app_catalog}.usage` (see `docs/usage-analytics.md`) |
| Recovery Git schedule | `RecoveryService.start_scheduler` | Every `interval_hours` (1–24) when `schedule_enabled`; shared settings lease across app instances |
| Immediate group membership | `DataAccessService.on_assignment_event` (Identity listener) | Each data-access approval, assign, or revoke |
| Outstanding work and tag snapshot | `DataAccessService._record_state` | End of every sync run |
| Request metrics flush | `MetricsRecorder` (daemon thread) | Every 5 seconds; queued metrics are written in batches of 200 |
| Request metrics prune | `MetricsRecorder` | At most hourly; deletes rows older than 14 days |
| Task heartbeat and prune | `TaskService` (daemon thread) | Every 30 seconds; marks stale running tasks failed, hourly deletes tasks older than 30 days |
| Job run followers | `job_trackers.follow_*` (daemon thread per run) | Grants sync and usage rollup runs started from the app |

---

## Adding an endpoint

1. Add request/response models in `backend/app/models/<feature>.py` (subclass `ApiModel`).
2. Add the service method; raise `AppError` subclasses for expected failures.
3. Add the route in `backend/app/api/routes/<feature>.py` with `Admin` or `User` and a docstring
   (its first line becomes the API reference summary).
4. Register new routers in `backend/app/api/routes/__init__.py`.
5. Add the client call in `frontend/src/api/services.ts`, types in `types.ts`, hook in `queries.ts`.
6. Add backend tests in `backend/tests/` and UI tests in `frontend/src/test/`.
