from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from backend.app.models.app_info import StorageHealth
from backend.app.models.audit import AuditEvent


class SettingsRepository(ABC):
    @abstractmethod
    def get_all(self) -> dict[str, Any]: ...

    @abstractmethod
    def put_many(self, values: dict[str, Any], actor: str) -> None: ...


class AuditRepository(ABC):
    @abstractmethod
    def record(self, event: AuditEvent) -> None: ...

    @abstractmethod
    def list_recent(self, limit: int = 100) -> list[AuditEvent]: ...


class StorageProvider(ABC):
    """Strategy for where app state lives. One provider supplies every repository."""

    kind: str

    @property
    @abstractmethod
    def settings(self) -> SettingsRepository: ...

    @property
    @abstractmethod
    def audit(self) -> AuditRepository: ...

    def initialize(self) -> None:
        """Prepare storage (create schema, open pools). Must be idempotent."""

    @abstractmethod
    def health(self) -> StorageHealth: ...

    def close(self) -> None:
        """Release connections."""
