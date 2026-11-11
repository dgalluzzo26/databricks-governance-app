"""Deployment-time configuration loaded from environment variables and ``.env``."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class StorageBackend(StrEnum):
    """Where the app persists its own state (attributes, requests, rules, audit).

    Attributes:
        LAKEBASE: Lakebase Postgres; Unity Catalog Delta tables are kept as an enforcement
            mirror for the ABAC UDFs.
        DELTA: Unity Catalog Delta tables in ``app_catalog.app_schema`` via the SQL warehouse.
        MEMORY: In-process memory; not persisted, intended for local development and tests.
    """

    LAKEBASE = "lakebase"
    DELTA = "delta"
    MEMORY = "memory"


class AuthMode(StrEnum):
    """Identity used for per-request Databricks API calls.

    Attributes:
        SERVICE_PRINCIPAL: Calls run as the service principal (Account Admin SP when
            configured, otherwise the app SP).
        ON_BEHALF_OF_USER: Calls run as the signed-in user with their forwarded access token,
            so Unity Catalog applies the user's own permissions.
    """

    SERVICE_PRINCIPAL = "service_principal"
    ON_BEHALF_OF_USER = "on_behalf_of_user"


class AppConfig(BaseSettings):
    """Deployment-time configuration, read from environment variables.

    Runtime settings that admins change in the UI live in the storage backend instead
    (see services/settings.py).

    Values come from the environment variable named in parentheses (also read from a
    ``.env`` file); unknown variables are ignored. Fields can also be set by Python name.

    Attributes:
        app_name: Databricks App name (``DATABRICKS_APP_NAME``).
        port: HTTP port the server listens on (``DATABRICKS_APP_PORT``).
        databricks_host: Workspace URL (``DATABRICKS_HOST``); None lets the SDK resolve it.
        databricks_profile: Databricks CLI profile for the app client in local development
            (``DATABRICKS_CONFIG_PROFILE``); None uses default SDK auth.
        warehouse_id: SQL warehouse for Statement Execution (``DATABRICKS_WAREHOUSE_ID``);
            when None, SQL-backed operations raise ``NotConfiguredError``.
        storage_backend: Where app state is persisted (``STORAGE_BACKEND``).
        app_catalog: Catalog for app-owned objects (``APP_CATALOG``).
        app_schema: Schema for app-owned tables (``APP_SCHEMA``).
        abac_function_schema: Schema in ``app_catalog`` holding ABAC UDFs
            (``ABAC_FUNCTION_SCHEMA``).
        row_filter_tag_key: Governed tag that attaches row-filter dimensions to columns
            (``ROW_FILTER_TAG_KEY``).
        row_filter_policy_name: Metastore row-filter policy name (``ROW_FILTER_POLICY_NAME``).
        mask_rule_tag_key: Governed tag that selects mask rules on columns
            (``MASK_RULE_TAG_KEY``).
        mask_variant_policy_name: Metastore mask policy name (``MASK_VARIANT_POLICY_NAME``).
        mask_geometry_policy_name: Metastore geometry mask policy name
            (``MASK_GEOMETRY_POLICY_NAME``).
        mask_geography_policy_name: Metastore geography mask policy name
            (``MASK_GEOGRAPHY_POLICY_NAME``).
        mask_handler_llm_endpoint: Model serving endpoint that drafts custom mask expressions
            (``MASK_HANDLER_LLM_ENDPOINT``).
        mask_handler_validation_secret: HMAC secret for mask validation tokens
            (``MASK_HANDLER_VALIDATION_SECRET``); None falls back to the admin client secret.
        pg_host: Lakebase Postgres host (``PGHOST``).
        pg_port: Lakebase Postgres port (``PGPORT``).
        pg_database: Lakebase database name (``PGDATABASE``).
        pg_user: Lakebase Postgres user (``PGUSER``).
        lakebase_endpoint: Lakebase endpoint used to mint database credentials
            (``LAKEBASE_ENDPOINT``).
        lakebase_schema: Postgres schema for app tables (``LAKEBASE_SCHEMA``).
        auth_mode: Identity for per-request API calls (``AUTH_MODE``).
        admin_group: Workspace group whose members are app admins (``APP_ADMIN_GROUP``).
        admin_secret_scope: Secret scope holding Account Admin SP credentials
            (``ADMIN_SECRET_SCOPE``).
        admin_client_id: Account Admin SP client ID (``ADMIN_CLIENT_ID``); None when not set.
        admin_client_secret: Account Admin SP client secret (``ADMIN_CLIENT_SECRET``); None
            when not set.
        metadata_cache_ttl_seconds: TTL in seconds for cached Unity Catalog metadata
            (``METADATA_CACHE_TTL_SECONDS``).
        identity_cache_ttl_seconds: TTL in seconds for cached user / group lookups
            (``IDENTITY_CACHE_TTL_SECONDS``).
        log_level: Root log level (``LOG_LEVEL``).
        dev_user_email: Identity to assume locally when no Apps proxy headers are present
            (``DEV_USER_EMAIL``); None falls back to the CLI profile's user.
        frontend_dist: Directory of the built frontend served as static files
            (``FRONTEND_DIST``).
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
    # ABAC functions live in {app_catalog}.{abac_function_schema}.
    abac_function_schema: str = Field("masking_functions", alias="ABAC_FUNCTION_SCHEMA")
    row_filter_tag_key: str = Field("row_filter", alias="ROW_FILTER_TAG_KEY")
    row_filter_policy_name: str = Field("rf_by_dimension", alias="ROW_FILTER_POLICY_NAME")
    mask_rule_tag_key: str = Field("mask_rule", alias="MASK_RULE_TAG_KEY")
    mask_variant_policy_name: str = Field(
        "mask_sensitivity_variant", alias="MASK_VARIANT_POLICY_NAME"
    )
    mask_geometry_policy_name: str = Field(
        "mask_sensitivity_geometry", alias="MASK_GEOMETRY_POLICY_NAME"
    )
    mask_geography_policy_name: str = Field(
        "mask_sensitivity_geography", alias="MASK_GEOGRAPHY_POLICY_NAME"
    )
    mask_handler_llm_endpoint: str = Field(
        "databricks-meta-llama-3-3-70b-instruct", alias="MASK_HANDLER_LLM_ENDPOINT"
    )
    mask_handler_validation_secret: str | None = Field(
        None, alias="MASK_HANDLER_VALIDATION_SECRET"
    )

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
        """Whether both Account Admin SP client ID and secret are set in this config."""
        return bool(self.admin_client_id and self.admin_client_secret)


@lru_cache
def get_config() -> AppConfig:
    """Return the process-wide configuration, loaded from the environment on first call.

    Returns:
        The cached ``AppConfig`` instance.
    """
    return AppConfig()
