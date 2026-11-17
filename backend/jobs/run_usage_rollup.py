"""Databricks Job entrypoint for the daily usage rollup (schedule + Refresh now).

Usage (spark_python_task parameters)::

    --warehouse-id=<id> --app-catalog=<catalog> --app-schema=<schema>
    --trigger=schedule|user --actor=<user or scheduler> --run_id=<uuid>
    --backfill-days=90 --lookback-days=3

Locally (validates SQL against a scratch schema as your CLI identity)::

    .venv/bin/python backend/jobs/run_usage_rollup.py --profile <PROFILE> \
        --warehouse-id <ID> --usage-schema usage_scratch

Exit codes: 0 on success; 1 on failure.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# Bundle deploys this file under ``.../files/backend/jobs/``; repo root is two up.
# spark_python_task runs in an IPython kernel without ``__file__``; fall back to argv[0].
_here = Path(globals().get("__file__") or sys.argv[0]).resolve()
_REPO_ROOT = _here.parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from databricks.sdk import WorkspaceClient  # noqa: E402

from backend.app.clients.sql import SqlExecutor, quote_identifier  # noqa: E402
from backend.app.services.account_groups import group_names_from_user_resource  # noqa: E402
from backend.app.services.catalog_enrollment import (  # noqa: E402
    DEFAULT_ENROLLMENT_TAG_KEY,
    DEFAULT_ENROLLMENT_TAG_VALUE,
)
from backend.app.services.usage_rollup import (  # noqa: E402
    DEFAULT_BACKFILL_DAYS,
    DEFAULT_LOOKBACK_DAYS,
    USAGE_SCHEMA,
    UsageRollup,
    UsageTables,
)

logger = logging.getLogger("usage_rollup_job")

_ACCOUNT_SCIM_USERS = "/api/2.0/account/scim/v2/Users"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="", help="CLI profile (local runs only)")
    parser.add_argument("--warehouse-id", default=os.environ.get("DATABRICKS_WAREHOUSE_ID", ""))
    parser.add_argument("--app-catalog", default=os.environ.get("APP_CATALOG", "data_governance_app"))
    parser.add_argument("--app-schema", default=os.environ.get("APP_SCHEMA", "app"))
    parser.add_argument("--usage-schema", default=USAGE_SCHEMA)
    parser.add_argument("--enrollment-tag-key", default=DEFAULT_ENROLLMENT_TAG_KEY)
    parser.add_argument("--enrollment-tag-value", default=DEFAULT_ENROLLMENT_TAG_VALUE)
    parser.add_argument("--trigger", default="schedule")
    parser.add_argument("--actor", default="scheduler")
    parser.add_argument("--run_id", default="")
    parser.add_argument("--backfill-days", type=int, default=DEFAULT_BACKFILL_DAYS)
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    return parser.parse_args(argv)


def account_group_resolver(client: WorkspaceClient):
    """Return ``user_name -> account groups`` via Account SCIM, falling back to workspace SCIM."""

    def resolve(user_name: str) -> list[str]:
        escaped = user_name.replace('"', '\\"')
        try:
            payload = client.api_client.do(
                "GET",
                _ACCOUNT_SCIM_USERS,
                query={"filter": f'userName eq "{escaped}"', "attributes": "id,userName,groups"},
            )
            resources = (payload or {}).get("Resources") or [] if isinstance(payload, dict) else []
            if resources:
                resource = resources[0]
                # Filtered list responses omit ``groups``; read the user by id to get them.
                if "groups" not in resource and resource.get("id"):
                    resource = client.api_client.do(
                        "GET",
                        f"{_ACCOUNT_SCIM_USERS}/{resource['id']}",
                        query={"attributes": "id,groups"},
                    )
                return group_names_from_user_resource(resource if isinstance(resource, dict) else {})
        except Exception as exc:  # noqa: BLE001 - Account SCIM may be unavailable to this identity
            logger.debug("Account SCIM lookup failed for %s: %s", user_name, exc)
        users = list(client.users.list(filter=f'userName eq "{escaped}"', attributes="id,groups"))
        if not users:
            return []
        return sorted(
            {str(getattr(g, "display", "") or "").strip() for g in (users[0].groups or [])} - {""},
            key=str.lower,
        )

    return resolve


def main(argv: list[str] | None = None) -> int:
    """Run one usage rollup and return a process exit code."""
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(levelname)s %(message)s")
    args = _parse_args(argv)
    if not args.warehouse_id:
        logger.error("--warehouse-id is required")
        return 1
    client = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    sql = SqlExecutor(lambda: client, args.warehouse_id, wait_timeout_seconds=50, max_wait_seconds=1800)
    tables = UsageTables(
        catalog=args.app_catalog,
        schema=args.usage_schema,
        identity_table=(
            f"{quote_identifier(args.app_catalog)}.{quote_identifier(args.app_schema)}."
            "`identity_attributes`"
        ),
    )
    sql.execute(
        f"CREATE SCHEMA IF NOT EXISTS {quote_identifier(args.app_catalog)}."
        f"{quote_identifier(args.usage_schema)}"
    )
    rollup = UsageRollup(
        sql,
        tables,
        (args.enrollment_tag_key, args.enrollment_tag_value),
        account_group_resolver(client),
    )
    result = rollup.run(
        trigger=args.trigger,
        actor=args.actor,
        backfill_days=args.backfill_days,
        lookback_days=args.lookback_days,
        run_id=args.run_id.strip() or None,
    )
    logger.info(
        "Usage rollup %s %s window=%s..%s catalogs=%s reads=%s column_reads=%s users=%s as_of=%s",
        result.run_id,
        result.status,
        result.window_start,
        result.window_end,
        ",".join(result.catalogs),
        result.read_events,
        result.column_read_events,
        result.users_resolved,
        result.data_as_of,
    )
    if result.status != "success":
        logger.error("Usage rollup failed: %s", result.error)
        return 1
    return 0


if __name__ == "__main__":
    rc = main()
    if rc != 0:
        raise RuntimeError(f"Usage rollup job failed with exit code {rc}")
