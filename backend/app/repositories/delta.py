from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from backend.app.clients.sql import SqlExecutor, quote_identifier
from backend.app.core.errors import AppError
from backend.app.models.app_info import StorageHealth
from backend.app.models.audit import AuditEvent
from backend.app.repositories.base import AuditRepository, SettingsRepository, StorageProvider


def _ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value).replace(" ", "T"))


class _DeltaTable:
    def __init__(self, sql: SqlExecutor, catalog: str, schema: str, table: str) -> None:
        self.sql = sql
        self.fqn = ".".join(quote_identifier(p) for p in (catalog, schema, table))


class DeltaSettingsRepository(SettingsRepository):
    def __init__(self, table: _DeltaTable) -> None:
        self._t = table

    def get_all(self) -> dict[str, Any]:
        rows = self._t.sql.query(f"SELECT setting_key, setting_value FROM {self._t.fqn}")
        return {r["setting_key"]: json.loads(r["setting_value"]) for r in rows if r["setting_value"]}

    def put_many(self, values: dict[str, Any], actor: str) -> None:
        for key, value in values.items():
            self._t.sql.execute(
                f"""
                MERGE INTO {self._t.fqn} t
                USING (SELECT :key AS setting_key, :value AS setting_value) s
                ON t.setting_key = s.setting_key
                WHEN MATCHED THEN UPDATE SET
                  setting_value = s.setting_value, updated_by = :actor, updated_at = current_timestamp()
                WHEN NOT MATCHED THEN INSERT (setting_key, setting_value, updated_by, updated_at)
                  VALUES (s.setting_key, s.setting_value, :actor, current_timestamp())
                """,
                {"key": key, "value": json.dumps(value), "actor": actor},
            )


class DeltaAuditRepository(AuditRepository):
    def __init__(self, table: _DeltaTable) -> None:
        self._t = table

    def record(self, event: AuditEvent) -> None:
        self._t.sql.execute(
            f"INSERT INTO {self._t.fqn} (event_id, event_type, actor, payload, created_at) "
            "VALUES (:id, :type, :actor, :payload, CAST(:created_at AS TIMESTAMP))",
            {
                "id": event.event_id,
                "type": event.event_type,
                "actor": event.actor,
                "payload": json.dumps(event.payload, default=str),
                "created_at": event.created_at.isoformat(),
            },
        )

    def list_recent(self, limit: int = 100) -> list[AuditEvent]:
        rows = self._t.sql.query(f"SELECT * FROM {self._t.fqn} ORDER BY created_at DESC LIMIT {int(limit)}")
        return [
            AuditEvent(
                event_id=r["event_id"],
                event_type=r["event_type"],
                actor=r["actor"],
                payload=json.loads(r["payload"] or "{}"),
                created_at=_ts(r["created_at"]),
            )
            for r in rows
        ]


class DeltaStorageProvider(StorageProvider):
    """Stores app state in Unity Catalog Delta tables through a SQL warehouse.

    The setup job creates the tables (provisioning/steps/app_storage.py); the app service
    principal only needs SELECT and MODIFY on them.
    """

    kind = "delta"

    def __init__(self, sql: SqlExecutor, catalog: str, schema: str) -> None:
        self._sql = sql
        self._location = f"{catalog}.{schema}"
        settings_table = _DeltaTable(sql, catalog, schema, "app_settings")
        self._settings = DeltaSettingsRepository(settings_table)
        self._audit = DeltaAuditRepository(_DeltaTable(sql, catalog, schema, "audit_events"))
        self._probe_table = settings_table.fqn

    @property
    def settings(self) -> SettingsRepository:
        return self._settings

    @property
    def audit(self) -> AuditRepository:
        return self._audit

    def health(self) -> StorageHealth:
        try:
            self._sql.query(f"SELECT 1 FROM {self._probe_table} LIMIT 1")
            return StorageHealth(backend=self.kind, healthy=True, detail=self._location)
        except AppError as exc:
            return StorageHealth(backend=self.kind, healthy=False, detail=exc.message)
