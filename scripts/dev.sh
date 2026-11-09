#!/usr/bin/env bash
# Local development: FastAPI on :8000 with reload, Vite on :5173 proxying /api.
#
#   DATABRICKS_CONFIG_PROFILE=<profile> ./scripts/dev.sh
#
# Defaults to in-memory storage. Set STORAGE_BACKEND=delta (plus DATABRICKS_WAREHOUSE_ID) to use
# the deployed Delta tables, or the PG* / LAKEBASE_ENDPOINT variables for Lakebase.
set -euo pipefail

cd "$(dirname "$0")/.."

export STORAGE_BACKEND="${STORAGE_BACKEND:-memory}"
export DATABRICKS_APP_PORT=8000

[[ -d .venv ]] || { python3 -m venv .venv && .venv/bin/pip install -q -r requirements-dev.txt; }
[[ -d frontend/node_modules ]] || (cd frontend && npm ci --no-audit --no-fund)

.venv/bin/uvicorn backend.app.main:create_app --factory --reload --reload-dir backend --port 8000 &
API_PID=$!
trap 'kill $API_PID 2>/dev/null' EXIT

cd frontend && npm run dev
