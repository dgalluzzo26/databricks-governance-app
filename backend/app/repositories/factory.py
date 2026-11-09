from __future__ import annotations

import logging
from collections.abc import Callable

from backend.app.clients.sql import SqlExecutor
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig, StorageBackend
from backend.app.core.errors import AppError
from backend.app.repositories.base import StorageProvider
from backend.app.repositories.delta import DeltaStorageProvider
from backend.app.repositories.lakebase import LakebaseStorageProvider
from backend.app.repositories.memory import InMemoryStorageProvider
from backend.app.repositories.unavailable import UnavailableStorageProvider

logger = logging.getLogger(__name__)

_Builder = Callable[[AppConfig, WorkspaceClientFactory, SqlExecutor], StorageProvider]

_BUILDERS: dict[StorageBackend, _Builder] = {
    StorageBackend.LAKEBASE: lambda cfg, factory, _sql: LakebaseStorageProvider(cfg, factory),
    StorageBackend.DELTA: lambda cfg, _factory, sql: DeltaStorageProvider(
        sql, cfg.app_catalog, cfg.app_schema
    ),
    StorageBackend.MEMORY: lambda _cfg, _factory, _sql: InMemoryStorageProvider(),
}


def build_storage(config: AppConfig, factory: WorkspaceClientFactory, sql: SqlExecutor) -> StorageProvider:
    backend = config.storage_backend
    try:
        return _BUILDERS[backend](config, factory, sql)
    except AppError as exc:
        logger.error("Storage backend %s is not usable: %s", backend.value, exc.message)
        return UnavailableStorageProvider(backend.value, exc.message)
