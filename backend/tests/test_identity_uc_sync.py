"""UC identity mirror full resync and container startup mirroring."""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from backend.app.core.config import AppConfig
from backend.app.core.container import ServiceContainer
from backend.app.models.identity_attribute import AttributeDefinition, IdentityAttributeAssignment
from backend.app.models.principal import PrincipalKind
from backend.app.repositories.memory import InMemoryStorageProvider
from backend.app.services import identity_uc_sync
from backend.app.services.identity_uc_sync import IdentityUcSync

NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


class _Sql:
    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute(self, statement: str, params: Any = None) -> list:  # noqa: ARG002
        self.statements.append(" ".join(statement.split()))
        return []


def _sync(sql: _Sql) -> IdentityUcSync:
    return IdentityUcSync(sql, SimpleNamespace(app_catalog="cat", app_schema="app"))  # type: ignore[arg-type]


def _assignment(i: int, **kwargs: Any) -> IdentityAttributeAssignment:
    return IdentityAttributeAssignment(
        assignment_id=f"a{i}",
        principal_name=kwargs.pop("principal", f"user{i}@example.com"),
        principal_type=PrincipalKind.USER,
        attribute_key="data_access",
        attribute_value="finance",
        created_at=NOW,
        **kwargs,
    )


def _source(definitions: list[AttributeDefinition], assignments: list[IdentityAttributeAssignment]):  # noqa: ANN202
    storage = InMemoryStorageProvider()
    repo = storage.identity_attributes
    for definition in definitions:
        repo.upsert_definition(definition)
    for assignment in assignments:
        repo.add_assignment(assignment)
    return repo


def _writes(sql: _Sql) -> list[str]:
    return [s for s in sql.statements if not s.startswith("CREATE TABLE IF NOT EXISTS")]


def test_full_sync_overwrites_each_table_in_one_statement() -> None:
    sql = _Sql()
    source = _source(
        [
            AttributeDefinition(
                key="data_access", governed_tag_keys=["data_access_control"], allowed_values=["x"]
            )
        ],
        [
            _assignment(1, principal="o'brien@example.com", revoked_at=NOW, revoked_by="admin"),
            _assignment(2),
        ],
    )
    counts = _sync(sql).full_sync(source)
    assert counts == {"definitions": 1, "assignments": 2}
    writes = _writes(sql)
    assert len(writes) == 2
    definitions, assignments = writes
    assert definitions.startswith(
        "INSERT OVERWRITE `cat`.`app`.`identity_attribute_definitions` (attribute_key"
    )
    assert "'[]'" in definitions and "'[\"data_access_control\"]'" in definitions
    assert assignments.startswith("INSERT OVERWRITE `cat`.`app`.`identity_attributes` (assignment_id")
    assert "o\\'brien@example.com" in assignments or "o''brien@example.com" in assignments
    assert "CAST('2026-11-13T12:00:00+00:00' AS TIMESTAMP)" in assignments
    assert assignments.count("NULL") >= 2
    assert not any(s.startswith("DELETE") for s in sql.statements)


def test_full_sync_empty_source_clears_atomically() -> None:
    sql = _Sql()
    _sync(sql).full_sync(_source([], []))
    assert _writes(sql) == [
        "INSERT OVERWRITE `cat`.`app`.`identity_attribute_definitions` "
        "SELECT * FROM `cat`.`app`.`identity_attribute_definitions` WHERE false",
        "INSERT OVERWRITE `cat`.`app`.`identity_attributes` "
        "SELECT * FROM `cat`.`app`.`identity_attributes` WHERE false",
    ]


def test_full_sync_large_sources_stage_then_swap(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(identity_uc_sync, "_OVERWRITE_CHUNK", 2)
    sql = _Sql()
    _sync(sql).full_sync(_source([], [_assignment(i) for i in range(5)]))
    staged = [s for s in _writes(sql) if "identity_attributes__staging" in s]
    assert staged[0].startswith("CREATE OR REPLACE TABLE `cat`.`app`.`identity_attributes__staging`")
    assert sum(1 for s in staged if s.startswith("INSERT INTO")) == 3
    assert staged[-2].startswith("INSERT OVERWRITE `cat`.`app`.`identity_attributes` (assignment_id")
    assert staged[-1] == "DROP TABLE IF EXISTS `cat`.`app`.`identity_attributes__staging`"


def test_full_sync_holds_the_mirror_lock() -> None:
    sql = _Sql()
    sync = _sync(sql)
    seen: list[bool] = []

    class _Source:
        def list_definitions(self) -> list:
            seen.append(sync.lock._is_owned())  # type: ignore[attr-defined]  # noqa: SLF001
            return []

        def list_assignments(self, _criteria) -> list:  # noqa: ANN001
            return []

    sync.full_sync(_Source())  # type: ignore[arg-type]
    assert seen == [True]


def _container() -> ServiceContainer:
    config = AppConfig(_env_file=None, STORAGE_BACKEND="memory")  # type: ignore[call-arg]
    return ServiceContainer.build(config, storage=InMemoryStorageProvider())


def test_startup_mirror_runs_inline_in_background_or_not_at_all(monkeypatch) -> None:  # noqa: ANN001
    container = _container()
    calls: list[str] = []
    done = threading.Event()

    def _mirror() -> None:
        calls.append(threading.current_thread().name)
        done.set()

    monkeypatch.setattr(container, "_mirror_to_uc", _mirror)
    monkeypatch.setattr(container, "_ensure_admin_credentials", lambda: None)
    monkeypatch.setattr(container.metrics, "start", lambda: None)
    container.startup(sync_schedule=False, mirror_uc=False)
    assert calls == []
    container.startup(sync_schedule=False)
    assert calls == [threading.current_thread().name]
    done.clear()
    container.startup(sync_schedule=False, mirror_in_background=True)
    assert done.wait(5) and calls[-1] == "uc-mirror-resync"
