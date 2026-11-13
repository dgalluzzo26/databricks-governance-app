#!/usr/bin/env python3
"""Grants-sync performance test driver.

Seeds one-column tables in ``data_access_control.perf_test`` tagged with existing
``data_access_control`` values (all reviewed) and data-access Identity assignments for existing
account users and groups, written straight to Lakebase so the sync does all membership work.
Runs the deployed sync Job and prints where each run spent its time.

Stages (cumulative):

    stage  tables  assignments
      1     2500       250
      2     3000       375
      3     3500       500
      4     4000       625
      5     4500       750
      6     5000       875

Usage::

    .venv/bin/python scripts/perf_grants.py pause            # stop the schedule (survives deploys)
    .venv/bin/python scripts/perf_grants.py configure --grant-concurrency 8 \
        --membership-concurrency 8 --autoscale on --max-clusters 8 --baseline-clusters 4
    .venv/bin/python scripts/perf_grants.py principals       # cache account users/groups
    .venv/bin/python scripts/perf_grants.py seed --stage 1 --generation 2   # new assignment IDs
    .venv/bin/python scripts/perf_grants.py run --mode dry_run --label stage1
    .venv/bin/python scripts/perf_grants.py run --mode apply --label stage1
    .venv/bin/python scripts/perf_grants.py report           # every recorded result
    .venv/bin/python scripts/perf_grants.py cleanup          # revoke perf assignments (then apply)
    .venv/bin/python scripts/perf_grants.py revoke-grants    # REVOKE dac_* grants on perf_test (keep tables)
    .venv/bin/python scripts/perf_grants.py probe --levels 2,4,8,12,16 --statements 80
    .venv/bin/python scripts/perf_grants.py drop             # drop the perf schema

Two identities are used: the CLI profile (your user) creates tables and triggers the Job; the
Account Admin SP (secrets scope) lists account principals and reads/writes Lakebase.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from databricks.sdk import WorkspaceClient  # noqa: E402
from databricks.sdk.service.jobs import CronSchedule, JobSettings, PauseStatus  # noqa: E402
from databricks.sdk.service.sql import StatementState  # noqa: E402

PROFILE = "dbc-16303671-e52d"
WAREHOUSE_ID = "dd0a656c3ddc883e"
JOB_NAME = "data-governance-app-data-access-sync"
SECRET_SCOPE = "databricks-governance-app"
CATALOG = "data_access_control"
SCHEMA = "perf_test"
ACCESS_TAG = "data_access_control"
REVIEW_TAG = "clr_review_status"
ASSIGNMENT_PREFIX = "perf-"
PERF_ACTOR = "perf-test"
STAGE_TABLES = (2500, 3000, 3500, 4000, 4500, 5000)
STAGE_ASSIGNMENTS = (250, 375, 500, 625, 750, 875)
SKIP_VALUES = {"public", "non-public"}
SKIP_GROUPS = {"users", "admins", "account users"}
OUT = REPO / ".perf"
SQL_WORKERS = 16
BATCH = 25


def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


class Clients:
    """User client (profile) plus Admin SP client and Lakebase storage, built lazily."""

    def __init__(self, profile: str) -> None:
        # The user client must be built before Admin SP env vars are set, or the SDK sees two
        # auth methods.
        self.user = WorkspaceClient(profile=profile)
        self._admin: WorkspaceClient | None = None
        self._container: Any = None

    def admin(self) -> WorkspaceClient:
        if self._admin is None:
            from backend.app.clients.secrets import decode_secret_value

            values = {
                key: decode_secret_value(self.user.secrets.get_secret(scope=SECRET_SCOPE, key=key).value)
                for key in ("client_id", "client_secret")
            }
            host = self.user.config.host
            os.environ.pop("DATABRICKS_CONFIG_PROFILE", None)
            os.environ.update(
                {
                    "DATABRICKS_HOST": host,
                    "DATABRICKS_CLIENT_ID": values["client_id"],
                    "DATABRICKS_CLIENT_SECRET": values["client_secret"],
                    "ADMIN_CLIENT_ID": values["client_id"],
                    "ADMIN_CLIENT_SECRET": values["client_secret"],
                }
            )
            self._admin = WorkspaceClient(
                host=host, client_id=values["client_id"], client_secret=values["client_secret"]
            )
        return self._admin

    def container(self) -> Any:
        """Service container on Lakebase as the Admin SP (same identity as the sync Job)."""
        if self._container is None:
            self.admin()
            os.environ.setdefault("STORAGE_BACKEND", "lakebase")
            os.environ.setdefault("DATABRICKS_WAREHOUSE_ID", WAREHOUSE_ID)
            from backend.app.core.config import get_config
            from backend.app.core.container import ServiceContainer
            from backend.jobs.run_data_access_sync import ensure_lakebase_env

            ensure_lakebase_env()
            get_config.cache_clear()
            container = ServiceContainer.build(get_config())
            container.storage.initialize(apply_ddl=False)
            self._container = container
        return self._container


# SQL as the user.


def run_sql(w: WorkspaceClient, statement: str) -> list[list[Any]]:
    resp = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID, statement=statement, wait_timeout="50s"
    )
    while resp.status and resp.status.state in (StatementState.PENDING, StatementState.RUNNING):
        time.sleep(1)
        resp = w.statement_execution.get_statement(resp.statement_id)
    state = resp.status.state if resp.status else None
    if state != StatementState.SUCCEEDED:
        error = resp.status.error.message if resp.status and resp.status.error else state
        raise RuntimeError(f"{error}\n--- {statement[:300]}")
    return list((resp.result.data_array if resp.result else None) or [])


def access_values(w: WorkspaceClient) -> list[str]:
    """Existing governed values for the access tag, minus demo-only ones."""
    policy = w.tag_policies.get_tag_policy(ACCESS_TAG)
    values = sorted(v.name for v in (policy.values or []) if v.name and v.name not in SKIP_VALUES)
    if not values:
        raise RuntimeError(f"Tag policy {ACCESS_TAG} has no values")
    return values


def table_name(i: int) -> str:
    return f"t{i:05d}"


def table_value(i: int, values: list[str]) -> str:
    return values[(i - 1) % len(values)]


def existing_tables(w: WorkspaceClient) -> set[str]:
    rows = run_sql(
        w,
        f"SELECT table_name FROM system.information_schema.table_tags "
        f"WHERE catalog_name = '{CATALOG}' AND schema_name = '{SCHEMA}' "
        f"AND lower(tag_name) = '{REVIEW_TAG}'",
    )
    return {str(r[0]) for r in rows}


def _table_statements(i: int, values: list[str]) -> list[str]:
    fqn = f"`{CATALOG}`.`{SCHEMA}`.`{table_name(i)}`"
    return [
        f"CREATE TABLE IF NOT EXISTS {fqn} (id INT)",
        f"ALTER TABLE {fqn} SET TAGS ('{ACCESS_TAG}' = '{table_value(i, values)}', "
        f"'{REVIEW_TAG}' = 'reviewed')",
    ]


def _create_batch(w: WorkspaceClient, indexes: list[int], values: list[str], scripting: bool) -> int:
    statements = [s for i in indexes for s in _table_statements(i, values)]
    if scripting:
        run_sql(w, "BEGIN\n" + ";\n".join(statements) + ";\nEND")
    else:
        for statement in statements:
            run_sql(w, statement)
    return len(indexes)


def seed_tables(w: WorkspaceClient, target: int) -> None:
    values = access_values(w)
    run_sql(w, f"CREATE SCHEMA IF NOT EXISTS `{CATALOG}`.`{SCHEMA}`")
    have = existing_tables(w)
    todo = [i for i in range(1, target + 1) if table_name(i) not in have]
    log(f"tables: {len(have)} exist, {len(todo)} to create (target {target}, {len(values)} values)")
    if not todo:
        return
    batches = [todo[k : k + BATCH] for k in range(0, len(todo), BATCH)]
    scripting = True
    try:
        _create_batch(w, batches[0], values, scripting=True)
    except RuntimeError as exc:
        log(f"SQL scripting batch failed, falling back to one statement per call: {exc}")
        scripting = False
        _create_batch(w, batches[0], values, scripting=False)
    done, started = len(batches[0]), time.perf_counter()
    with ThreadPoolExecutor(max_workers=SQL_WORKERS) as pool:
        futures = [pool.submit(_create_batch, w, b, values, scripting) for b in batches[1:]]
        for future in as_completed(futures):
            done += future.result()
            if done % 250 < BATCH:
                log(f"  {done}/{len(todo)} tables ({time.perf_counter() - started:.0f}s)")
    log(f"tables: created {len(todo)} in {time.perf_counter() - started:.0f}s")


# Principals and assignments as the Admin SP.


def _scim_pages(admin: WorkspaceClient, path: str, attributes: str) -> list[dict]:
    items: list[dict] = []
    start = 1
    while True:
        page = admin.api_client.do(
            "GET", path, query={"attributes": attributes, "startIndex": start, "count": 100}
        )
        resources = list(page.get("Resources") or [])
        items.extend(resources)
        total = int(page.get("totalResults") or 0)
        if not resources or len(items) >= total:
            return items
        start += len(resources)


def cache_principals(clients: Clients) -> dict[str, list[str]]:
    admin = clients.admin()
    users = [
        str(u["userName"])
        for u in _scim_pages(admin, "/api/2.0/account/scim/v2/Users", "id,userName,active")
        if u.get("userName") and u.get("active", True)
    ]
    groups = [
        str(g["displayName"])
        for g in _scim_pages(admin, "/api/2.0/account/scim/v2/Groups", "id,displayName")
        if g.get("displayName")
        and not str(g["displayName"]).lower().startswith("dac_")
        and str(g["displayName"]).lower() not in SKIP_GROUPS
    ]
    principals = {"users": sorted(users), "groups": sorted(groups)}
    OUT.mkdir(exist_ok=True)
    (OUT / "principals.json").write_text(json.dumps(principals, indent=2))
    log(f"principals: {len(users)} users, {len(groups)} groups → {OUT / 'principals.json'}")
    return principals


def load_principals(clients: Clients) -> dict[str, list[str]]:
    path = OUT / "principals.json"
    return json.loads(path.read_text()) if path.exists() else cache_principals(clients)


def planned_assignments(
    count: int, principals: dict[str, list[str]], values: list[str], generation: int = 1
) -> list[tuple[str, str, str, str]]:
    """Deterministic ``(assignment_id, principal, type, value)`` list; unique principal/value pairs.

    Revoked assignment IDs cannot be reused, so each generation uses its own ID prefix
    (``perf-g{generation}-NNNNN``). ``cleanup`` matches every generation by the ``perf-`` prefix.
    """
    pool = [(u, "USER") for u in principals["users"]] + [(g, "GROUP") for g in principals["groups"]]
    if not pool:
        raise RuntimeError("No account principals to assign")
    # Interleave users and groups so every stage gets a mix.
    users = [p for p in pool if p[1] == "USER"]
    groups = [p for p in pool if p[1] == "GROUP"]
    mixed: list[tuple[str, str]] = []
    for k in range(max(len(users), len(groups))):
        if k < len(users):
            mixed.append(users[k])
        if k < len(groups):
            mixed.append(groups[k])
    out: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    i = 0
    while len(out) < count:
        principal, kind = mixed[i % len(mixed)]
        value = values[(i * 7 + i // len(mixed)) % len(values)]
        if (principal.lower(), value) not in seen:
            seen.add((principal.lower(), value))
            out.append((f"{ASSIGNMENT_PREFIX}g{generation}-{len(out) + 1:05d}", principal, kind, value))
        i += 1
        if i > count * 50:
            raise RuntimeError("Not enough unique principal/value pairs")
    return out


def data_access_key(container: Any) -> str:
    definition = container.identity_attributes.find_data_access_definition(ACCESS_TAG)
    if definition is None:
        raise RuntimeError("No Identity definition linked to the data_access_control tag")
    return definition.key


def seed_assignments(clients: Clients, target: int, generation: int) -> None:
    from backend.app.models.identity_attribute import AttributeSource, IdentityAttributeAssignment
    from backend.app.models.principal import PrincipalKind

    container = clients.container()
    repo = container.storage.identity_attributes
    key = data_access_key(container)
    values = access_values(clients.user)
    planned = planned_assignments(target, load_principals(clients), values, generation)
    now = datetime.now(UTC)
    added = 0
    started = time.perf_counter()
    for assignment_id, principal, kind, value in planned:
        if repo.get_assignment(assignment_id) is not None:
            continue
        repo.add_assignment(
            IdentityAttributeAssignment(
                assignment_id=assignment_id,
                principal_name=principal,
                principal_type=PrincipalKind(kind),
                attribute_key=key,
                attribute_value=value,
                source=AttributeSource.MANUAL,
                granted_by=PERF_ACTOR,
                justification="grants-sync performance test",
                created_at=now,
            )
        )
        added += 1
    users = sum(1 for p in planned if p[2] == "USER")
    log(
        f"assignments: {added} added in {time.perf_counter() - started:.0f}s; "
        f"target {target} ({users} users, {target - users} groups) on key {key}; "
        f"generation {generation}"
    )


# Schedule, runs, and results.


def job_id(w: WorkspaceClient) -> int:
    jobs = [j for j in w.jobs.list(name=JOB_NAME) if j.job_id]
    if not jobs:
        raise RuntimeError(f"Job {JOB_NAME} not found")
    return int(jobs[0].job_id)


def update_settings(clients: Clients, **changes: Any) -> Any:
    """Merge ``changes`` into the stored data-access settings (validated, same as the API)."""
    from backend.app.models.data_access import DataAccessSettings

    container = clients.container()
    raw = container.storage.settings.get_all().get("data_access") or {}
    settings = DataAccessSettings.model_validate(raw).model_copy(update=changes)
    container.storage.settings.put_many({"data_access": settings.model_dump(mode="json")}, PERF_ACTOR)
    return settings


def configure(
    clients: Clients,
    *,
    grant_concurrency: int | None,
    membership_concurrency: int | None,
    autoscale: bool | None,
    max_clusters: int | None,
    baseline_clusters: int | None,
) -> None:
    changes = {
        k: v
        for k, v in {
            "grant_concurrency": grant_concurrency,
            "membership_concurrency": membership_concurrency,
            "warehouse_autoscale": autoscale,
            "warehouse_max_clusters": max_clusters,
            "warehouse_baseline_clusters": baseline_clusters,
        }.items()
        if v is not None
    }
    if not changes:
        raise RuntimeError(
            "configure needs at least one of --grant-concurrency, --membership-concurrency, "
            "--autoscale, --max-clusters, --baseline-clusters"
        )
    settings = update_settings(clients, **changes)
    log(
        f"settings: grant_concurrency={settings.grant_concurrency} "
        f"membership_concurrency={settings.membership_concurrency} "
        f"warehouse_autoscale={settings.warehouse_autoscale} "
        f"warehouse_max_clusters={settings.warehouse_max_clusters} "
        f"warehouse_baseline_clusters={settings.warehouse_baseline_clusters}"
    )


def pause(clients: Clients) -> None:
    update_settings(clients, schedule_enabled=False)
    w = clients.user
    jid = job_id(w)
    job = w.jobs.get(jid)
    cron = job.settings.schedule.quartz_cron_expression if job.settings and job.settings.schedule else None
    w.jobs.update(
        jid,
        new_settings=JobSettings(
            schedule=CronSchedule(
                quartz_cron_expression=cron or "0 0 * * * ?",
                timezone_id="UTC",
                pause_status=PauseStatus.PAUSED,
            )
        ),
    )
    log(f"schedule paused: settings.schedule_enabled=false and Job {jid} PAUSED")


def stored_run(clients: Clients, run_id: str) -> dict | None:
    runs = clients.container().storage.settings.get_all().get("data_access_runs") or []
    return next((r for r in runs if r.get("run_id") == run_id), None)


def run_job(clients: Clients, mode: str, label: str) -> None:
    w = clients.user
    jid = job_id(w)
    run_id = str(uuid.uuid4())
    log(f"run {label} {mode}: starting Job {jid} (run_id {run_id})")
    started = time.perf_counter()
    waiter = w.jobs.run_now(
        jid, job_parameters={"mode": mode, "trigger": "user", "actor": PERF_ACTOR, "run_id": run_id}
    )
    job_run = waiter.result(timeout=timedelta(hours=2))
    wall = time.perf_counter() - started
    state = job_run.state.result_state.value if job_run.state and job_run.state.result_state else "?"
    run = stored_run(clients, run_id) or {}
    result = {
        "label": label,
        "mode": mode,
        "run_id": run_id,
        "job_run_id": job_run.run_id,
        "job_result": state,
        "job_wall_seconds": round(wall, 1),
        "job_setup_seconds": round((job_run.setup_duration or 0) / 1000, 1),
        "job_execution_seconds": round((job_run.execution_duration or 0) / 1000, 1),
        "status": run.get("status"),
        "error": run.get("error"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "summary": run.get("summary"),
        "profile": run.get("profile"),
    }
    out = OUT / "results"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"{label}_{mode}.json"
    path.write_text(json.dumps(result, indent=2, default=str))
    log(f"run {label} {mode}: job {state} in {wall:.0f}s → {path}")
    print_result(result)


def print_result(result: dict) -> None:
    profile = result.get("profile") or {}
    summary = result.get("summary") or {}
    print(
        f"\n== {result['label']} {result['mode']}: {result.get('status')} "
        f"(job wall {result.get('job_wall_seconds')}s, setup {result.get('job_setup_seconds')}s)"
    )
    if result.get("error"):
        print(f"   error: {str(result['error'])[:500]}")
    keys = ("eligible_objects", "grants_to_add", "grants_to_revoke", "members_to_add", "members_to_remove")
    print("   plan: " + ", ".join(f"{k}={summary.get(k)}" for k in keys))
    print("   sizes: " + ", ".join(f"{k}={v}" for k, v in sorted((profile.get("sizes") or {}).items())))
    print("   phases (s):")
    for name, seconds in sorted((profile.get("phases") or {}).items(), key=lambda kv: -kv[1]):
        print(f"     {name:<28} {seconds:>9.2f}")
    calls = profile.get("calls") or {}
    if calls:
        print("   calls:                          n     total s    avg s    max s")
        for name in sorted(calls, key=lambda n: -(profile["call_seconds"].get(n) or 0)):
            n = calls[name]
            total = profile["call_seconds"].get(name, 0.0)
            print(
                f"     {name:<26} {n:>6} {total:>11.2f} {total / n:>8.3f} "
                f"{profile['call_max_seconds'].get(name, 0.0):>8.2f}"
            )
    for message in profile.get("notes") or []:
        print(f"   note: {message}")


def report() -> None:
    for path in sorted((OUT / "results").glob("*.json"), key=lambda p: p.stat().st_mtime):
        print_result(json.loads(path.read_text()))


def cleanup(clients: Clients) -> None:
    container = clients.container()
    repo = container.storage.identity_attributes
    from backend.app.models.identity_attribute import AttributeAssignmentFilter

    key = data_access_key(container)
    now = datetime.now(UTC)
    revoked = 0
    for item in repo.list_assignments(AttributeAssignmentFilter(attribute_key=key, include_inactive=False)):
        if item.assignment_id.startswith(ASSIGNMENT_PREFIX):
            repo.revoke_assignment(item.assignment_id, PERF_ACTOR, now)
            revoked += 1
    log(f"cleanup: revoked {revoked} perf assignments; run an apply to remove memberships")


def revoke_grants(clients: Clients) -> None:
    """REVOKE every ``dac_*`` table and schema privilege on the perf schema (tables/tags kept).

    Run after the cleanup apply: grants follow tags, not memberships, so the sync would otherwise
    keep re-granting them. Starts the retest from zero grants, the same as the stage 1 baseline.
    """
    w = clients.user
    grantee = "grantee LIKE 'dac%'"
    tables = run_sql(
        w,
        f"SELECT table_name, privilege_type, grantee FROM system.information_schema.table_privileges "
        f"WHERE table_catalog = '{CATALOG}' AND table_schema = '{SCHEMA}' AND {grantee}",
    )
    schemas = run_sql(
        w,
        f"SELECT privilege_type, grantee FROM system.information_schema.schema_privileges "
        f"WHERE catalog_name = '{CATALOG}' AND schema_name = '{SCHEMA}' AND {grantee}",
    )
    statements = [
        f"REVOKE {priv} ON TABLE `{CATALOG}`.`{SCHEMA}`.`{name}` FROM `{principal}`"
        for name, priv, principal in tables
    ] + [f"REVOKE {priv} ON SCHEMA `{CATALOG}`.`{SCHEMA}` FROM `{principal}`" for priv, principal in schemas]
    log(
        f"revoke: {len(statements)} dac_* grants on {CATALOG}.{SCHEMA} "
        f"({len(tables)} table, {len(schemas)} schema)"
    )
    started = time.perf_counter()
    done, failed = 0, 0
    with ThreadPoolExecutor(max_workers=SQL_WORKERS) as pool:
        futures = [pool.submit(run_sql, w, statement) for statement in statements]
        for future in as_completed(futures):
            try:
                future.result()
            except RuntimeError as exc:
                failed += 1
                log(f"  revoke failed: {str(exc).splitlines()[0][:200]}")
            done += 1
            if done % 250 == 0:
                log(f"  {done}/{len(statements)} revoked ({time.perf_counter() - started:.0f}s)")
    log(f"revoke: {done - failed} revoked, {failed} failed in {time.perf_counter() - started:.0f}s")
    if failed:
        raise RuntimeError(f"{failed} revokes failed; re-run revoke-grants before the retest")


def probe_concurrency(clients: Clients, *, levels: list[int], statements: int) -> None:
    """Measure GRANT throughput at each concurrency on the current warehouse (no scaling).

    Uses no-op GRANTs on existing perf tables so the work matches the sync, then writes
    ``.perf/results/probe_concurrency.json``. Keep ``max_num_clusters=1`` while probing so the
    numbers are per-cluster capacity for the current size.
    """
    w = clients.user
    wh = w.warehouses.get(WAREHOUSE_ID)
    size = str(wh.cluster_size)
    clusters = int(wh.max_num_clusters or 1)
    log(f"probe: warehouse {WAREHOUSE_ID} size={size} max_num_clusters={clusters}")
    if clusters != 1:
        log("probe: max_num_clusters is not 1; results mix horizontal scale into the reading")

    # Cycle across the first N tables so statements stay cheap UC metadata work.
    tables = [f"`{CATALOG}`.`{SCHEMA}`.`{table_name(i)}`" for i in range(1, min(statements, 500) + 1)]
    if len(tables) < statements:
        tables = (tables * ((statements // len(tables)) + 1))[:statements]
    principal = "`dac_finance`"

    def one(i: int) -> float:
        started = time.perf_counter()
        run_sql(w, f"GRANT SELECT ON TABLE {tables[i % len(tables)]} TO {principal}")
        return time.perf_counter() - started

    results: list[dict[str, Any]] = []
    for workers in levels:
        # Warm the warehouse so the first level is not paying cold start alone.
        one(0)
        started = time.perf_counter()
        latencies: list[float] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, i) for i in range(statements)]
            for future in as_completed(futures):
                latencies.append(future.result())
        wall = time.perf_counter() - started
        latencies.sort()
        p50 = latencies[len(latencies) // 2]
        p95 = latencies[max(0, int(len(latencies) * 0.95) - 1)]
        row = {
            "workers": workers,
            "statements": statements,
            "wall_seconds": round(wall, 2),
            "throughput_per_s": round(statements / wall, 2),
            "avg_seconds": round(sum(latencies) / len(latencies), 3),
            "p50_seconds": round(p50, 3),
            "p95_seconds": round(p95, 3),
            "max_seconds": round(latencies[-1], 3),
        }
        results.append(row)
        log(
            f"probe concurrency={workers}: wall={row['wall_seconds']}s "
            f"throughput={row['throughput_per_s']}/s avg={row['avg_seconds']}s "
            f"p50={row['p50_seconds']}s p95={row['p95_seconds']}s max={row['max_seconds']}s"
        )

    out = {
        "warehouse_id": WAREHOUSE_ID,
        "cluster_size": size,
        "max_num_clusters": clusters,
        "levels": results,
    }
    (OUT / "results").mkdir(parents=True, exist_ok=True)
    path = OUT / "results" / "probe_concurrency.json"
    path.write_text(json.dumps(out, indent=2))
    log(f"probe written → {path}")
    # Pick the lowest concurrency whose throughput is within 10% of the best.
    best = max(r["throughput_per_s"] for r in results)
    chosen = next(r for r in results if r["throughput_per_s"] >= best * 0.9)
    log(
        f"probe suggestion: concurrency={chosen['workers']} on {size} × {clusters} "
        f"(throughput {chosen['throughput_per_s']}/s, best {best}/s)"
    )


def drop(clients: Clients) -> None:
    run_sql(clients.user, f"DROP SCHEMA IF EXISTS `{CATALOG}`.`{SCHEMA}` CASCADE")
    log(f"dropped {CATALOG}.{SCHEMA}; run an apply to revoke its grants from history")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command",
        choices=[
            "pause",
            "configure",
            "principals",
            "seed",
            "run",
            "report",
            "cleanup",
            "revoke-grants",
            "probe",
            "drop",
        ],
    )
    parser.add_argument("--profile", default=PROFILE)
    parser.add_argument("--stage", type=int, choices=range(1, 7))
    parser.add_argument("--mode", choices=["dry_run", "apply"], default="dry_run")
    parser.add_argument("--label", default="adhoc")
    parser.add_argument("--tables-only", action="store_true")
    parser.add_argument("--assignments-only", action="store_true")
    parser.add_argument("--generation", type=int, default=1, help="assignment ID generation (seed)")
    parser.add_argument("--grant-concurrency", type=int, choices=range(1, 65), help="configure")
    parser.add_argument("--membership-concurrency", type=int, choices=range(1, 65), help="configure")
    parser.add_argument("--autoscale", choices=["on", "off"], help="configure warehouse autoscale")
    parser.add_argument("--max-clusters", type=int, choices=range(1, 11), help="configure sync ceiling")
    parser.add_argument(
        "--baseline-clusters", type=int, choices=range(1, 11), help="configure post-sync ceiling"
    )
    parser.add_argument(
        "--levels",
        default="2,4,8,12,16",
        help="comma-separated concurrencies for probe",
    )
    parser.add_argument("--statements", type=int, default=80, help="statements per probe level")
    args = parser.parse_args()
    if args.command == "report":
        report()
        return 0
    clients = Clients(args.profile)
    if args.command == "pause":
        pause(clients)
    elif args.command == "configure":
        configure(
            clients,
            grant_concurrency=args.grant_concurrency,
            membership_concurrency=args.membership_concurrency,
            autoscale=None if args.autoscale is None else args.autoscale == "on",
            max_clusters=args.max_clusters,
            baseline_clusters=args.baseline_clusters,
        )
    elif args.command == "principals":
        cache_principals(clients)
    elif args.command == "seed":
        if args.stage is None:
            parser.error("seed needs --stage")
        if not args.assignments_only:
            seed_tables(clients.user, STAGE_TABLES[args.stage - 1])
        if not args.tables_only:
            seed_assignments(clients, STAGE_ASSIGNMENTS[args.stage - 1], args.generation)
    elif args.command == "run":
        run_job(clients, args.mode, args.label)
    elif args.command == "cleanup":
        cleanup(clients)
    elif args.command == "revoke-grants":
        revoke_grants(clients)
    elif args.command == "probe":
        levels = [int(x) for x in args.levels.split(",") if x.strip()]
        probe_concurrency(clients, levels=levels, statements=args.statements)
    elif args.command == "drop":
        drop(clients)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
