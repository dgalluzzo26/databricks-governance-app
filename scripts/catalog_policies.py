#!/usr/bin/env python3
"""Manage catalog-level ABAC policies as a backup for metastore-level ABAC.

If metastore-level ABAC is ever disabled or restricted in Unity Catalog, this
script replicates the data-governance-app masking and row-filter policies
directly at the catalog level.

Policies applied:
  - mask_sensitivity_variant (COLUMN MASK on non-geo sensitivity-tagged columns)
  - mask_sensitivity_geometry (COLUMN MASK on geometry columns)
  - mask_sensitivity_geography (COLUMN MASK on geography columns)
  - rf_by_dimension (ROW FILTER on row_filter dimension columns)

Usage:
  # Apply backup policies to a catalog (uses default profile dbc-16303671-e52d):
  python scripts/catalog_policies.py apply --catalog data_access_control

  # Inspect effective policies on a catalog:
  python scripts/catalog_policies.py status --catalog data_access_control

  # Drop catalog-level policies when metastore ABAC is active:
  python scripts/catalog_policies.py drop --catalog data_access_control

  # Dry-run (prints SQL statements without executing):
  python scripts/catalog_policies.py apply --catalog data_access_control --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from typing import Any

DEFAULT_PROFILE = os.environ.get(
    "DATABRICKS_CONFIG_PROFILE",
    os.environ.get("DATABRICKS_PROFILE", "dbc-16303671-e52d"),
)
DEFAULT_WAREHOUSE_ID = os.environ.get(
    "DATABRICKS_WAREHOUSE_ID", "21dd266e614f0a03"
)
DEFAULT_APP_CATALOG = os.environ.get("APP_CATALOG", "data_governance_app")
DEFAULT_UDF_SCHEMA = os.environ.get("ABAC_FUNCTION_SCHEMA", "masking_functions")
DEFAULT_EXCEPT_PRINCIPALS = (
    "da48b54e-2dd9-4baf-98a3-67b16e0413db",  # App SP
    "bbf03021-a1ad-4721-a6a3-ddd500982f72",  # Account Admin SP
)
DEFAULT_ROW_FILTER_DIMENSIONS = ("disaster_number", "org", "state")

POLICY_NAMES = (
    "mask_sensitivity_variant",
    "mask_sensitivity_geometry",
    "mask_sensitivity_geography",
    "rf_by_dimension",
)


class DatabricksSql:
    """Executes SQL statements via the Databricks SQL Statements REST API."""

    def __init__(self, profile: str, warehouse_id: str) -> None:
        self.profile = profile
        self.warehouse_id = warehouse_id

    def execute(self, statement: str) -> list[list[Any]]:
        payload = {
            "warehouse_id": self.warehouse_id,
            "statement": statement,
            "wait_timeout": "50s",
        }
        proc = subprocess.run(
            [
                "databricks",
                "api",
                "post",
                "/api/2.0/sql/statements",
                "-p",
                self.profile,
                "--json",
                json.dumps(payload),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        body = json.loads(proc.stdout)
        status = ((body.get("status") or {}).get("state")) or ""
        if status in {"PENDING", "RUNNING"}:
            body = self._wait(body["statement_id"])
            status = ((body.get("status") or {}).get("state")) or ""
        if status != "SUCCEEDED":
            error_msg = (body.get("status") or {}).get("error", {}).get("message")
            raise RuntimeError(error_msg or json.dumps(body.get("status") or body, indent=2))
        result = body.get("result") or {}
        return list(result.get("data_array") or [])

    def _wait(self, statement_id: str) -> dict[str, Any]:
        for _ in range(60):
            proc = subprocess.run(
                [
                    "databricks",
                    "api",
                    "get",
                    f"/api/2.0/sql/statements/{statement_id}",
                    "-p",
                    self.profile,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            body = json.loads(proc.stdout)
            state = (body.get("status") or {}).get("state")
            if state in {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}:
                return body
            time.sleep(2)
        raise TimeoutError(f"Timed out waiting for statement {statement_id}")


def build_except_clause(principals: list[str]) -> str:
    cleaned = [p.strip().strip("`") for p in principals if p.strip()]
    if not cleaned:
        return ""
    return "\nEXCEPT " + ", ".join(f"`{p}`" for p in cleaned)


def build_catalog_policy_statements(
    catalog: str,
    *,
    app_catalog: str = DEFAULT_APP_CATALOG,
    udf_schema: str = DEFAULT_UDF_SCHEMA,
    except_principals: list[str] | None = None,
    row_filter_dimensions: list[str] | None = None,
) -> dict[str, str]:
    cat = catalog.strip().strip("`")
    app_cat = app_catalog.strip().strip("`")
    schema = udf_schema.strip().strip("`")
    except_clause = build_except_clause(
        list(DEFAULT_EXCEPT_PRINCIPALS) if except_principals is None else except_principals
    )
    dimensions = (
        list(DEFAULT_ROW_FILTER_DIMENSIONS)
        if row_filter_dimensions is None
        else [d.strip() for d in row_filter_dimensions if d.strip()]
    )

    statements: dict[str, str] = {}

    # 1. Variant sensitivity mask (non-geo)
    statements["mask_sensitivity_variant"] = f"""CREATE OR REPLACE POLICY mask_sensitivity_variant
