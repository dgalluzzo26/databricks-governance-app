"""Process-wide dependency container that builds and owns clients, storage and services."""

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
from backend.app.services.attribute_explorer import AttributeExplorerService
from backend.app.services.data_access import DataAccessService
from backend.app.services.governed_tags import GovernedTagService
from backend.app.services.identity import IdentityService
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.app.services.identity_uc_sync import IdentityUcSync
from backend.app.services.mask_rules import MaskRuleService
from backend.app.services.policies import PolicyService
from backend.app.services.policy_exceptions import PolicyExceptionService
from backend.app.services.recovery import RecoveryService
from backend.app.services.request_context import RequestContextResolver
from backend.app.services.row_filters import RowFilterService

logger = logging.getLogger(__name__)


@dataclass
class ServiceContainer:
    """Wires configuration, clients, storage and services once per process.

    Build with ``ServiceContainer.build``, then call ``startup`` before serving and
    ``shutdown`` on exit.

    Attributes:
        config: Deployment configuration.
        workspace: Factory for app SP, Account Admin SP and per-user workspace clients.
        auth: Strategy choosing which identity per-request API calls run as.
        sql: SQL executor running as the Account Admin SP (falls back to the app SP).
        storage: Storage provider for app state.
        request_context: Resolves the caller's identity from request headers.
        identity: User / group lookups and admin-group membership.
        app_info: Deployment info and storage health.
        policies: Read-only ABAC policy explorer.
        identity_attributes: Attribute definitions and assignments.
        access_requests: Access requests, approvers and renewals.
        row_filters: Row-filter dimensions and their policy pack.
        mask_rules: Mask rules and their policy pack.
        access_explorer: Per-table access analysis.
        attribute_explorer: Attribute path holders and unlocked tables.
        policy_exceptions: Principals exempted from the app-managed policies.
        data_access: Tag-driven group grant sync (settings, readiness, dry-run/apply).
        recovery: Recovery export preview and Git target settings.
        identity_uc_sync: Mirrors identity attributes to Unity Catalog Delta; only set for
            the Lakebase backend, otherwise None.
    """

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
    attribute_explorer: AttributeExplorerService
    policy_exceptions: PolicyExceptionService
    data_access: DataAccessService
    recovery: RecoveryService
    identity_uc_sync: IdentityUcSync | None = None

    @classmethod
    def build(cls, config: AppConfig, storage: StorageProvider | None = None) -> ServiceContainer:
        """Construct all clients and services for ``config``.

        Workspace clients are created lazily on first use; storage initialization and Unity
        Catalog syncs are deferred to ``startup``.

        Args:
            config: Deployment configuration.
            storage: Pre-built storage provider (e.g. for tests); when None, one is built
                from ``config.storage_backend``.

        Returns:
            A fully wired container.
        """
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
        uc_sync = IdentityUcSync(app_sql, config) if storage.kind == StorageBackend.LAKEBASE.value else None
        identity_attributes = IdentityAttributeService(storage, governed_tags, uc_sync, sql)
        policy_exceptions = PolicyExceptionService(storage, workspace)
        row_filters = RowFilterService(
            config,
            workspace,
            sql,
            storage,
            governed_tags,
            identity_attributes,
            metadata_cache,
            policy_exceptions,
        )
        mask_rules = MaskRuleService(
            config,
            sql,
            storage,
            governed_tags,
            metadata_cache,
            mirror_sql=app_sql if storage.kind == StorageBackend.LAKEBASE.value else sql,
            workspace=workspace,
            policy_exceptions=policy_exceptions,
        )
        policy_exceptions._mask_rules = mask_rules  # noqa: SLF001
        policy_exceptions._row_filters = row_filters  # noqa: SLF001
        access_requests = AccessRequestService(storage, identity_attributes)
        data_access = DataAccessService(
            storage,
            sql,
            workspace,
            identity_attributes,
            sync_job_id=config.data_access_sync_job_id,
        )
        attribute_explorer = AttributeExplorerService(
            storage, identity_attributes, access_requests, data_access
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
            access_requests=access_requests,
            row_filters=row_filters,
            mask_rules=mask_rules,
            access_explorer=AccessExplorerService(
                config,
                sql,
                identity_attributes,
                mask_rules,
                row_filters,
                access_requests,
                data_access,
            ),
            attribute_explorer=attribute_explorer,
            policy_exceptions=policy_exceptions,
            data_access=data_access,
            recovery=RecoveryService(config, storage, workspace),
            identity_uc_sync=uc_sync,
        )

    def startup(self, *, migrate_storage: bool = True, sync_schedule: bool = True) -> None:
        """Prepare external state before serving requests or running a sync Job.

        Loads Account Admin SP credentials, initializes storage, and mirrors identity
        attributes and mask rules to Unity Catalog. Failures are logged rather than raised so
        the app still starts and can report the problem in the UI.

        Args:
            migrate_storage: When False, open storage without DDL (sync Job path).
            sync_schedule: When False, skip pushing settings to the Job schedule (Job path).
        """
        self._ensure_admin_credentials()
        try:
            self.storage.initialize(apply_ddl=migrate_storage)
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
        if sync_schedule:
            self.data_access.sync_job_schedule_from_storage()

    def shutdown(self) -> None:
        """Release storage resources such as connection pools."""
        self.storage.close()

    def _ensure_admin_credentials(self) -> None:
        """Load Account Admin SP from the secret scope when not already injected.

        Exports ``ADMIN_SECRET_SCOPE`` if unset, hydrates credentials into the environment,
        drops the cached admin client so it is rebuilt with them, and logs whether
        metastore-level ABAC will be available.
        """
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
