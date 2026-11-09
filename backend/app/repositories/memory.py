from __future__ import annotations

import threading
from typing import Any

from backend.app.models.app_info import StorageHealth
from backend.app.models.audit import AuditEvent
from backend.app.repositories.base import AuditRepository, SettingsRepository, StorageProvider


class InMemorySettingsRepository(SettingsRepository):
    def __init__(self) -> None:
        self._values: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get_all(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._values)

    def put_many(self, values: dict[str, Any], actor: str) -> None:
        with self._lock:
            self._values.update(values)


class InMemoryAuditRepository(AuditRepository):
    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._lock = threading.Lock()

    def record(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        with self._lock:
            return sorted(self._events, key=lambda e: e.created_at, reverse=True)[:limit]


class InMemoryStorageProvider(StorageProvider):
    """Non-persistent storage for local development and tests."""

    kind = "memory"

    def __init__(self) -> None:
        self._settings = InMemorySettingsRepository()
        self._audit = InMemoryAuditRepository()

    @property
    def settings(self) -> SettingsRepository:
        return self._settings

    @property
    def audit(self) -> AuditRepository:
        return self._audit

    def health(self) -> StorageHealth:
        return StorageHealth(
            backend=self.kind, healthy=True, detail="In-memory storage; data is not persisted"
        )
