"""Models describing app deployment configuration and storage health."""

from __future__ import annotations

from backend.app.models.base import ApiModel


class StorageHealth(ApiModel):
    """Health of the configured storage backend.

    Attributes:
        backend: Storage backend kind, e.g. ``lakebase``, ``delta`` or ``memory``.
        healthy: True when the backend responded to a health probe.
        detail: Free-text detail: the storage location or a note when healthy, the error
            message when unhealthy; None when the backend has nothing to report.
    """

    backend: str
    healthy: bool
    detail: str | None = None


class AppInfo(ApiModel):
    """Non-secret deployment configuration shown on the app's settings / about page.

    Attributes:
        app_name: Databricks App name.
        version: Backend application version.
        workspace_host: Workspace URL, or None when it could not be resolved.
        auth_mode: How Databricks API calls are authenticated (``service_principal`` or
            ``on_behalf_of_user``).
        warehouse_id: SQL warehouse used for statements, or None when not configured.
        app_catalog: Unity Catalog catalog that holds app-owned objects.
        app_schema: Schema within ``app_catalog`` that holds app-owned tables.
        admin_group: Workspace group whose members are app admins.
        admin_secret_scope: Secret scope from which Account Admin SP credentials are loaded.
        admin_sp_configured: True when Account Admin SP credentials are available.
        storage: Health of the storage backend.
    """

    app_name: str
    version: str
    workspace_host: str | None
    auth_mode: str
    warehouse_id: str | None
    app_catalog: str
    app_schema: str
    admin_group: str
    admin_secret_scope: str
    admin_sp_configured: bool
    storage: StorageHealth
