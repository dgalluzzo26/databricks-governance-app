from __future__ import annotations

import uuid
from datetime import UTC, datetime

from backend.app.core.config import AppConfig, StorageBackend
from backend.app.models.audit import AuditEvent
from backend.app.repositories.factory import build_storage
from backend.app.repositories.memory import InMemoryStorageProvider


def test_memory_settings_round_trip() -> None:
    storage = InMemoryStorageProvider()
    storage.settings.put_many({"governed_catalogs": ["a", "b"]}, actor="me")
    assert storage.settings.get_all() == {"governed_catalogs": ["a", "b"]}


def test_memory_audit_most_recent_first() -> None:
    storage = InMemoryStorageProvider()
    for minute in (1, 3, 2):
        storage.audit.record(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=f"e{minute}",
                actor="me",
                created_at=datetime(2026, 1, 1, 0, minute, tzinfo=UTC),
            )
        )
    assert [e.event_type for e in storage.audit.list_recent()] == ["e3", "e2", "e1"]


def test_lakebase_without_configuration_reports_unhealthy() -> None:
    config = AppConfig(storage_backend=StorageBackend.LAKEBASE, _env_file=None)
    storage = build_storage(config, factory=None, sql=None)  # type: ignore[arg-type]
    health = storage.health()
    assert health.backend == "lakebase"
    assert health.healthy is False
    assert "PGHOST" in (health.detail or "")
