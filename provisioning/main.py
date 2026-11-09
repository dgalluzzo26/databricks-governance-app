"""Run governance app setup steps.

Locally:
    python -m provisioning.main --profile <PROFILE> --warehouse-id <ID>

From the bundle job, the same entry point runs through provisioning/run_setup.py.
"""

from __future__ import annotations

import argparse
import logging
import sys

from databricks.sdk import WorkspaceClient

from provisioning.context import ProvisioningContext, SqlRunner
from provisioning.steps import ALL_STEPS

logger = logging.getLogger("provisioning")


def _bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _csv(value: str) -> list[str]:
    return [v.strip() for v in (value or "").split(",") if v.strip()]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default=None, help="Databricks CLI profile (local runs only)")
    parser.add_argument("--warehouse-id", required=True)
    parser.add_argument("--app-name", default="data-governance-app")
    parser.add_argument("--app-catalog", default="data_governance_app")
    parser.add_argument("--app-schema", default="app")
    parser.add_argument("--demo-catalog", default="data_governance_demo")
    parser.add_argument("--seed-demo", default="true")
    parser.add_argument(
        "--catalog-managed-location",
        default="",
        help="Storage location for new catalogs; leave empty to use the metastore root or Default Storage",
    )
    parser.add_argument(
        "--demo-except-principals",
        default="",
        help="Comma-separated principals excepted from demo masks/filters (default: the caller)",
    )
    parser.add_argument("--steps", default="all", help="Comma-separated step names, or 'all'")
    return parser.parse_args(argv)


def build_context(args: argparse.Namespace) -> ProvisioningContext:
    client = WorkspaceClient(profile=args.profile) if args.profile else WorkspaceClient()
    excepted = _csv(args.demo_except_principals) or [client.current_user.me().user_name]
    return ProvisioningContext(
        client=client,
        sql=SqlRunner(client, args.warehouse_id),
        app_name=args.app_name,
        app_catalog=args.app_catalog,
        app_schema=args.app_schema,
        demo_catalog=args.demo_catalog,
        seed_demo=_bool(args.seed_demo),
        demo_except_principals=excepted,
        catalog_managed_location=args.catalog_managed_location.strip() or None,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = parse_args(argv if argv is not None else sys.argv[1:])
    ctx = build_context(args)

    selected = None if args.steps == "all" else set(_csv(args.steps))
    for step_cls in ALL_STEPS:
        step = step_cls()
        if selected is not None and step.name not in selected:
            continue
        if not step.should_run(ctx):
            logger.info("Skipping %s (demo seeding disabled)", step.name)
            continue
        logger.info("Running step %s", step.name)
        step.run(ctx)
    logger.info("Setup complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
