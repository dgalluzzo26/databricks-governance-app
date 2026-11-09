from __future__ import annotations

import threading

from databricks.sdk import WorkspaceClient

from backend.app.core.config import AppConfig


class WorkspaceClientFactory:
    """Creates workspace clients for the app service principal and for end users.

    On Databricks Apps the service principal credentials come from DATABRICKS_CLIENT_ID /
    DATABRICKS_CLIENT_SECRET; locally they come from the configured CLI profile.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._service_principal: WorkspaceClient | None = None

    def service_principal(self) -> WorkspaceClient:
        if self._service_principal is None:
            with self._lock:
                if self._service_principal is None:
                    self._service_principal = self._build_default()
        return self._service_principal

    def for_user(self, access_token: str) -> WorkspaceClient:
        # auth_type="pat" stops the SDK from also picking up the SP's OAuth env vars.
        return WorkspaceClient(host=self.host, token=access_token, auth_type="pat")

    @property
    def host(self) -> str:
        return self.service_principal().config.host

    def _build_default(self) -> WorkspaceClient:
        if self._config.databricks_profile:
            return WorkspaceClient(profile=self._config.databricks_profile)
        return WorkspaceClient()