ON CATALOG `{cat}`
COMMENT 'Catalog backup: Mask sensitivity columns (non-geo); presentation via mask_rule'
COLUMN MASK `{app_cat}`.`{schema}`.`mask_variant`
TO `account users`{except_clause}
MATCH COLUMNS (
  has_tag('sensitivity_category')
  AND has_tag('sensitivity')
  AND has_tag('data_type')
  AND NOT has_tag_value('data_type', 'geometry')
  AND NOT has_tag_value('data_type', 'geography')
) AS c
ON COLUMN c
USING COLUMNS (
  get_column_tag_value(c, 'sensitivity_category'),
  get_column_tag_value(c, 'sensitivity'),
  get_column_tag_value(c, 'mask_rule')
)"""

    # 2. Geometry mask
    statements["mask_sensitivity_geometry"] = f"""CREATE OR REPLACE POLICY mask_sensitivity_geometry
ON CATALOG `{cat}`
COMMENT 'Catalog backup: Mask sensitivity geometry columns; NULL unless sensitivity path granted'
COLUMN MASK `{app_cat}`.`{schema}`.`mask_geometry_4326`
TO `account users`{except_clause}
MATCH COLUMNS (
  has_tag('sensitivity_category')
  AND has_tag('sensitivity')
  AND has_tag_value('data_type', 'geometry')
) AS c
ON COLUMN c
USING COLUMNS (
  get_column_tag_value(c, 'sensitivity_category'),
  get_column_tag_value(c, 'sensitivity')
)"""

    # 3. Geography mask
    statements["mask_sensitivity_geography"] = f"""CREATE OR REPLACE POLICY mask_sensitivity_geography
ON CATALOG `{cat}`
COMMENT 'Catalog backup: Mask sensitivity geography columns; NULL unless sensitivity path granted'
COLUMN MASK `{app_cat}`.`{schema}`.`mask_geography_4326`
TO `account users`{except_clause}
MATCH COLUMNS (
  has_tag('sensitivity_category')
  AND has_tag('sensitivity')
  AND has_tag_value('data_type', 'geography')
) AS c
ON COLUMN c
USING COLUMNS (
  get_column_tag_value(c, 'sensitivity_category'),
  get_column_tag_value(c, 'sensitivity')
)"""

    # 4. Row filter by dimension
    if dimensions:
        match_branches = "\n  OR ".join(
            f"has_tag_value('row_filter', '{dim}')" for dim in dimensions
        )
        statements["rf_by_dimension"] = f"""CREATE OR REPLACE POLICY rf_by_dimension ON CATALOG `{cat}`
