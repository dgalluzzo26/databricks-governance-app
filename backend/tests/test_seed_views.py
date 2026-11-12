"""Seed script: views carry object tags only; governance column tags are cleared, never set."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

SEED_PATH = Path(__file__).resolve().parents[2] / "scripts" / "seed_data_access_control_catalog.py"


def _load_seed() -> ModuleType:
    spec = importlib.util.spec_from_file_location("seed_data_access_control_catalog", SEED_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Dataclasses look the module up in sys.modules while it is being executed.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeSql:
    """Records statements; reports leftover governance column tags for any view query."""

    def __init__(self, leftover: list[tuple[str, str]]) -> None:
        self.statements: list[str] = []
        self._leftover = leftover

    def execute(self, statement: str, **_: Any) -> list[list[Any]]:
        self.statements.append(statement)
        if "system.information_schema.column_tags" in statement:
            return [[column, tag] for column, tag in self._leftover]
        return []


def test_build_views_has_no_column_tags() -> None:
    seed = _load_seed()
    views = seed.build_views()
    assert views, "expected at least one cross-domain view"
    for view in views:
        assert not hasattr(view, "column_tags")


def test_create_views_sets_object_tags_and_never_column_tags() -> None:
    seed = _load_seed()
    sql = FakeSql(leftover=[("first_name", "sensitivity"), ("first_name", "mask_rule")])
    seed.create_views(sql, seed.build_views())

    assert any("CREATE OR REPLACE VIEW" in s for s in sql.statements)
    assert any("ALTER VIEW" in s and "SET TAGS" in s for s in sql.statements)
    # No column-level SET TAGS, anywhere, for views.
    # " SET TAGS" (with leading space) excludes "UNSET TAGS".
    assert not any("ALTER COLUMN" in s and " SET TAGS" in s for s in sql.statements)
    # Leftover governance column tags are removed.
    unset = [s for s in sql.statements if "UNSET TAGS" in s]
    assert unset
    assert all("ALTER COLUMN `first_name`" in s for s in unset)
    assert "'mask_rule'" in unset[0] and "'sensitivity'" in unset[0]


def test_clear_view_column_tags_noop_when_clean() -> None:
    seed = _load_seed()
    view = seed.build_views()[0]
    sql = FakeSql(leftover=[])
    seed.clear_view_column_tags(sql, view)
    assert not any("UNSET TAGS" in s for s in sql.statements)
