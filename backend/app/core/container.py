from __future__ import annotations

import logging
from dataclasses import dataclass

from backend.app.clients.auth import AuthStrategy, build_auth_strategy
from backend.app.clients.cache import TtlCache
from backend.app.clients.sql import SqlExecutor
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.repositories.base import StorageProvider
from backend.app.repositories.factory import build_storage
from backend.app.services.app_info import AppInfoService
from backend.app.services.identity import IdentityService
from backend.app.services.request_context import RequestContextResolver

logger = logging.getLogger(__name__)


@dataclass
class ServiceContainer:
    """Wires configuration, clients, storage and services once per process."""

    config: AppConfig
    workspace: WorkspaceClientFactory
    auth: AuthStrategy
    sql: SqlExecutor
    storage: StorageProvider
    request_context: RequestContextResolver
    identity: IdentityService
    app_info: AppInfoService

    @classmethod
    def build(cls, config: AppConfig, storage: StorageProvider | None = None) -> ServiceContainer:
        workspace = WorkspaceClientFactory(config)
        sql = SqlExecutor(workspace.service_principal, config.warehouse_id)
        storage = storage or build_storage(config, workspace, sql)
        identity_cache = TtlCache(config.identity_cache_ttl_seconds)
        return cls(
            config=config,
            workspace=workspace,
            auth=build_auth_strategy(config.auth_mode, workspace),
            sql=sql,
            storage=storage,
            request_context=RequestContextResolver(config, workspace, identity_cache),
            identity=IdentityService(workspace, identity_cache, config.admin_group),
            app_info=AppInfoService(config, workspace, storage),
        )

    def startup(self) -> None:
        try:
            self.storage.initialize()
        except Exception:
            # Keep serving so the UI can show the storage problem instead of a dead app.
            logger.exception("Storage backend %s failed to initialize", self.storage.kind)

    def shutdown(self) -> None:
        self.storage.close()
