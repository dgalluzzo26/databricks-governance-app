"""Factory for Databricks workspace clients for each identity the app uses."""

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

    The app and admin clients are built lazily and cached for the process (thread-safe);
    per-user clients are created fresh on every call. Building a client does not by itself
    call the API, but SDK auth resolution may read local config or env vars.

    Args:
        config: Deployment configuration (host, CLI profile, admin credentials).
    """

    def __init__(self, config: AppConfig) -> None:
        self._config = config
        self._lock = threading.Lock()
        self._app: WorkspaceClient | None = None
        self._admin: WorkspaceClient | None = None

    def app(self) -> WorkspaceClient:
        """Return the cached Databricks Apps service principal client.

        Returns:
            Client using default SDK auth, or the configured CLI profile in local development.
        """
        if self._app is None:
            with self._lock:
                if self._app is None:
                    self._app = self._build_app()
        return self._app

    def admin(self) -> WorkspaceClient:
        """Return the cached Account Admin SP client.

        Returns:
            OAuth M2M client for the Account Admin SP, or the app client when admin
            credentials are not configured.
        """
        if self._admin is None:
            with self._lock:
                if self._admin is None:
                    self._admin = self._build_admin()
        return self._admin

    def service_principal(self) -> WorkspaceClient:
        """Backward-compatible alias: UC work uses the Account Admin SP when available.

        Returns:
            The same client as ``admin()``.
        """
        return self.admin()

    def for_user(self, access_token: str) -> WorkspaceClient:
        """Create a new client that acts as the end user (not cached).

        Args:
            access_token: User OAuth token forwarded by the Databricks Apps proxy.

        Returns:
            Client authenticated with the user's token against the app's workspace host.
        """
        # auth_type="pat" stops the SDK from also picking up the SP's OAuth env vars.
        return WorkspaceClient(host=self.host, token=access_token, auth_type="pat")

    @property
    def host(self) -> str:
        """Workspace URL as resolved by the app client."""
        return self.app().config.host

    @property
    def admin_configured(self) -> bool:
        """Whether Account Admin SP client ID and secret are available (config or env)."""
        return bool(self._admin_credentials()[0] and self._admin_credentials()[1])

    @property
    def app_client_id(self) -> str | None:
        """Application ID of the Databricks Apps SP, or None (e.g. local PAT / profile auth)."""
        return (os.environ.get("DATABRICKS_CLIENT_ID") or self.app().config.client_id or "").strip() or None

    @property
    def admin_client_id(self) -> str | None:
        """Application ID of the Account Admin SP, or None when not configured."""
        return self._admin_credentials()[0] or None

    def invalidate_admin(self) -> None:
        """Drop the cached admin client so the next call picks up freshly hydrated credentials."""
        with self._lock:
            self._admin = None

    def _admin_credentials(self) -> tuple[str, str]:
        """Return the Account Admin SP ``(client_id, client_secret)``.

        Config values win over ``ADMIN_CLIENT_ID`` / ``ADMIN_CLIENT_SECRET`` env vars, which
        are read live so secrets hydrated after startup are picked up.

        Returns:
            Stripped client ID and secret; empty strings when unset.
        """
        client_id = (self._config.admin_client_id or os.environ.get("ADMIN_CLIENT_ID") or "").strip()
        client_secret = (
            self._config.admin_client_secret or os.environ.get("ADMIN_CLIENT_SECRET") or ""
        ).strip()
        return client_id, client_secret

    def _build_app(self) -> WorkspaceClient:
        """Build the app client from the CLI profile, if set, else default SDK auth."""
        if self._config.databricks_profile:
            return WorkspaceClient(profile=self._config.databricks_profile)
        return WorkspaceClient()

    def _build_admin(self) -> WorkspaceClient:
        """Build the Account Admin SP client, falling back to the app client with a warning.

        Returns:
            OAuth M2M client against the configured host (or the app client's host), or the
            app client when admin credentials are missing.
        """
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
