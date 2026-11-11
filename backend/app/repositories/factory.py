"""Select and construct the configured storage backend for app state.

Resolves ``AppConfig.storage_backend`` to a Lakebase, Delta, or in-memory
``StorageProvider``. Misconfigured backends surface as ``UnavailableStorageProvider``
so the app can still start and report health.
"""

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
    """Build the storage provider for the configured backend.

    On ``AppError`` during construction (for example missing Lakebase env), returns an
    ``UnavailableStorageProvider`` instead of raising so startup can continue.

    Args:
        config: Application configuration including ``storage_backend``.
        factory: Workspace client factory (used by Lakebase for OAuth DB credentials).
        sql: SQL executor (used by the Delta backend).

    Returns:
        A ready-to-``initialize`` storage provider, or an unavailable stub on config errors.
    """
    backend = config.storage_backend
    try:
        return _BUILDERS[backend](config, factory, sql)
    except AppError as exc:
        logger.error("Storage backend %s is not usable: %s", backend.value, exc.message)
        return UnavailableStorageProvider(backend.value, exc.message)
