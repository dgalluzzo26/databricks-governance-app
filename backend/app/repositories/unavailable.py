from __future__ import annotations

from typing import Any, NoReturn

from backend.app.core.errors import NotConfiguredError
from backend.app.models.app_info import StorageHealth
from backend.app.models.audit import AuditEvent
from backend.app.repositories.base import AuditRepository, SettingsRepository, StorageProvider


class _Unavailable:
    def __init__(self, reason: str) -> None:
        self._reason = reason

    def _fail(self) -> NoReturn:
        raise NotConfiguredError(f"Storage is unavailable: {self._reason}")


class _UnavailableSettings(_Unavailable, SettingsRepository):
    def get_all(self) -> dict[str, Any]:
        self._fail()

    def put_many(self, values: dict[str, Any], actor: str) -> None:
        self._fail()


class _UnavailableAudit(_Unavailable, AuditRepository):
    def record(self, event: AuditEvent) -> None:
        self._fail()

    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        self._fail()


class UnavailableStorageProvider(StorageProvider):
    """Stands in for a backend that could not be built, so the app starts and reports why."""

    def __init__(self, kind: str, reason: str) -> None:
        self.kind = kind
        self._reason = reason
        self._settings = _UnavailableSettings(reason)
        self._audit = _UnavailableAudit(reason)

    @property
    def settings(self) -> SettingsRepository:
        return self._settings

    @property
    def audit(self) -> AuditRepository:
        return self._audit

    def health(self) -> StorageHealth:
        return StorageHealth(backend=self.kind, healthy=False, detail=self._reason)
