"""Databricks Job entrypoint for data-access sync (schedule + Run now).

Usage (spark_python_task parameters)::

    --mode=apply|dry_run|   (empty → use settings.schedule_mode)
    --trigger=schedule|user
    --actor=<user name or scheduler>
    --run_id=<uuid>         (empty → generate inside run_sync)

Exit codes: 0 on success or paused no-op; 1 on failed sync or unexpected error.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Bundle deploys this file under ``.../files/backend/jobs/``; repo root is two up.
# Databricks spark_python_task runs via an IPython kernel where ``__file__`` is
# not defined, so fall back to ``sys.argv[0]`` (the python_file path).
_here = Path(globals().get("__file__") or sys.argv[0]).resolve()
_REPO_ROOT = _here.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from databricks.sdk import WorkspaceClient  # noqa: E402

from backend.app.clients.secrets import hydrate_admin_secrets  # noqa: E402
from backend.app.core.config import get_config  # noqa: E402
from backend.app.core.container import ServiceContainer  # noqa: E402
from backend.app.models.data_access import SyncRunStatus, SyncTrigger  # noqa: E402

logger = logging.getLogger("data_access_sync_job")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", default="", help="dry_run, apply, or empty for settings default")
    parser.add_argument("--trigger", default="schedule", help="schedule or user")
    parser.add_argument("--actor", default="scheduler", help="User name or scheduler")
    parser.add_argument("--run_id", default="", help="Optional predetermined run id")
    parser.add_argument("--warehouse-id", default="", help="DATABRICKS_WAREHOUSE_ID")
    parser.add_argument("--storage-backend", default="", help="STORAGE_BACKEND")
    parser.add_argument("--app-catalog", default="", help="APP_CATALOG")
    parser.add_argument("--app-schema", default="", help="APP_SCHEMA")
    parser.add_argument("--admin-group", default="", help="APP_ADMIN_GROUP")
    parser.add_argument("--admin-secret-scope", default="", help="ADMIN_SECRET_SCOPE")
    parser.add_argument("--lakebase-project-id", default="", help="LAKEBASE_PROJECT_ID")
    parser.add_argument("--lakebase-database-id", default="", help="LAKEBASE_DATABASE_ID")
    return parser.parse_args(argv)


def _apply_env_from_args(args: argparse.Namespace) -> None:
    """Copy deployment args into process env before ``get_config`` / Lakebase resolve."""
    mapping = {
        "warehouse_id": "DATABRICKS_WAREHOUSE_ID",
        "storage_backend": "STORAGE_BACKEND",
        "app_catalog": "APP_CATALOG",
        "app_schema": "APP_SCHEMA",
        "admin_group": "APP_ADMIN_GROUP",
        "admin_secret_scope": "ADMIN_SECRET_SCOPE",
        "lakebase_project_id": "LAKEBASE_PROJECT_ID",
        "lakebase_database_id": "LAKEBASE_DATABASE_ID",
    }
    for attr, env_name in mapping.items():
        value = getattr(args, attr, "") or ""
        if str(value).strip():
            os.environ[env_name] = str(value).strip()


def ensure_lakebase_env() -> None:
    """Fill PGHOST / PGDATABASE / PGUSER / LAKEBASE_ENDPOINT when Apps injection is absent.

    Jobs do not receive the Apps postgres ``valueFrom`` injection. Resolve the production
    branch endpoint and database from ``LAKEBASE_PROJECT_ID`` / ``LAKEBASE_DATABASE_ID``.
    """
    needed = ("PGHOST", "PGDATABASE", "PGUSER", "LAKEBASE_ENDPOINT")
    if all(os.environ.get(key, "").strip() for key in needed):
        return
    project = (os.environ.get("LAKEBASE_PROJECT_ID") or "data-governance-app").strip()
    database_id = (os.environ.get("LAKEBASE_DATABASE_ID") or "governance").strip()
    parent = f"projects/{project}/branches/production"
    client = WorkspaceClient()
    endpoints = list(client.postgres.list_endpoints(parent=parent))
    if not endpoints:
        raise RuntimeError(f"No Lakebase endpoints under {parent}")
    endpoint = endpoints[0]
    hosts = getattr(getattr(endpoint, "status", None), "hosts", None)
    host = getattr(hosts, "host", None) if hosts is not None else None
    if not host:
        raise RuntimeError(f"Lakebase endpoint {endpoint.name} has no host")
    database = client.postgres.get_database(name=f"{parent}/databases/{database_id}")
    db_status = getattr(database, "status", None)
    postgres_db = getattr(db_status, "postgres_database", None) or database_id
    me = client.current_user.me()
    user_name = (getattr(me, "user_name", None) or getattr(me, "id", None) or "").strip()
    if not user_name:
        raise RuntimeError("Could not resolve Job run-as user for PGUSER")
    os.environ["LAKEBASE_ENDPOINT"] = str(endpoint.name)
    os.environ["PGHOST"] = str(host)
    os.environ["PGDATABASE"] = str(postgres_db)
    os.environ["PGUSER"] = user_name
    os.environ.setdefault("PGPORT", "5432")
    os.environ.setdefault("PGSSLMODE", "require")
    logger.info(
        "Resolved Lakebase env endpoint=%s host=%s database=%s user=%s",
        endpoint.name,
        host,
        postgres_db,
        user_name,
    )


def main(argv: list[str] | None = None) -> int:
    """Run one data-access sync and return a process exit code."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    trigger_text = (args.trigger or "schedule").strip().lower()
    try:
        trigger = SyncTrigger(trigger_text)
    except ValueError:
        logger.error("Invalid trigger %r (expected schedule or user)", args.trigger)
        return 1

    try:
        _apply_env_from_args(args)
        ensure_lakebase_env()
        hydrate_admin_secrets()
        get_config.cache_clear()
        config = get_config()
        container = ServiceContainer.build(config)
        # App SP owns Lakebase tables and runs DDL; Job only needs a live connection.
        container.startup(migrate_storage=False, sync_schedule=False)
        try:
            settings = container.data_access.get_settings().settings
            if trigger == SyncTrigger.SCHEDULE and not settings.schedule_enabled:
                logger.info("Schedule paused (schedule_enabled=false); exiting without sync")
                return 0
            mode = (args.mode or "").strip().lower()
            if not mode:
                mode = (settings.schedule_mode.value if settings.schedule_mode else "dry_run").lower()
            run_id = (args.run_id or "").strip() or None
            run = container.data_access.run_sync(
                actor=(args.actor or "scheduler").strip() or "scheduler",
                trigger=trigger,
                mode=mode,
                run_id=run_id,
            )
            if run.status == SyncRunStatus.FAILED:
                logger.error("Data-access %s %s failed: %s", mode, run.run_id, run.error)
                return 1
            logger.info(
                "Data-access %s %s finished status=%s error=%s",
                mode,
                run.run_id,
                run.status.value,
                run.error,
            )
            return 0
        finally:
            container.shutdown()
    except Exception:
        logger.exception("Data-access sync job failed")
        return 1


if __name__ == "__main__":
    rc = main()
    if rc != 0:
        raise RuntimeError(f"Data-access sync job failed with exit code {rc}")
