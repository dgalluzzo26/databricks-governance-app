# Data Governance App

A Databricks App for federated data access governance in Unity Catalog. FastAPI backend, React
frontend, deployed with one Declarative Automation Bundle that also creates everything the app
needs (catalogs, schema, Lakebase database, setup job).

Features are added one at a time. Current scope:

- App shell (signed-in user, theme, collapsible nav)
- **Access explorer** (per-table masks, row filters, and attribute suggestions)
- Read-only **ABAC policy explorer** (metastore vs catalog buckets)
- **App-managed identity attributes** (definitions + assignments in Lakebase/Delta until native
  UC identity attributes exist)
- **Mask rules** and **row filters** (registry + regenerated UC functions/policies)

Developer reference for the mask and row-filter chains:
[docs/mask-and-row-filter-flows.md](docs/mask-and-row-filter-flows.md).

## Deploy

Requires the Databricks CLI (authenticated to the target workspace), Node 22+, and Python 3.11+.

```bash
./scripts/deploy.sh            # target "dev": Lakebase storage
./scripts/deploy.sh dev-delta  # same workspace, Delta table storage
```

The script builds the UI, deploys the bundle, runs the setup job, and starts the app. Re-running
it is safe; every step is idempotent.

| Created | By | Purpose |
| --- | --- | --- |
| Lakebase project `data-governance-app`, database `governance` | bundle | Lakebase storage |
| App `data-governance-app` | bundle | The app, with SQL warehouse and Lakebase resources |
| Job `data-governance-app-setup` | bundle | Runs the setup steps below |
| Catalog `data_governance_app`, schema `app`, Delta tables | setup job | Delta storage (`app_settings`, `audit_events`) |
| Catalog `data_governance_demo` | setup job | Demo domain, only when `seed_demo=true` |
| Grants to the app service principal | setup job | Access to the app schema (and demo catalog) |

The setup job creates catalogs with SQL, so they work on metastores that use Default Storage.
On a metastore without Default Storage or a root location, set `catalog_managed_location`.

## Configuration

Deployment settings are bundle variables (`databricks.yml`). Override them per target or at
deploy time, for example `./scripts/deploy.sh dev --var seed_demo=false`.

| Variable | Default | |
| --- | --- | --- |
| `storage_backend` | `lakebase` | `lakebase` or `delta` |
| `warehouse_id` | per target | SQL warehouse for metadata queries and Delta storage |
| `app_catalog` / `app_schema` | `data_governance_app` / `app` | Delta storage location |
| `demo_catalog`, `seed_demo` | `data_governance_demo`, `false` | Demo domain (creates governed tags, so it needs room under the account's tag policy limit) |
| `catalog_managed_location` | empty | Storage location for new catalogs |
| `lakebase_project_id`, `lakebase_database_id` | `data-governance-app`, `governance` | Lakebase storage |
| `admin_group` | `admins` | Members are app administrators |

The app reads these as environment variables (`backend/app/core/config.py`). The signed-in
user's groups come from `/Me` using the user token the Apps proxy forwards (default
`iam.current-user:read` scope). Membership in `admin_group` makes a user an app admin. With Lakebase, the
app service principal creates and owns the `governance_app` schema on first start, so deploy
before pointing a local run at the same database.

## Account Admin SP (metastore ABAC)

Metastore-level ABAC policies need an **Account Admin** service principal. The Databricks Apps
runtime SP is not enough. Credentials live in secret scope **`databricks-governance-app`**
(override with `admin_secret_scope` / `ADMIN_SECRET_SCOPE`):

| Secret key | Env var injected into the app |
| --- | --- |
| `client_id` | `ADMIN_CLIENT_ID` |
| `client_secret` | `ADMIN_CLIENT_SECRET` |

The app resource declaration grants the Apps SP `READ` on those secrets and injects them at
runtime. The app also hydrates from the scope on startup as a fallback (same pattern as
`data-access-app`). Lakebase still uses the Apps SP; Unity Catalog / ABAC uses the Account Admin SP.

```bash
# Create / refresh (copy from data-access-app if that already holds the Account Admin SP)
databricks secrets create-scope databricks-governance-app --profile "$PROFILE"
databricks secrets put-acl databricks-governance-app <your-email> MANAGE --profile "$PROFILE"
# After the app exists:
APP_SP=$(databricks apps get data-governance-app --profile "$PROFILE" -o json \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["service_principal_client_id"])')
databricks secrets put-acl databricks-governance-app "$APP_SP" READ --profile "$PROFILE"
```

## Architecture

```
backend/app/
  core/          config, errors, logging, ServiceContainer (dependency wiring)
  clients/       workspace client factory, auth strategies (service principal / on-behalf-of user),
                 SQL executor, TTL cache
  models/        API models (camelCase on the wire)
  repositories/  StorageProvider strategy: Lakebase, Delta, in-memory (+ unavailable fallback)
  services/      identity, request context, app info
  api/           FastAPI dependencies and routes (/api/health, /api/me, /api/app-info)
  main.py        app factory; serves the React build as an SPA
provisioning/    setup job: ordered, idempotent ProvisioningStep classes
frontend/src/
  api/           HttpClient, typed API service classes, React Query hooks
  app/           App, routes, navigation config
  components/    layout shell (TopBar, SideNav, UserMenu) and Databricks-styled UI kit
  pages/         Home, App status
```

To add a feature, add a model, repository methods if it stores state, a service, and a route.
Wire them in `ServiceContainer`. On the frontend, add an API class, a query hook, a page, and an
entry in `app/navigation.ts` and `app/routes.tsx`.

## Local development

```bash
python3 -m venv .venv && PIP_INDEX_URL=https://pypi-proxy.dev.databricks.com/simple .venv/bin/pip install -r requirements-dev.txt
DATABRICKS_CONFIG_PROFILE=dbc-16303671-e52d ./scripts/dev.sh   # API :8000, UI :5173
```

Locally the caller is the CLI profile's user (or `DEV_USER_EMAIL`) and storage defaults to
in-memory.

```bash
.venv/bin/pytest && .venv/bin/ruff check backend provisioning   # backend
cd frontend && npm test && npm run typecheck                     # frontend
```
