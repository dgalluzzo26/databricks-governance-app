from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from backend.app.clients.auth import AuthStrategy, build_auth_strategy
from backend.app.clients.cache import TtlCache
from backend.app.clients.secrets import hydrate_admin_secrets
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
        # UC / SQL metadata uses the Account Admin SP when configured.
        sql = SqlExecutor(workspace.admin, config.warehouse_id)
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
        self._ensure_admin_credentials()
        try:
            self.storage.initialize()
        except Exception:
            # Keep serving so the UI can show the storage problem instead of a dead app.
            logger.exception("Storage backend %s failed to initialize", self.storage.kind)

    def shutdown(self) -> None:
        self.storage.close()

    def _ensure_admin_credentials(self) -> None:
        """Load Account Admin SP from the secret scope when not already injected."""
        os.environ.setdefault("ADMIN_SECRET_SCOPE", self.config.admin_secret_scope)
        loaded = hydrate_admin_secrets()
        self.workspace.invalidate_admin()
        if self.workspace.admin_configured:
            logger.info(
                "Account Admin SP ready (scope=%s, source=%s)",
                self.config.admin_secret_scope,
                ",".join(loaded) if loaded else "env",
            )
        else:
            logger.warning(
                "Account Admin SP is not configured; metastore-level ABAC requires secrets in scope %s",
                self.config.admin_secret_scope,
            )
