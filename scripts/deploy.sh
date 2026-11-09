#!/usr/bin/env bash
# One-command deploy: build UI, deploy the bundle, run setup, start the app.
#
#   ./scripts/deploy.sh                 # target "dev"
#   ./scripts/deploy.sh dev-delta       # Delta storage backend
#   ./scripts/deploy.sh dev --skip-setup --var seed_demo=false
#
# Extra --var name=value flags are passed to every bundle command.
set -euo pipefail

cd "$(dirname "$0")/.."

TARGET="dev"
RUN_SETUP=1
BUNDLE_ARGS=()

if [[ $# -gt 0 && "$1" != --* ]]; then
  TARGET="$1"
  shift
fi
while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-setup) RUN_SETUP=0 ;;
    --var) BUNDLE_ARGS+=("--var" "$2"); shift ;;
    --var=*) BUNDLE_ARGS+=("$1") ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

command -v databricks >/dev/null || { echo "Databricks CLI not found" >&2; exit 1; }
command -v npm >/dev/null || { echo "npm not found" >&2; exit 1; }

step "Building frontend"
(
  cd frontend
  [[ -d node_modules ]] || npm ci --no-audit --no-fund
  npm run build
)

step "Validating bundle (target: $TARGET)"
databricks bundle validate -t "$TARGET" ${BUNDLE_ARGS[@]+"${BUNDLE_ARGS[@]}"}

step "Deploying bundle"
databricks bundle deploy -t "$TARGET" ${BUNDLE_ARGS[@]+"${BUNDLE_ARGS[@]}"}

if [[ $RUN_SETUP -eq 1 ]]; then
  step "Running setup job (tables, demo domain, app grants)"
  databricks bundle run setup_job -t "$TARGET" ${BUNDLE_ARGS[@]+"${BUNDLE_ARGS[@]}"}
fi

step "Deploying and starting the app"
databricks bundle run governance_app -t "$TARGET" ${BUNDLE_ARGS[@]+"${BUNDLE_ARGS[@]}"}

APP_NAME=$(databricks bundle summary -t "$TARGET" -o json ${BUNDLE_ARGS[@]+"${BUNDLE_ARGS[@]}"} \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["resources"]["apps"]["governance_app"]["name"])')
APP_URL=$(databricks apps get "$APP_NAME" -t "$TARGET" -o json 2>/dev/null \
  | python3 -c 'import json,sys; print(json.load(sys.stdin).get("url",""))' || true)

step "Done"
echo "App:  $APP_NAME"
echo "URL:  ${APP_URL:-<see Compute > Apps>}"
echo "Logs: databricks apps logs $APP_NAME -t $TARGET"