COMMENT 'Catalog backup: App-managed row filter over row_filter dimensions: {", ".join(dimensions)}'
ROW FILTER `{app_cat}`.`{schema}`.`has_row_access`
TO `account users`{except_clause}
MATCH COLUMNS (
  {match_branches}
) AS c
USING COLUMNS (c, get_column_tag_value(c, 'row_filter'))"""

    return statements


def cmd_apply(args: argparse.Namespace) -> int:
    catalog = args.catalog.strip().strip("`")
    statements = build_catalog_policy_statements(
        catalog,
        app_catalog=args.app_catalog,
        udf_schema=args.udf_schema,
        except_principals=args.except_principals,
        row_filter_dimensions=args.dimensions,
    )

    print(f"Applying {len(statements)} backup ABAC policies to CATALOG `{catalog}`...")
    if args.dry_run:
        print("\n--- DRY RUN: SQL statements to execute ---\n")
        for name, sql in statements.items():
            print(f"-- Policy: {name}")
            print(sql.strip())
            print(";\n")
        return 0

    sql = DatabricksSql(args.profile, args.warehouse_id)
    success = 0
    for name, statement in statements.items():
        print(f"  -> Creating policy `{name}` ON CATALOG `{catalog}`... ", end="", flush=True)
        try:
            sql.execute(statement)
            print("OK")
            success += 1
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED: {exc}")

    print(f"\nDone: {success}/{len(statements)} policies applied to catalog `{catalog}`.")
    return 0 if success == len(statements) else 1


def cmd_drop(args: argparse.Namespace) -> int:
    catalog = args.catalog.strip().strip("`")
    print(f"Dropping catalog-level ABAC policies from CATALOG `{catalog}`...")

    if args.dry_run:
        print("\n--- DRY RUN: SQL statements to execute ---\n")
        for name in POLICY_NAMES:
            print(f"DROP POLICY {name} ON CATALOG `{catalog}`;")
        return 0

    sql = DatabricksSql(args.profile, args.warehouse_id)
    for name in POLICY_NAMES:
        print(f"  -> Dropping policy `{name}` ON CATALOG `{catalog}`... ", end="", flush=True)
        drop_stmt = f"DROP POLICY {name} ON CATALOG `{catalog}`"
        try:
            sql.execute(drop_stmt)
            print("DROPPED")
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "does not exist" in msg.lower() or "not found" in msg.lower():
                print("NOT PRESENT")
            else:
                print(f"FAILED: {msg}")
    print(f"\nDone cleaning catalog `{catalog}`.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    catalog = args.catalog.strip().strip("`")
    print(f"Checking policies on CATALOG `{catalog}`...\n")

    sql = DatabricksSql(args.profile, args.warehouse_id)
    query = f"""
    SELECT policy_name, policy_type, on_securable_type, securable_name, to_principals, when_condition
    FROM system.information_schema.abac_policy_definitions
    WHERE (securable_name = '{catalog}' AND on_securable_type = 'CATALOG')
       OR (on_securable_type = 'METASTORE')
    ORDER BY on_securable_type, policy_name
    """
    try:
        rows = sql.execute(query)
        if not rows:
            print("No active ABAC policies found for this catalog or metastore.")
            return 0

        print(f"{'SCOPE':<12} {'NAME':<30} {'TYPE':<14} {'TARGET':<20}")
        print("-" * 80)
        for row in rows:
            p_name, p_type, on_type, sec_name, _, _ = row
            target = sec_name or "(metastore)"
            print(f"{on_type:<12} {p_name:<30} {p_type:<14} {target:<20}")
    except Exception as exc:  # noqa: BLE001
        print(f"Could not query abac_policy_definitions: {exc}")
        print("Falling back to SHOW EFFECTIVE POLICIES...")
        try:
            rows = sql.execute(f"SHOW EFFECTIVE POLICIES ON CATALOG `{catalog}`")
            for row in rows:
                print(row)
        except Exception as inner:  # noqa: BLE001
            print(f"SHOW EFFECTIVE POLICIES failed: {inner}")
            return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="action", help="Action to perform")

    def add_common_args(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "-c",
            "--catalog",
            required=True,
            help="Target catalog name (e.g. data_access_control)",
        )
        p.add_argument(
            "-p",
            "--profile",
            default=DEFAULT_PROFILE,
            help=f"Databricks CLI profile (default: {DEFAULT_PROFILE})",
        )
        p.add_argument(
            "-w",
            "--warehouse-id",
            default=DEFAULT_WAREHOUSE_ID,
            help=f"SQL warehouse id (default: {DEFAULT_WAREHOUSE_ID})",
        )
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="Print generated SQL statements without executing",
        )

    # Apply subcommand
    apply_parser = subparsers.add_parser("apply", help="Apply ABAC policies to catalog")
    add_common_args(apply_parser)
    apply_parser.add_argument(
        "--app-catalog",
        default=DEFAULT_APP_CATALOG,
        help=f"Catalog containing governance UDFs (default: {DEFAULT_APP_CATALOG})",
    )
    apply_parser.add_argument(
        "--udf-schema",
        default=DEFAULT_UDF_SCHEMA,
        help=f"Schema containing governance UDFs (default: {DEFAULT_UDF_SCHEMA})",
    )
    apply_parser.add_argument(
        "--except-principals",
        nargs="*",
        default=None,
        help="Principals to exclude from masking/filters (defaults to App SP and Admin SP)",
    )
    apply_parser.add_argument(
        "--dimensions",
        nargs="*",
        default=None,
        help="Row filter dimension names (default: disaster_number org state)",
    )

    # Drop subcommand
    drop_parser = subparsers.add_parser("drop", help="Drop ABAC policies from catalog")
    add_common_args(drop_parser)

    # Status subcommand
    status_parser = subparsers.add_parser(
        "status", help="Show active metastore and catalog policies"
    )
    add_common_args(status_parser)

    # Default to apply if catalog passed as flag
    args = parser.parse_args()
    if not args.action:
        parser.print_help()
        return 1

    if args.action == "apply":
        return cmd_apply(args)
    if args.action == "drop":
        return cmd_drop(args)
    if args.action == "status":
        return cmd_status(args)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
