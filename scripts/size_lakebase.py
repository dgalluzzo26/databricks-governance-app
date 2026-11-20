#!/usr/bin/env python3
"""Set the Lakebase production endpoint's autoscaling range after bundle deploy.

Reads ``lakebase_project_id``, ``lakebase_min_cu`` and ``lakebase_max_cu`` from the bundle
target. Sizing lives here rather than in ``default_endpoint_settings`` on the project resource
because changing that field makes the bundle recreate the app database and role.

Idempotent: no-op when the endpoint already has the requested range.
"""

from __future__ import annotations

import argparse
import json
import sys

from ensure_app_catalog import _bundle_validate, _bundle_vars, _profile_for_target, _run


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-t", "--target", required=True)
    parser.add_argument("--var", action="append", default=[], help="Bundle var overrides (name=value)")
    args = parser.parse_args()

    bundle_args = [f"--var={v}" for v in args.var]
    validated = _bundle_validate(args.target, bundle_args)
    variables = _bundle_vars(validated)
    profile = _profile_for_target(validated, args.target)
    project = variables.get("lakebase_project_id") or "data-governance-app"
    min_cu = float(variables.get("lakebase_min_cu") or 1)
    max_cu = float(variables.get("lakebase_max_cu") or 4)
    if max_cu < min_cu or max_cu - min_cu > 16:
        print(f"Invalid Lakebase range {min_cu}-{max_cu} CU (max - min must be 0-16)", file=sys.stderr)
        return 2

    branch = f"projects/{project}/branches/production"
    endpoints = json.loads(
        _run(["databricks", "postgres", "list-endpoints", branch, "-p", profile, "-o", "json"])
    )
    primary = next(
        (e for e in endpoints if (e.get("status") or {}).get("endpoint_type") == "ENDPOINT_TYPE_READ_WRITE"),
        None,
    )
    if primary is None:
        print(f"No read-write endpoint on {branch}; skipping", file=sys.stderr)
        return 0
    status = primary.get("status") or {}
    current = (
        float(status.get("autoscaling_limit_min_cu") or 0),
        float(status.get("autoscaling_limit_max_cu") or 0),
    )
    if current == (min_cu, max_cu):
        print(f"Lakebase endpoint already {min_cu:g}-{max_cu:g} CU")
        return 0
    _run(
        [
            "databricks",
            "postgres",
            "update-endpoint",
            primary["name"],
            "spec.autoscaling_limit_min_cu,spec.autoscaling_limit_max_cu",
            "--json",
            json.dumps({"spec": {"autoscaling_limit_min_cu": min_cu, "autoscaling_limit_max_cu": max_cu}}),
            "-p",
            profile,
        ]
    )
    print(f"Lakebase endpoint resized {current[0]:g}-{current[1]:g} -> {min_cu:g}-{max_cu:g} CU")
    return 0


if __name__ == "__main__":
    sys.exit(main())
