"""Tests for scripts/seed_data_governance_demo.py (no workspace access needed)."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

SEED_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_data_governance_demo.py"


def _load_seed() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_data_governance_demo", SEED_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look the module up in sys.modules while it is being executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeSql:
    """Records statements and answers COUNT(*) with a configurable row count."""

    def __init__(self, existing_rows: int = 0) -> None:
        self.statements: list[str] = []
        self._existing_rows = existing_rows

    def execute(self, statement: str, **_: Any) -> list[list[Any]]:
        self.statements.append(statement)
        if statement.startswith("SELECT COUNT(*)"):
            return [[self._existing_rows]]
        return []


@pytest.fixture(scope="module")
def seed() -> ModuleType:
    return _load_seed()


def test_tree_has_12_domains_and_112_subdomains(seed: ModuleType) -> None:
    assert len(seed.ACCESS_TREE) == 12
    assert sum(len(subs) for subs in seed.ACCESS_TREE.values()) == 112


def test_access_paths_cover_domains_and_subdomains(seed: ModuleType) -> None:
    paths = seed.access_paths()
    assert len(paths) == 12 + 112
    assert "finance" in paths
    assert "finance.budget" in paths
    assert paths == sorted(paths)


def test_sensitivity_values_are_classes_and_leaves(seed: ModuleType) -> None:
    values = seed.sensitivity_values()
    assert "PII" in values
    assert "PII.Name" in values
    assert "SPII.SSN" in values
    assert "FIN.BankAcct" in values
    assert len(values) == len(seed.SENSITIVITY_CLASSES) + sum(
        len(leaves) for leaves in seed.SENSITIVITY_CLASSES.values()
    )


def test_catalog_names_use_dga_prefix(seed: ModuleType) -> None:
    assert seed.catalog_name("finance") == "dga_finance"


def test_subdomain_tables_use_new_tag_keys_only(seed: ModuleType) -> None:
    records, summary = seed.subdomain_tables("finance", "budget")
    assert (records.name, summary.name) == ("records", "summary")
    assert records.table_tags == {"access_path": "finance.budget", "data_governance_status": "ready"}
    assert summary.table_tags == records.table_tags

    all_tag_keys = set(records.table_tags) | {key for tags in records.column_tags.values() for key in tags}
    assert "data_access_control" not in all_tag_keys
    assert "sensitivity_category" not in all_tag_keys
    assert {"access_path", "sensitivity", "mask_rule"} <= all_tag_keys


def test_each_domain_has_a_sensitive_focus_column(seed: ModuleType) -> None:
    for domain in seed.ACCESS_TREE:
        focus_col, focus_leaf = seed.DOMAIN_FOCUS[domain]
        records, _ = seed.subdomain_tables(domain, seed.ACCESS_TREE[domain][0])
        assert records.column_tags[focus_col]["sensitivity"] == focus_leaf
        assert focus_leaf in seed.sensitivity_values()


def test_no_view_statements_are_generated(seed: ModuleType) -> None:
    sql = FakeSql()
    seed.build_subdomain(sql, "finance", "budget")
    assert sql.statements, "expected SQL for the subdomain"
    assert not any(re.search(r"\bVIEW\b", stmt.upper()) for stmt in sql.statements)


def test_build_subdomain_skips_insert_when_rows_exist(seed: ModuleType) -> None:
    sql = FakeSql(existing_rows=10)
    seed.build_subdomain(sql, "finance", "budget")
    assert not any(stmt.startswith("INSERT INTO") for stmt in sql.statements)


def test_build_subdomain_inserts_into_empty_tables(seed: ModuleType) -> None:
    sql = FakeSql(existing_rows=0)
    seed.build_subdomain(sql, "finance", "budget")
    inserts = [stmt for stmt in sql.statements if stmt.startswith("INSERT INTO")]
    assert len(inserts) == 2


def test_dry_run_exits_without_workspace_access(seed: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_: Any, **__: Any) -> None:
        raise AssertionError("dry run must not touch the workspace")

    monkeypatch.setattr(seed, "seed", fail)
    monkeypatch.setattr(sys, "argv", ["seed_data_governance_demo.py", "--dry-run", "--domain", "finance"])
    assert seed.main() == 0
