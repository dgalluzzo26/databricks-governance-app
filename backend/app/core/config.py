from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class StorageBackend(StrEnum):
    LAKEBASE = "lakebase"
    DELTA = "delta"
    MEMORY = "memory"


class AuthMode(StrEnum):
    SERVICE_PRINCIPAL = "service_principal"
    ON_BEHALF_OF_USER = "on_behalf_of_user"


class AppConfig(BaseSettings):
    """Deployment-time configuration, read from environment variables.

    Runtime settings that admins change in the UI live in the storage backend instead
    (see services/settings.py).
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", populate_by_name=True)

    app_name: str = Field("data-governance-app", alias="DATABRICKS_APP_NAME")
    port: int = Field(8000, alias="DATABRICKS_APP_PORT")
    databricks_host: str | None = Field(None, alias="DATABRICKS_HOST")
    databricks_profile: str | None = Field(None, alias="DATABRICKS_CONFIG_PROFILE")
    warehouse_id: str | None = Field(None, alias="DATABRICKS_WAREHOUSE_ID")

    storage_backend: StorageBackend = Field(StorageBackend.MEMORY, alias="STORAGE_BACKEND")
    app_catalog: str = Field("data_governance_app", alias="APP_CATALOG")
    app_schema: str = Field("app", alias="APP_SCHEMA")
    demo_catalog: str | None = Field(None, alias="DEMO_CATALOG")

    pg_host: str | None = Field(None, alias="PGHOST")
    pg_port: int = Field(5432, alias="PGPORT")
    pg_database: str | None = Field(None, alias="PGDATABASE")
    pg_user: str | None = Field(None, alias="PGUSER")
    lakebase_endpoint: str | None = Field(None, alias="LAKEBASE_ENDPOINT")
    lakebase_schema: str = Field("governance_app", alias="LAKEBASE_SCHEMA")

    auth_mode: AuthMode = Field(AuthMode.SERVICE_PRINCIPAL, alias="AUTH_MODE")
    admin_group: str = Field("admins", alias="APP_ADMIN_GROUP")
    # Account Admin SP for Unity Catalog / metastore ABAC (from secret scope or env).
    admin_secret_scope: str = Field("databricks-governance-app", alias="ADMIN_SECRET_SCOPE")
    admin_client_id: str | None = Field(None, alias="ADMIN_CLIENT_ID")
    admin_client_secret: str | None = Field(None, alias="ADMIN_CLIENT_SECRET")
    metadata_cache_ttl_seconds: int = Field(120, alias="METADATA_CACHE_TTL_SECONDS")
    identity_cache_ttl_seconds: int = Field(300, alias="IDENTITY_CACHE_TTL_SECONDS")
    log_level: str = Field("INFO", alias="LOG_LEVEL")

    dev_user_email: str | None = Field(None, alias="DEV_USER_EMAIL")
    frontend_dist: Path = Field(REPO_ROOT / "frontend" / "dist", alias="FRONTEND_DIST")

    @property
    def admin_sp_configured(self) -> bool:
        return bool(self.admin_client_id and self.admin_client_secret)


@lru_cache
def get_config() -> AppConfig:
    return AppConfig()
