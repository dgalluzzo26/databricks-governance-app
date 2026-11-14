"""Drive a typical user session against a deployed app and report per-step latency.

Runs N simulated users concurrently, each doing the same read-only session (home, policies,
Access Explorer, Attribute Explorer, approvals inbox, directory search). Approve/deny are
skipped because they change real grants; their history comes from the app's own metrics.

    .venv/bin/python scripts/perf/load_test.py --profile dbc-16303671-e52d --users 1 --rounds 2
    .venv/bin/python scripts/perf/load_test.py --profile dbc-16303671-e52d --users 50 --rounds 1
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import httpx

DEFAULT_URL = "https://data-governance-app-7474655957102538.aws.databricksapps.com"
DEFAULT_TABLE = "dgalluzzo_sandbox_7474655957102538.default.abac_type_test"


@dataclass
class Results:
    samples: dict[str, list[float]] = field(default_factory=lambda: defaultdict(list))
    errors: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, step: str, ms: float, error: str | None) -> None:
        with self.lock:
            self.samples[step].append(ms)
            if error:
                self.errors[step].append(error)


def token_for(profile: str) -> str:
    out = subprocess.run(
        ["databricks", "auth", "token", "--profile", profile], check=True, capture_output=True, text=True
    ).stdout
    return json.loads(out)["access_token"]


class Session:
    def __init__(self, base: str, token: str, results: Results, table: str) -> None:
        self.client = httpx.Client(
            base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=180.0
        )
        self.results = results
        self.table = table

    def call(self, step: str, method: str, path: str, body: Any = None) -> Any:
        started = time.perf_counter()
        error = None
        data: Any = None
        try:
            resp = self.client.request(method, path, json=body)
            if resp.status_code >= 400:
                error = f"HTTP {resp.status_code}: {resp.text[:160]}"
            else:
                data = resp.json() if resp.content else None
        except httpx.HTTPError as exc:
            error = f"{type(exc).__name__}: {exc}"
        self.results.add(step, (time.perf_counter() - started) * 1000, error)
        return data

    def run(self) -> None:
        catalog, schema, _ = self.table.split(".", 2)

        # Home page
        self.call("home: me", "GET", "/api/me")
        self.call("home: my assignments", "GET", "/api/identity/assignments/me")
        self.call("home: my access status", "GET", "/api/data-access/access-status/me")
        self.call("home: my requests", "GET", "/api/identity/access-requests/mine")

        # Policies
        policies = self.call("policies: list", "GET", "/api/policies") or {}
        ids: list[str] = []
        for bucket in ("metastore", "catalog"):
            items = policies.get(bucket) or []
            if items:
                ids.append(items[0]["id"])
        for pid in ids:
            self.call("policies: detail", "GET", f"/api/policies/{pid}")

        # Access Explorer
        self.call("access explorer: tree catalogs", "GET", "/api/access-explorer/tree/catalogs")
        self.call("access explorer: tree schemas", "GET", f"/api/access-explorer/tree/schemas?catalog={catalog}")
        listing = self.call(
            "access explorer: tree tables",
            "GET",
            f"/api/access-explorer/tree/tables?catalog={catalog}&schema={schema}",
        ) or {}
        # Each simulated user opens a different table so the per-table cache is not always warm.
        names = [t["tableName"] for t in listing.get("tables") or []]
        table = f"{catalog}.{schema}.{random.choice(names)}" if names else self.table
        term = table.rsplit(".", 1)[-1][:4] or "abac"
        self.call("access explorer: search", "GET", f"/api/access-explorer/search?q={term}&limit=40")
        self.call("access explorer: analyze table", "POST", "/api/access-explorer/analyze", {"fullName": table})

        # Attribute Explorer
        definitions = self.call("attribute explorer: definitions", "GET", "/api/identity/definitions") or {}
        self.call("attribute explorer: governed tags", "GET", "/api/identity/governed-tags")
        key = next(
            (d.get("key") for d in (definitions.get("definitions") or definitions.get("items") or [])
             if d.get("key") == "data_access"),
            "data_access",
        )
        self.call(
            "attribute explorer: analyze",
            "POST",
            "/api/attribute-explorer/analyze",
            {"attributeKey": key, "attributeValue": catalog},
        )

        # Approvals and directory
        self.call("approvals: inbox", "GET", "/api/identity/approvals")
        self.call("directory: principal search", "GET", "/api/directory/principals?q=dav&limit=20")


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct / 100 * (len(ordered) - 1))))
    return ordered[index]


def report(title: str, results: Results, wall_s: float) -> None:
    print(f"\n=== {title} (wall {wall_s:.1f}s) ===")
    print(f"{'step':40} {'n':>4} {'p50 ms':>8} {'p95 ms':>8} {'max ms':>8} {'errors':>6}")
    for step, values in results.samples.items():
        print(
            f"{step:40} {len(values):>4} {statistics.median(values):>8.0f} "
            f"{percentile(values, 95):>8.0f} {max(values):>8.0f} {len(results.errors.get(step, [])):>6}"
        )
    for step, errs in results.errors.items():
        print(f"  ! {step}: {errs[0]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--table", default=DEFAULT_TABLE)
    parser.add_argument("--users", type=int, default=1)
    parser.add_argument("--rounds", type=int, default=1, help="Sessions per user, run back to back")
    args = parser.parse_args()

    token = token_for(args.profile)
    for round_no in range(1, args.rounds + 1):
        results = Results()
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=args.users) as pool:
            for _ in range(args.users):
                pool.submit(lambda: Session(args.url, token, results, args.table).run())
        report(f"round {round_no}, {args.users} user(s)", results, time.perf_counter() - started)


if __name__ == "__main__":
    main()
