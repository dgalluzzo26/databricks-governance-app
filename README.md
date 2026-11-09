# Data Governance App

A Databricks App for federated data access governance in Unity Catalog. FastAPI backend, React
frontend, deployed with one Declarative Automation Bundle that also creates everything the app
needs (catalogs, schema, Lakebase database, setup job).

Features are added one at a time. Current scope: the app shell. It shows the signed-in user and
their groups, and reports deployment and storage health.

## Deploy

Requires the Databricks CLI (authenticated to the target workspace), Node 22+, and Python 3.11+.

```bash
./scripts/deploy.sh            # target "dev": Lakebase storage
./scripts/deploy.sh dev-delta  # same workspace, Delta table storage
```

The script builds the UI, deploys the bundle, runs the setup job, and starts the app. Re-running
it is safe; every step is idempotent.

| Created by the bundle | Purpose |
| --- | --- |
| Catalog `data_governance_app`, schema `app` | Delta storage tables (`app_settings`, `audit_events`) |
| Catalog `data_governance_demo` | Optional demo domain (tagged tables and ABAC policies) |
| Lakebase project `data-governance-app`, database `governance` | Lakebase storage |
| Job `data-governance-app-setup` | Creates tables, seeds the demo, grants the app service principal |
| App `data-governance-app` | The app, with SQL warehouse and Lakebase resources |

## Configuration

Deployment settings are bundle variables (`databricks.yml`). Override them per target or at
deploy time, for example `./scripts/deploy.sh dev --var seed_demo=false`.

| Variable | Default | |
| --- | --- | --- |
| `storage_backend` | `lakebase` | `lakebase` or `delta` |
| `warehouse_id` | per target | SQL warehouse for metadata queries and Delta storage |
| `app_catalog` / `app_schema` | `data_governance_app` / `app` | Delta storage location |
| `demo_catalog`, `seed_demo` | `data_governance_demo`, `true` | Demo domain |
| `lakebase_project_id`, `lakebase_database_id` | `data-governance-app`, `governance` | Lakebase storage |
| `admin_group` | `admins` | Members are app administrators |

The app reads these as environment variables (`backend/app/core/config.py`). With Lakebase, the
app service principal creates and owns the `governance_app` schema on first start, so deploy
before pointing a local run at the same database.

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
