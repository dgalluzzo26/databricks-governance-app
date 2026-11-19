#!/usr/bin/env python3
"""Create the app catalog/schema before bundle deploy when Default Storage is in play.

App telemetry_export_destinations points at ``{app_catalog}.{app_schema}.otel_*``.
Those tables are created on first deploy, but the catalog itself must already exist —
the catalogs REST API rejects create without a storage root on metastores that only
have Default Storage. SQL ``CREATE CATALOG`` succeeds there, so we run it via a
warehouse before ``databricks bundle deploy``.

Idempotent. Safe to re-run.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout or f"command failed: {cmd}")
    return proc.stdout


def _bundle_validate(target: str, bundle_args: list[str]) -> dict[str, Any]:
    raw = _run(["databricks", "bundle", "validate", "-t", target, "-o", "json", *bundle_args])
    return json.loads(raw)


def _bundle_vars(data: dict[str, Any]) -> dict[str, str]:
    variables = data.get("variables") or {}
    out: dict[str, str] = {}
    for key, meta in variables.items():
        if isinstance(meta, dict) and "value" in meta:
            out[key] = str(meta["value"])
        elif isinstance(meta, dict) and "default" in meta:
            out[key] = str(meta["default"])
    return out


def _profile_for_target(data: dict[str, Any], target: str) -> str:
    """Resolve the CLI profile for SQL/API calls.

    ``databricks api`` does not reliably honor ``-t``; it falls back to DEFAULT.
    Bundle targets declare ``workspace.profile`` — use that explicitly.
    """
    workspace = data.get("workspace") or {}
    profile = workspace.get("profile")
    if profile:
        return str(profile)
    # Older validate payloads nest under targets; fall back to the target name.
    return target


def _warehouse_id(target: str, profile: str, bundle_args: list[str]) -> str:
    # Prefer the bound/managed app warehouse when it already exists in the workspace.
    try:
        summary = json.loads(
            _run(["databricks", "bundle", "summary", "-t", target, "-o", "json", *bundle_args])
        )
        wid = (
            ((summary.get("resources") or {}).get("sql_warehouses") or {})
            .get("app_warehouse", {})
            .get("id")
        )
        if wid:
            return str(wid)
    except RuntimeError:
        pass

    warehouses = json.loads(
        _run(["databricks", "warehouses", "list", "--profile", profile, "-o", "json"])
    )
    if isinstance(warehouses, dict):
        warehouses = warehouses.get("warehouses") or []
    if not warehouses:
        raise RuntimeError(
            "No SQL warehouse available to create the app catalog. "
            "Create any warehouse in the workspace, then re-run deploy."
        )
    return str(warehouses[0]["id"])


def _sql(profile: str, warehouse_id: str, statement: str) -> None:
    payload = {"warehouse_id": warehouse_id, "statement": statement, "wait_timeout": "50s"}
    body: dict[str, Any] = json.loads(
        _run(
            [
                "databricks",
                "api",
                "post",
                "/api/2.0/sql/statements",
                "--profile",
                profile,
                "--json",
                json.dumps(payload),
            ]
        )
    )
    deadline = time.time() + 180
    while time.time() < deadline:
        state = (body.get("status") or {}).get("state")
        if state == "SUCCEEDED":
            return
        if state in {"FAILED", "CANCELED", "CLOSED"}:
            err = (body.get("status") or {}).get("error") or body
            raise RuntimeError(f"SQL failed: {err}\n---\n{statement}")
        time.sleep(2)
        sid = body["statement_id"]
        body = json.loads(
            _run(
                [
                    "databricks",
                    "api",
                    "get",
                    f"/api/2.0/sql/statements/{sid}",
                    "--profile",
                    profile,
                ]
            )
        )
    raise TimeoutError(f"Timed out waiting for: {statement}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-t", "--target", required=True)
    parser.add_argument("--var", action="append", default=[], help="Bundle var overrides (name=value)")
    args = parser.parse_args()
    bundle_args: list[str] = []
    for item in args.var:
        bundle_args.extend(["--var", item])

    validated = _bundle_validate(args.target, bundle_args)
    variables = _bundle_vars(validated)
    profile = _profile_for_target(validated, args.target)
    catalog = variables.get("app_catalog") or "data_governance_app"
    schema = variables.get("app_schema") or "app"
    location = (variables.get("catalog_managed_location") or "").strip()

    warehouse_id = _warehouse_id(args.target, profile, bundle_args)
    print(f"Using profile {profile}, warehouse {warehouse_id} for `{catalog}`.`{schema}`")

    location_sql = f" MANAGED LOCATION '{location}'" if location else ""
    _sql(
        profile,
        warehouse_id,
        f"CREATE CATALOG IF NOT EXISTS `{catalog}`{location_sql} "
        f"COMMENT 'Data Governance App state'",
    )
    _sql(
        profile,
        warehouse_id,
        f"CREATE SCHEMA IF NOT EXISTS `{catalog}`.`{schema}` "
        f"COMMENT 'Data Governance App Delta tables'",
    )
    print(f"Ready: `{catalog}`.`{schema}`")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"ensure_app_catalog failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
