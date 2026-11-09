from __future__ import annotations

import logging
import os
import threading

from databricks.sdk import WorkspaceClient

from backend.app.core.config import AppConfig

logger = logging.getLogger(__name__)


class WorkspaceClientFactory:
    """Creates workspace clients for the Apps SP, Account Admin SP, and end users.

    - ``app()`` is the Databricks Apps service principal (Lakebase, app-owned resources).
    - ``admin()`` is the Account Admin SP from secret scope / env (Unity Catalog, including
      metastore-level ABAC). Falls back to ``app()`` when admin credentials are not configured.
    - ``for_user()`` runs as the signed-in user with a forwarded access token.
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._app: WorkspaceClient | None = None
        self._admin: WorkspaceClient | None = None

    def app(self) -> WorkspaceClient:
        if self._app is None:
            with self._lock:
                if self._app is None:
                    self._app = self._build_app()
        return self._app

    def admin(self) -> WorkspaceClient:
        if self._admin is None:
            with self._lock:
                if self._admin is None:
                    self._admin = self._build_admin()
        return self._admin

    def service_principal(self) -> WorkspaceClient:
        """Backward-compatible alias: UC work uses the Account Admin SP when available."""
        return self.admin()

    def for_user(self, access_token: str) -> WorkspaceClient:
        # auth_type="pat" stops the SDK from also picking up the SP's OAuth env vars.
        return WorkspaceClient(host=self.host, token=access_token, auth_type="pat")

    @property
    def host(self) -> str:
        return self.app().config.host

    @property
    def admin_configured(self) -> bool:
        return bool(self._admin_credentials()[0] and self._admin_credentials()[1])

    def invalidate_admin(self) -> None:
        """Drop the cached admin client so the next call picks up freshly hydrated credentials."""
        with self._lock:
            self._admin = None

    def _admin_credentials(self) -> tuple[str, str]:
        client_id = (self._config.admin_client_id or os.environ.get("ADMIN_CLIENT_ID") or "").strip()
        client_secret = (
            self._config.admin_client_secret or os.environ.get("ADMIN_CLIENT_SECRET") or ""
        ).strip()
        return client_id, client_secret

    def _build_app(self) -> WorkspaceClient:
        if self._config.databricks_profile:
            return WorkspaceClient(profile=self._config.databricks_profile)
        return WorkspaceClient()

    def _build_admin(self) -> WorkspaceClient:
        client_id, client_secret = self._admin_credentials()
        if not client_id or not client_secret:
            logger.warning(
                "ADMIN_CLIENT_ID / ADMIN_CLIENT_SECRET are not set; "
                "falling back to the app service principal (metastore ABAC will not work)"
            )
            return self.app()

        host = (self._config.databricks_host or os.environ.get("DATABRICKS_HOST") or "").strip()
        host = host or self.app().config.host
        logger.info("Using Account Admin service principal for Unity Catalog access")
        return WorkspaceClient(host=host, client_id=client_id, client_secret=client_secret)
