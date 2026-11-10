from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from backend.app.clients.sql import SqlExecutor, quote_identifier
from backend.app.core.errors import AppError
from backend.app.models.app_info import StorageHealth
from backend.app.models.audit import AuditEvent
from backend.app.models.identity_attribute import (
    AttributeAssignmentFilter,
    AttributeDefinition,
    AttributeSource,
    IdentityAttributeAssignment,
    ValueSource,
)
from backend.app.models.principal import PrincipalKind
from backend.app.repositories.base import (
    AccessRequestRepository,
    AuditRepository,
    CategoryApproverRepository,
    IdentityAttributeRepository,
    MaskRuleRepository,
    SettingsRepository,
    StorageProvider,
)
from backend.app.repositories.memory import (
    InMemoryAccessRequestRepository,
    InMemoryCategoryApproverRepository,
    InMemoryMaskRuleRepository,
)


def _ts(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    return datetime.fromisoformat(str(value).replace(" ", "T"))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


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


class DeltaIdentityAttributeRepository(IdentityAttributeRepository):
    def __init__(self, definitions: _DeltaTable, assignments: _DeltaTable) -> None:
        self._definitions = definitions
        self._assignments = assignments

    def list_definitions(self) -> list[AttributeDefinition]:
        rows = self._definitions.sql.query(
            f"SELECT * FROM {self._definitions.fqn} ORDER BY attribute_key"
        )
        return [self._definition_from_row(r) for r in rows]

    def get_definition(self, key: str) -> AttributeDefinition | None:
        rows = self._definitions.sql.query(
            f"SELECT * FROM {self._definitions.fqn} WHERE attribute_key = :key",
            {"key": key},
        )
        return self._definition_from_row(rows[0]) if rows else None

    def upsert_definition(self, definition: AttributeDefinition) -> AttributeDefinition:
        self._definitions.sql.execute(
            f"""
            MERGE INTO {self._definitions.fqn} t
            USING (
              SELECT
                :key AS attribute_key,
                :description AS description,
                :governed_tag_keys AS governed_tag_keys,
                :allowed_values AS allowed_values,
                :created_by AS created_by,
                CAST(:created_at AS TIMESTAMP) AS created_at,
                :updated_by AS updated_by,
                CAST(:updated_at AS TIMESTAMP) AS updated_at
            ) s
            ON t.attribute_key = s.attribute_key
            WHEN MATCHED THEN UPDATE SET
              description = s.description,
              governed_tag_keys = s.governed_tag_keys,
              allowed_values = s.allowed_values,
              updated_by = s.updated_by,
              updated_at = s.updated_at
            WHEN NOT MATCHED THEN INSERT (
              attribute_key, description, governed_tag_keys, allowed_values,
              created_by, created_at, updated_by, updated_at
            ) VALUES (
              s.attribute_key, s.description, s.governed_tag_keys, s.allowed_values,
              s.created_by, s.created_at, s.updated_by, s.updated_at
            )
            """,
            {
                "key": definition.key,
                "description": definition.description,
                "governed_tag_keys": json.dumps(definition.governed_tag_keys),
                "allowed_values": json.dumps(definition.allowed_values),
                "created_by": definition.created_by,
                "created_at": _iso(definition.created_at) or datetime.now().astimezone().isoformat(),
                "updated_by": definition.updated_by,
                "updated_at": _iso(definition.updated_at) or datetime.now().astimezone().isoformat(),
            },
        )
        return self.get_definition(definition.key) or definition

    def delete_definition(self, key: str) -> bool:
        before = self.get_definition(key)
        if before is None:
            return False
        self._definitions.sql.execute(
            f"DELETE FROM {self._definitions.fqn} WHERE attribute_key = :key",
            {"key": key},
        )
        return True

    def list_assignments(self, criteria: AttributeAssignmentFilter) -> list[IdentityAttributeAssignment]:
        clauses = ["1=1"]
        params: dict[str, Any] = {}
        if criteria.principal_name:
            clauses.append("lower(principal_name) = lower(:principal_name)")
            params["principal_name"] = criteria.principal_name
        if criteria.principal_type:
            clauses.append("principal_type = :principal_type")
            params["principal_type"] = criteria.principal_type.value
        if criteria.attribute_key:
            clauses.append("attribute_key = :attribute_key")
            params["attribute_key"] = criteria.attribute_key
        if not criteria.include_inactive:
            clauses.append("revoked_at IS NULL")
            clauses.append("(expires_at IS NULL OR expires_at > current_timestamp())")
        where = " AND ".join(clauses)
        rows = self._assignments.sql.query(
            f"SELECT * FROM {self._assignments.fqn} WHERE {where} ORDER BY created_at DESC",
            params,
        )
        return [self._assignment_from_row(r) for r in rows]

    def get_assignment(self, assignment_id: str) -> IdentityAttributeAssignment | None:
        rows = self._assignments.sql.query(
            f"SELECT * FROM {self._assignments.fqn} WHERE assignment_id = :id",
            {"id": assignment_id},
        )
        return self._assignment_from_row(rows[0]) if rows else None

    def add_assignment(self, assignment: IdentityAttributeAssignment) -> IdentityAttributeAssignment:
        return self.upsert_assignment(assignment)

    def upsert_assignment(self, assignment: IdentityAttributeAssignment) -> IdentityAttributeAssignment:
        """Insert or replace by assignment_id (used by UC mirror sync)."""
        self._assignments.sql.execute(
            f"DELETE FROM {self._assignments.fqn} WHERE assignment_id = :id",
            {"id": assignment.assignment_id},
        )
        self._assignments.sql.execute(
            f"""
            INSERT INTO {self._assignments.fqn} (
              assignment_id, principal_name, principal_type, attribute_key, attribute_value,
              source, granted_by, justification, expires_at, created_at, revoked_at, revoked_by
            ) VALUES (
              :assignment_id, :principal_name, :principal_type, :attribute_key, :attribute_value,
              :source, :granted_by, :justification, CAST(:expires_at AS TIMESTAMP),
              CAST(:created_at AS TIMESTAMP), CAST(:revoked_at AS TIMESTAMP), :revoked_by
            )
            """,
            {
                "assignment_id": assignment.assignment_id,
                "principal_name": assignment.principal_name,
                "principal_type": assignment.principal_type.value,
                "attribute_key": assignment.attribute_key,
                "attribute_value": assignment.attribute_value,
                "source": assignment.source.value,
                "granted_by": assignment.granted_by,
                "justification": assignment.justification,
                "expires_at": _iso(assignment.expires_at),
                "created_at": assignment.created_at.isoformat(),
                "revoked_at": _iso(assignment.revoked_at),
                "revoked_by": assignment.revoked_by,
            },
        )
        return assignment

    def revoke_assignment(
        self, assignment_id: str, actor: str, at: datetime
    ) -> IdentityAttributeAssignment | None:
        current = self.get_assignment(assignment_id)
        if current is None:
            return None
        # Delta SQL DELETE+INSERT is simpler than MERGE for a soft revoke.
        self._assignments.sql.execute(
            f"DELETE FROM {self._assignments.fqn} WHERE assignment_id = :id",
            {"id": assignment_id},
        )
        updated = current.model_copy(update={"revoked_at": at, "revoked_by": actor})
        return self.add_assignment(updated)

    def update_assignment_expiry(
        self, assignment_id: str, expires_at: datetime, actor: str
    ) -> IdentityAttributeAssignment | None:
        current = self.get_assignment(assignment_id)
        if current is None:
            return None
        self._assignments.sql.execute(
            f"DELETE FROM {self._assignments.fqn} WHERE assignment_id = :id",
            {"id": assignment_id},
        )
        updated = current.model_copy(update={"expires_at": expires_at, "granted_by": actor})
        return self.add_assignment(updated)

    @staticmethod
    def _definition_from_row(row: dict[str, Any]) -> AttributeDefinition:
        raw_values = row.get("allowed_values") or "[]"
        values = json.loads(raw_values) if isinstance(raw_values, str) else raw_values
        if not isinstance(values, list):
            values = []
        raw_tags = row.get("governed_tag_keys") or "[]"
        tag_keys = json.loads(raw_tags) if isinstance(raw_tags, str) else raw_tags
        if not isinstance(tag_keys, list):
            tag_keys = []
        return AttributeDefinition(
            key=row["attribute_key"],
            description=row.get("description"),
            governed_tag_keys=[str(v) for v in tag_keys],
            allowed_values=[str(v) for v in values],
            # The Delta table has no value_source column; stored values without tags are free-form.
            value_source=(
                ValueSource.FREE_FORM if values and not tag_keys else ValueSource.GOVERNED_TAGS
            ),
            created_by=row.get("created_by"),
            created_at=_ts(row.get("created_at")),
            updated_by=row.get("updated_by"),
            updated_at=_ts(row.get("updated_at")),
        )

    @staticmethod
    def _assignment_from_row(row: dict[str, Any]) -> IdentityAttributeAssignment:
        return IdentityAttributeAssignment(
            assignment_id=row["assignment_id"],
            principal_name=row["principal_name"],
            principal_type=PrincipalKind(row["principal_type"]),
            attribute_key=row["attribute_key"],
            attribute_value=row["attribute_value"],
            source=AttributeSource(row["source"]),
            granted_by=row.get("granted_by"),
            justification=row.get("justification"),
            expires_at=_ts(row.get("expires_at")),
            created_at=_ts(row["created_at"]) or datetime.now().astimezone(),
            revoked_at=_ts(row.get("revoked_at")),
            revoked_by=row.get("revoked_by"),
        )


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
        self._attributes = DeltaIdentityAttributeRepository(
            _DeltaTable(sql, catalog, schema, "identity_attribute_definitions"),
            _DeltaTable(sql, catalog, schema, "identity_attributes"),
        )
        # Access-request workflow is Lakebase-first; keep in-memory stubs on Delta.
        self._category_approvers = InMemoryCategoryApproverRepository()
        self._access_requests = InMemoryAccessRequestRepository()
        self._mask_rules = InMemoryMaskRuleRepository()
        self._probe_table = settings_table.fqn

    @property
    def settings(self) -> SettingsRepository:
        return self._settings

    @property
    def audit(self) -> AuditRepository:
        return self._audit

    @property
    def identity_attributes(self) -> IdentityAttributeRepository:
        return self._attributes

    @property
    def category_approvers(self) -> CategoryApproverRepository:
        return self._category_approvers

    @property
    def access_requests(self) -> AccessRequestRepository:
        return self._access_requests

    @property
    def mask_rules(self) -> MaskRuleRepository:
        return self._mask_rules

    def health(self) -> StorageHealth:
        try:
            self._sql.query(f"SELECT 1 FROM {self._probe_table} LIMIT 1")
            return StorageHealth(backend=self.kind, healthy=True, detail=self._location)
        except AppError as exc:
            return StorageHealth(backend=self.kind, healthy=False, detail=exc.message)
