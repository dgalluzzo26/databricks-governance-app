#!/usr/bin/env python3
"""Ensure the catalog enrollment tag and apply it to the governance perimeter catalogs.

Creates/extends the governed tag policy ``data_access_controlled`` (value ``true``) and tags:

  * data_access_control
  * dgalluzzo_sandbox_7474655957102538

Usage:
  python scripts/enroll_governance_catalogs.py --profile dbc-16303671-e52d --warehouse-id dd0a656c3ddc883e
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from typing import Any

TAG_KEY = "data_access_controlled"
TAG_VALUE = "true"
CATALOGS = (
    "data_access_control",
    "dgalluzzo_sandbox_7474655957102538",
)


class DatabricksSql:
    def __init__(self, profile: str, warehouse_id: str) -> None:
        self.profile = profile
        self.warehouse_id = warehouse_id

    def execute(self, statement: str) -> list[list[Any]]:
        proc = subprocess.run(
            [
                "databricks",
                "api",
                "post",
                "/api/2.0/sql/statements",
                "-p",
                self.profile,
                "--json",
                json.dumps(
                    {
                        "warehouse_id": self.warehouse_id,
                        "statement": statement,
                        "wait_timeout": "50s",
                    }
                ),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        body = json.loads(proc.stdout)
        status = ((body.get("status") or {}).get("state")) or ""
        if status == "PENDING" or status == "RUNNING":
            body = self._wait(body["statement_id"])
            status = ((body.get("status") or {}).get("state")) or ""
        if status != "SUCCEEDED":
            raise RuntimeError(json.dumps(body.get("status") or body, indent=2))
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
            state = ((body.get("status") or {}).get("state"))
            if state in {"SUCCEEDED", "FAILED", "CANCELED", "CLOSED"}:
                return body
            time.sleep(2)
        raise TimeoutError(f"Timed out waiting for statement {statement_id}")


def ensure_tag_policy(profile: str) -> None:
    """Best-effort governed tag policy; catalog tags still work ungoverned if quota is full."""
    get_args = [
        "databricks",
        "tag-policies",
        "get-tag-policy",
        TAG_KEY,
        "-p",
        profile,
        "-o",
        "json",
    ]
    proc = subprocess.run(get_args, check=False, capture_output=True, text=True)
    value_objs = [{"name": TAG_VALUE}]
    if proc.returncode != 0:
        create = {
            "tag_key": TAG_KEY,
            "description": "Catalog enrollment for governance perimeter (ABAC WHEN + Access Explorer)",
            "values": value_objs,
        }
        create_proc = subprocess.run(
            [
                "databricks",
                "tag-policies",
                "create-tag-policy",
                "--json",
                json.dumps(create),
                "-p",
                profile,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if create_proc.returncode == 0:
            print(f"created tag policy {TAG_KEY}")
        else:
            print(
                f"skipping governed tag policy for {TAG_KEY} "
                f"(will use ungoverned catalog tags): {create_proc.stderr.strip() or create_proc.stdout.strip()}"
            )
        return

    existing = json.loads(proc.stdout)
    current = {v.get("name") for v in (existing.get("values") or []) if v.get("name")}
    if TAG_VALUE in current:
        print(f"tag policy {TAG_KEY} already has value {TAG_VALUE!r}")
        return
    merged = sorted(current | {TAG_VALUE})
    update = {
        "tag_key": TAG_KEY,
        "description": existing.get("description")
        or "Catalog enrollment for governance perimeter",
        "values": [{"name": v} for v in merged],
    }
    update_proc = subprocess.run(
        [
            "databricks",
            "tag-policies",
            "update-tag-policy",
            TAG_KEY,
            "values,description",
            "--json",
            json.dumps(update),
            "-p",
            profile,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if update_proc.returncode == 0:
        print(f"updated tag policy {TAG_KEY}: added {TAG_VALUE!r}")
    else:
        print(
            f"could not update tag policy {TAG_KEY}; continuing with catalog tags: "
            f"{update_proc.stderr.strip() or update_proc.stdout.strip()}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--warehouse-id", default="dd0a656c3ddc883e")
    args = parser.parse_args()
    try:
        ensure_tag_policy(args.profile)
        sql = DatabricksSql(args.profile, args.warehouse_id)
        for catalog in CATALOGS:
            print(f"tagging catalog {catalog} …")
            sql.execute(
                f"ALTER CATALOG `{catalog}` SET TAGS ('{TAG_KEY}' = '{TAG_VALUE}')"
            )
        rows = sql.execute(
            f"""
            SELECT catalog_name, tag_name, tag_value
            FROM system.information_schema.catalog_tags
            WHERE lower(tag_name) = lower('{TAG_KEY}')
            ORDER BY catalog_name
            """
        )
        print("enrolled catalogs:")
        for row in rows:
            print(f"  {row[0]}: {row[1]}={row[2]}")
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
