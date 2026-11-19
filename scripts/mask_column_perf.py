#!/usr/bin/env python3
"""Measure SELECT * cost as ABAC column masks go from 0 → every column on the table.

Builds a scaled copy of ``abac_type_test`` (source has only 2 rows). Discovers every
column from the source, clears sensitivity tags on the bench table, then re-applies
``sensitivity`` + ``access_path`` (+ ``mask_rule`` / ``data_type``) one column
at a time so the metastore mask policies match progressively more columns.
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass

from databricks.sdk import WorkspaceClient
from databricks.sdk.service.sql import StatementState

PROFILE = "dbc-16303671-e52d"
WAREHOUSE_ID = "21dd266e614f0a03"
CATALOG = "dgalluzzo_sandbox_7474655957102538"
SCHEMA = "default"
SOURCE = f"{CATALOG}.{SCHEMA}.abac_type_test"
BENCH = f"{CATALOG}.{SCHEMA}.abac_type_test_mask_bench"
SINK = f"{CATALOG}.{SCHEMA}.abac_type_test_mask_bench_sink"
N_ROWS = 100_000
WARMUPS = 1
RUNS = 3  # 0..N columns × (warmup+runs) CTAS; keep modest for full-table sweep

# Fallback tag defaults when the source column has no tags yet.
_TYPE_DEFAULTS: dict[str, tuple[str, str]] = {
    "string": ("mask", "string"),
    "int": ("non-string", "int"),
    "integer": ("non-string", "int"),
    "bigint": ("non-string", "bigint"),
    "long": ("non-string", "bigint"),
    "decimal": ("non-string", "decimal"),
    "double": ("non-string", "double"),
    "float": ("non-string", "float"),
    "boolean": ("non-string", "boolean"),
    "bool": ("non-string", "boolean"),
    "date": ("non-string", "date"),
    "timestamp": ("non-string", "timestamp"),
    "timestamp_ntz": ("non-string", "timestamp_ntz"),
    "binary": ("non-string", "binary"),
    "array": ("non-string", "array"),
    "struct": ("non-string", "struct"),
    "map": ("non-string", "map"),
    "geometry": ("non-string", "geometry"),
    "geography": ("non-string", "geography"),
}


@dataclass
class ColSpec:
    name: str
    mask_rule: str
    category: str
    sensitivity: str
    data_type: str


@dataclass
class RunResult:
    masked_columns: int
    columns: list[str]
    client_ms: list[float]
    median_client_ms: float
    mean_client_ms: float
    min_client_ms: float
    max_client_ms: float
    statement_ids: list[str]
    row_count: int | None


def log(msg: str) -> None:
    print(msg, flush=True)


def wait_statement(w: WorkspaceClient, statement_id: str, timeout_s: float = 600.0):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = w.statement_execution.get_statement(statement_id)
        state = res.status.state if res.status else None
        if state in {
            StatementState.SUCCEEDED,
            StatementState.FAILED,
            StatementState.CANCELED,
            StatementState.CLOSED,
        }:
            return res
        time.sleep(0.4)
    raise TimeoutError(statement_id)


def execute(w: WorkspaceClient, sql: str):
    started = time.perf_counter()
    submitted = w.statement_execution.execute_statement(
        warehouse_id=WAREHOUSE_ID,
        statement=sql,
        wait_timeout="0s",
    )
    statement_id = submitted.statement_id
    assert statement_id
    result = wait_statement(w, statement_id)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    state = result.status.state if result.status else None
    if state != StatementState.SUCCEEDED:
        msg = ""
        if result.status and result.status.error:
            msg = result.status.error.message or ""
        raise RuntimeError(f"SQL failed ({state}): {msg}\n{sql[:500]}")
    return result, elapsed_ms, statement_id


def qident(name: str) -> str:
    return f"`{name.replace('`', '')}`"


def _base_type(type_name: str) -> str:
    text = (type_name or "string").strip().lower()
    if text.startswith("decimal"):
        return "decimal"
    if text.startswith("array"):
        return "array"
    if text.startswith("struct"):
        return "struct"
    if text.startswith("map"):
        return "map"
    if text.startswith("geometry"):
        return "geometry"
    if text.startswith("geography"):
        return "geography"
    if text.startswith("timestamp_ntz"):
        return "timestamp_ntz"
    if text.startswith("timestamp"):
        return "timestamp"
    return text.split("(", 1)[0]


def discover_columns(w: WorkspaceClient) -> list[ColSpec]:
    """All columns on the source table, with tag defaults from source tags when present."""
    cols_res, _, _ = execute(
        w,
        f"""
        SELECT column_name, full_data_type
        FROM system.information_schema.columns
        WHERE table_catalog = '{CATALOG}'
          AND table_schema = '{SCHEMA}'
          AND table_name = 'abac_type_test'
        ORDER BY ordinal_position
        """,
    )
    tags_res, _, _ = execute(
        w,
        f"""
        SELECT column_name, tag_name, tag_value
        FROM system.information_schema.column_tags
        WHERE catalog_name = '{CATALOG}'
          AND schema_name = '{SCHEMA}'
          AND table_name = 'abac_type_test'
        """,
    )
    by_col: dict[str, dict[str, str]] = {}
    if tags_res.result and tags_res.result.data_array:
        for column_name, tag_name, tag_value in tags_res.result.data_array:
            by_col.setdefault(str(column_name), {})[str(tag_name)] = str(tag_value)

    specs: list[ColSpec] = []
    rows = cols_res.result.data_array if cols_res.result and cols_res.result.data_array else []
    for column_name, full_data_type in rows:
        name = str(column_name)
        tags = by_col.get(name, {})
        base = _base_type(str(full_data_type))
        default_rule, default_dtype = _TYPE_DEFAULTS.get(base, ("mask", "string"))
        specs.append(
            ColSpec(
                name=name,
                mask_rule=tags.get("mask_rule") or default_rule,
                category=tags.get("access_path") or "finance.budget",
                sensitivity=tags.get("sensitivity") or "PII.Name",
                data_type=tags.get("data_type") or default_dtype,
            )
        )
    if not specs:
        raise RuntimeError(f"No columns found on {SOURCE}")
    return specs


def unset_mask_tags(w: WorkspaceClient, columns: list[str]) -> None:
    for col in columns:
        try:
            execute(
                w,
                f"ALTER TABLE {BENCH} ALTER COLUMN {qident(col)} "
                f"UNSET TAGS ('sensitivity', 'access_path', 'mask_rule', 'data_type')",
            )
        except RuntimeError:
            try:
                execute(
                    w,
                    f"ALTER TABLE {BENCH} ALTER COLUMN {qident(col)} "
                    f"UNSET TAGS ('sensitivity', 'access_path')",
                )
            except RuntimeError as exc:
                log(f"  warn: could not unset tags on {col}: {exc}")


def set_mask_tags(w: WorkspaceClient, spec: ColSpec) -> None:
    execute(
        w,
        f"ALTER TABLE {BENCH} ALTER COLUMN {qident(spec.name)} SET TAGS ("
        f"'sensitivity' = '{spec.sensitivity}', "
        f"'access_path' = '{spec.category}', "
        f"'mask_rule' = '{spec.mask_rule}', "
        f"'data_type' = '{spec.data_type}')",
    )


def ensure_bench(w: WorkspaceClient) -> int:
    log(f"DROP TABLE IF EXISTS {BENCH}")
    execute(w, f"DROP TABLE IF EXISTS {BENCH}")
    execute(w, f"DROP TABLE IF EXISTS {SINK}")
    log(f"CREATE TABLE {BENCH} AS … ({N_ROWS} rows)")
    execute(
        w,
        f"""
        CREATE TABLE {BENCH} AS
        SELECT
          CAST(id AS INT) AS id,
          concat('person_', cast(id AS STRING), '_', cast(rand() AS STRING)) AS s_name,
          concat('open_', cast(id AS STRING)) AS s_open,
          CAST(id AS INT) AS i_val,
          CAST(id AS BIGINT) AS big_val,
          CAST(id AS DECIMAL(18,2)) AS dec_val,
          CAST(id AS DOUBLE) AS dbl_val,
          CAST(id AS FLOAT) AS flt_val,
          CAST((id % 2) = 0 AS BOOLEAN) AS bool_val,
          DATE '2024-01-01' + CAST((id % 365) AS INT) AS dt_val,
          TIMESTAMP '2024-01-01 00:00:00' + CAST((id % 86400) AS INT) * INTERVAL 1 SECOND AS ts_val,
          TIMESTAMP_NTZ '2024-01-01 00:00:00' + CAST((id % 86400) AS INT) * INTERVAL 1 SECOND AS ts_ntz,
          CAST(unhex(md5(cast(id AS STRING))) AS BINARY) AS bin_val,
          array(concat('a', cast(id AS STRING)), concat('b', cast(id AS STRING))) AS arr_val,
          named_struct('a', id, 'b', concat('x', cast(id AS STRING))) AS st_val,
          map('k', id) AS map_val,
          CAST(NULL AS GEOMETRY(4326)) AS geom_val,
          CAST(NULL AS GEOGRAPHY(4326)) AS geog_val,
          concat('user', cast(id AS STRING), '@acme.com') AS email,
          concat('555-010-', lpad(cast(id % 10000 AS STRING), 4, '0')) AS phone,
          concat('pk-', cast(id AS STRING)) AS pk,
          concat('secret-', cast(id AS STRING)) AS full_redact,
          concat('xxxx', lpad(cast(id % 10000 AS STRING), 4, '0')) AS keep_last_4
        FROM (
          SELECT explode(sequence(1, {N_ROWS})) AS id
        )
        """,
    )
    res, _, _ = execute(w, f"SELECT COUNT(*) AS n FROM {BENCH}")
    return int(res.result.data_array[0][0]) if res.result and res.result.data_array else 0


def time_select_star(w: WorkspaceClient, masked_n: int, cols: list[str]) -> RunResult:
    """Time server-side CTAS of SELECT * so every mask UDF runs without client download."""
    times: list[float] = []
    statement_ids: list[str] = []
    sql = f"CREATE OR REPLACE TABLE {SINK} AS SELECT * FROM {BENCH}"
    for i in range(WARMUPS + RUNS):
        _result, elapsed_ms, statement_id = execute(w, sql)
        if i < WARMUPS:
            continue
        times.append(elapsed_ms)
        statement_ids.append(statement_id)
    count_res, _, _ = execute(w, f"SELECT COUNT(*) AS n FROM {SINK}")
    row_count = (
        int(count_res.result.data_array[0][0])
        if count_res.result and count_res.result.data_array
        else None
    )
    return RunResult(
        masked_columns=masked_n,
        columns=list(cols),
        client_ms=times,
        median_client_ms=statistics.median(times),
        mean_client_ms=statistics.mean(times),
        min_client_ms=min(times),
        max_client_ms=max(times),
        statement_ids=statement_ids,
        row_count=row_count,
    )


def main() -> int:
    log(f"Connecting with profile={PROFILE} warehouse={WAREHOUSE_ID}")
    w = WorkspaceClient(profile=PROFILE)
    log(f"source={SOURCE}")
    log("Discovering all columns on source…")
    specs = discover_columns(w)
    log(f"columns={len(specs)}: {', '.join(s.name for s in specs)}")

    log(f"Building scaled bench table ({N_ROWS} rows)…")
    n = ensure_bench(w)
    log(f"Bench row count: {n}")

    names = [s.name for s in specs]
    log("Clearing sensitivity tags on all bench columns (baseline = 0 masked)…")
    unset_mask_tags(w, names)

    results: list[RunResult] = []
    active: list[str] = []

    log("\n=== masked=0 / {0} (baseline) ===".format(len(specs)))
    baseline = time_select_star(w, 0, active)
    results.append(baseline)
    log(
        f"median={baseline.median_client_ms:.0f}ms "
        f"mean={baseline.mean_client_ms:.0f}ms "
        f"min={baseline.min_client_ms:.0f}ms max={baseline.max_client_ms:.0f}ms "
        f"rows={baseline.row_count}"
    )

    for spec in specs:
        set_mask_tags(w, spec)
        active.append(spec.name)
        log(f"\n=== masked={len(active)} / {len(specs)} (+{spec.name} [{spec.mask_rule}/{spec.data_type}]) ===")
        time.sleep(1.5)
        run = time_select_star(w, len(active), active)
        results.append(run)
        delta = run.median_client_ms - baseline.median_client_ms
        per_col = delta / len(active) if active else 0.0
        log(
            f"median={run.median_client_ms:.0f}ms "
            f"mean={run.mean_client_ms:.0f}ms "
            f"min={run.min_client_ms:.0f}ms max={run.max_client_ms:.0f}ms "
            f"delta_vs_0={delta:+.0f}ms "
            f"~{per_col:+.0f}ms/col "
            f"rows={run.row_count}"
        )

    payload = {
        "source": SOURCE,
        "bench": BENCH,
        "rows": n,
        "warmups": WARMUPS,
        "runs": RUNS,
        "warehouse_id": WAREHOUSE_ID,
        "column_order": [asdict(s) for s in specs],
        "results": [asdict(r) for r in results],
    }
    out_path = "scripts/mask_column_perf_results.json"
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    log(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001
        log(f"FATAL: {exc}")
        raise
