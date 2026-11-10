from __future__ import annotations

import logging
import os
from dataclasses import dataclass

from backend.app.clients.auth import AuthStrategy, build_auth_strategy
from backend.app.clients.cache import TtlCache
from backend.app.clients.secrets import hydrate_admin_secrets
from backend.app.clients.sql import SqlExecutor
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig, StorageBackend
from backend.app.repositories.base import StorageProvider
from backend.app.repositories.factory import build_storage
from backend.app.services.access_explorer import AccessExplorerService
from backend.app.services.access_requests import AccessRequestService
from backend.app.services.app_info import AppInfoService
from backend.app.services.governed_tags import GovernedTagService
from backend.app.services.identity import IdentityService
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.app.services.identity_uc_sync import IdentityUcSync
from backend.app.services.mask_rules import MaskRuleService
from backend.app.services.policies import PolicyService
from backend.app.services.request_context import RequestContextResolver
from backend.app.services.row_filters import RowFilterService

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
    policies: PolicyService
    identity_attributes: IdentityAttributeService
    access_requests: AccessRequestService
    row_filters: RowFilterService
    mask_rules: MaskRuleService
    access_explorer: AccessExplorerService
    identity_uc_sync: IdentityUcSync | None = None

    @classmethod
    def build(cls, config: AppConfig, storage: StorageProvider | None = None) -> ServiceContainer:
        workspace = WorkspaceClientFactory(config)
        # UC / SQL metadata uses the Account Admin SP when configured.
        sql = SqlExecutor(workspace.admin, config.warehouse_id)
        # App SP owns app Delta state (grants from setup); use it for the UC mirror.
        app_sql = SqlExecutor(workspace.app, config.warehouse_id)
        storage = storage or build_storage(config, workspace, sql)
        identity_cache = TtlCache(config.identity_cache_ttl_seconds)
        metadata_cache = TtlCache(config.metadata_cache_ttl_seconds)
        governed_tags = GovernedTagService(workspace, metadata_cache)
        # Lakebase is OLTP; UC Delta is the enforcement mirror for mask/row-filter UDFs.
        uc_sync = (
            IdentityUcSync(app_sql, config)
            if storage.kind == StorageBackend.LAKEBASE.value
            else None
        )
        identity_attributes = IdentityAttributeService(storage, governed_tags, uc_sync, sql)
        row_filters = RowFilterService(
            config, workspace, sql, storage, governed_tags, identity_attributes, metadata_cache
        )
        mask_rules = MaskRuleService(
            config,
            sql,
            storage,
            governed_tags,
            metadata_cache,
            mirror_sql=app_sql if storage.kind == StorageBackend.LAKEBASE.value else sql,
            workspace=workspace,
        )
        return cls(
            config=config,
            workspace=workspace,
            auth=build_auth_strategy(config.auth_mode, workspace),
            sql=sql,
            storage=storage,
            request_context=RequestContextResolver(config, workspace, identity_cache),
            identity=IdentityService(workspace, identity_cache, config.admin_group),
            app_info=AppInfoService(config, workspace, storage),
            policies=PolicyService(workspace, sql, metadata_cache),
            identity_attributes=identity_attributes,
            access_requests=AccessRequestService(storage, identity_attributes),
            row_filters=row_filters,
            mask_rules=mask_rules,
            access_explorer=AccessExplorerService(
                config, sql, identity_attributes, mask_rules, row_filters
            ),
            identity_uc_sync=uc_sync,
        )

    def startup(self) -> None:
        self._ensure_admin_credentials()
        try:
            self.storage.initialize()
        except Exception:
            # Keep serving so the UI can show the storage problem instead of a dead app.
            logger.exception("Storage backend %s failed to initialize", self.storage.kind)
        if self.identity_uc_sync is not None:
            try:
                self.identity_attributes.sync_to_uc()
            except Exception:
                logger.exception(
                    "Failed to sync identity attributes to UC mirror at %s",
                    self.identity_uc_sync.location,
                )
        try:
            if self.storage.mask_rules.list_rules():
                self.mask_rules.sync_to_uc()
        except Exception:
            logger.exception("Failed to sync mask rules to UC mirror")

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
