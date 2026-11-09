from __future__ import annotations

import logging
import threading
import time
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.core.errors import NotConfiguredError
from backend.app.models.app_info import StorageHealth
from backend.app.models.audit import AuditEvent
from backend.app.repositories.base import AuditRepository, SettingsRepository, StorageProvider

logger = logging.getLogger(__name__)

# Lakebase OAuth tokens last one hour; refresh well before that.
_TOKEN_REFRESH_SECONDS = 45 * 60


class LakebaseTokenProvider:
    def __init__(self, factory: WorkspaceClientFactory, endpoint: str) -> None:
        self._factory = factory
        self._endpoint = endpoint
        self._lock = threading.Lock()
        self._token: str | None = None
        self._issued_at = 0.0

    def token(self) -> str:
        with self._lock:
            if self._token is None or time.monotonic() - self._issued_at > _TOKEN_REFRESH_SECONDS:
                # Lakebase credentials must come from the Apps SP (CAN_CONNECT_AND_CREATE),
                # not the Account Admin SP used for Unity Catalog.
                credential = self._factory.app().postgres.generate_database_credential(
                    endpoint=self._endpoint
                )
                self._token = credential.token
                self._issued_at = time.monotonic()
            return self._token


def _connection_class(tokens: LakebaseTokenProvider) -> type[psycopg.Connection]:
    """A Connection subclass that injects a fresh OAuth token on every new connection."""

    class LakebaseConnection(psycopg.Connection):
        @classmethod
        def connect(cls, conninfo: str = "", **kwargs: Any):  # type: ignore[override]
            kwargs["password"] = tokens.token()
            return super().connect(conninfo, **kwargs)

    return LakebaseConnection


_DDL = """
CREATE SCHEMA IF NOT EXISTS {schema};

CREATE TABLE IF NOT EXISTS {schema}.app_settings (
  setting_key   TEXT PRIMARY KEY,
  setting_value JSONB NOT NULL,
  updated_by    TEXT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {schema}.audit_events (
  event_id   TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  actor      TEXT NOT NULL,
  payload    JSONB NOT NULL DEFAULT '{{}}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS audit_events_created_idx ON {schema}.audit_events (created_at DESC);
"""


class _Pg:
    def __init__(self, pool: ConnectionPool, schema: str) -> None:
        self.pool = pool
        self.schema = schema

    def fetch(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.format(schema=self.schema), params or {})
            return cur.fetchall() if cur.description else []

    def run(self, sql: str, params: dict[str, Any] | None = None) -> None:
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql.format(schema=self.schema), params or {})


class LakebaseSettingsRepository(SettingsRepository):
    def __init__(self, pg: _Pg) -> None:
        self._pg = pg

    def get_all(self) -> dict[str, Any]:
        rows = self._pg.fetch("SELECT setting_key, setting_value FROM {schema}.app_settings")
        return {r["setting_key"]: r["setting_value"] for r in rows}

    def put_many(self, values: dict[str, Any], actor: str) -> None:
        for key, value in values.items():
            self._pg.run(
                """
                INSERT INTO {schema}.app_settings (setting_key, setting_value, updated_by, updated_at)
                VALUES (%(key)s, %(value)s, %(actor)s, now())
                ON CONFLICT (setting_key) DO UPDATE
                  SET setting_value = EXCLUDED.setting_value,
                      updated_by = EXCLUDED.updated_by,
                      updated_at = now()
                """,
                {"key": key, "value": Jsonb(value), "actor": actor},
            )


class LakebaseAuditRepository(AuditRepository):
    def __init__(self, pg: _Pg) -> None:
        self._pg = pg

    def record(self, event: AuditEvent) -> None:
        self._pg.run(
            "INSERT INTO {schema}.audit_events (event_id, event_type, actor, payload, created_at) "
            "VALUES (%(id)s, %(type)s, %(actor)s, %(payload)s, %(created_at)s)",
            {
                "id": event.event_id,
                "type": event.event_type,
                "actor": event.actor,
                "payload": Jsonb(event.payload),
                "created_at": event.created_at,
            },
        )

    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.audit_events ORDER BY created_at DESC LIMIT %(limit)s", {"limit": limit}
        )
        return [AuditEvent(**r) for r in rows]


class LakebaseStorageProvider(StorageProvider):
    """Stores app state in a Lakebase Postgres database.

    The app service principal creates and owns the schema on first start, which is why the
    app must be deployed once before running locally against the same database.
    """

    kind = "lakebase"

    def __init__(self, config: AppConfig, factory: WorkspaceClientFactory) -> None:
        required = {
            "PGHOST": config.pg_host,
            "PGDATABASE": config.pg_database,
            "PGUSER": config.pg_user,
            "LAKEBASE_ENDPOINT": config.lakebase_endpoint,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise NotConfiguredError(f"Lakebase storage is missing configuration: {', '.join(missing)}")
        if not config.lakebase_schema.replace("_", "").isalnum():
            raise NotConfiguredError(f"Invalid LAKEBASE_SCHEMA: {config.lakebase_schema!r}")

        self._schema = config.lakebase_schema
        self._location = f"{config.pg_database}.{self._schema}"
        tokens = LakebaseTokenProvider(factory, config.lakebase_endpoint or "")
        self._pool = ConnectionPool(
            conninfo=(
                f"host={config.pg_host} port={config.pg_port} dbname={config.pg_database} "
                f"user={config.pg_user} sslmode=require"
            ),
            connection_class=_connection_class(tokens),
            kwargs={"autocommit": True},
            min_size=1,
            max_size=8,
            max_lifetime=_TOKEN_REFRESH_SECONDS,
            check=ConnectionPool.check_connection,
            open=False,
        )
        self._pg = _Pg(self._pool, self._schema)
        self._settings = LakebaseSettingsRepository(self._pg)
        self._audit = LakebaseAuditRepository(self._pg)

    @property
    def settings(self) -> SettingsRepository:
        return self._settings

    @property
    def audit(self) -> AuditRepository:
        return self._audit

    def initialize(self) -> None:
        self._pool.open(wait=True, timeout=30)
        self._pg.run(_DDL)
        logger.info("Lakebase schema %s ready", self._location)

    def health(self) -> StorageHealth:
        try:
            self._pg.fetch("SELECT 1 AS ok")
            return StorageHealth(backend=self.kind, healthy=True, detail=self._location)
        except Exception as exc:  # noqa: BLE001 - surfaced to admins as a health message
            return StorageHealth(backend=self.kind, healthy=False, detail=str(exc))

    def close(self) -> None:
        self._pool.close()
