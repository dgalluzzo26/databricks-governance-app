from __future__ import annotations

from backend.app import __version__
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.models.app_info import AppInfo, StorageHealth
from backend.app.repositories.base import StorageProvider


class AppInfoService:
    def __init__(self, config: AppConfig, factory: WorkspaceClientFactory, storage: StorageProvider) -> None:
        self._config = config
        self._factory = factory
        self._storage = storage

    def storage_health(self) -> StorageHealth:
        return self._storage.health()

    def info(self) -> AppInfo:
        return AppInfo(
            app_name=self._config.app_name,
            version=__version__,
            workspace_host=self._factory.host,
            auth_mode=self._config.auth_mode.value,
            warehouse_id=self._config.warehouse_id,
            app_catalog=self._config.app_catalog,
            app_schema=self._config.app_schema,
            admin_group=self._config.admin_group,
            storage=self.storage_health(),
        )
