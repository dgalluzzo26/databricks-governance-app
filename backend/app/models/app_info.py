from __future__ import annotations

from backend.app.models.base import ApiModel


class StorageHealth(ApiModel):
    backend: str
    healthy: bool
    detail: str | None = None


class AppInfo(ApiModel):
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
