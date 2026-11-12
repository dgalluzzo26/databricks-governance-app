"""Lakebase (Postgres) persistence for app-managed governance state.

Stores settings, audit events, identity attributes, access requests, mask rules, and
policy exceptions in a Databricks Lakebase database. Unity Catalog objects and ABAC
policies remain in the metastore; this backend only holds the app's own tables.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.core.errors import NotConfiguredError
from backend.app.models.access_request import AccessRequest, AccessRequestStatus, CategoryApprover
from backend.app.models.app_info import StorageHealth
from backend.app.models.audit import AuditEvent
from backend.app.models.identity_attribute import (
    AttributeAssignmentFilter,
    AttributeDefinition,
    AttributeSource,
    IdentityAttributeAssignment,
    ValueSource,
)
from backend.app.models.mask_rule import MaskRuleDefinition, MaskRuleKind
from backend.app.models.policy_exception import PolicyException
from backend.app.models.principal import PrincipalKind
from backend.app.repositories.base import (
    AccessRequestRepository,
    AuditRepository,
    CategoryApproverRepository,
    IdentityAttributeRepository,
    MaskRuleRepository,
    PolicyExceptionRepository,
    SettingsRepository,
    StorageProvider,
)

logger = logging.getLogger(__name__)

# Lakebase OAuth tokens last one hour; refresh well before that.
_TOKEN_REFRESH_SECONDS = 45 * 60


class LakebaseTokenProvider:
    """Thread-safe cache of Lakebase Postgres OAuth tokens.

    Tokens are issued by the Apps service principal (CAN_CONNECT_AND_CREATE), not the
    Account Admin SP used for Unity Catalog operations.

    Args:
        factory: Workspace client factory used to mint database credentials.
        endpoint: Lakebase endpoint identifier passed to ``generate_database_credential``.
    """

    def __init__(self, factory: WorkspaceClientFactory, endpoint: str) -> None:
        self._factory = factory
        self._endpoint = endpoint
        self._lock = threading.Lock()
        self._token: str | None = None
        self._issued_at = 0.0

    def token(self) -> str:
        """Return a cached OAuth token, refreshing when older than the refresh window.

        Returns:
            A non-empty Postgres password (OAuth token) for Lakebase connections.
        """
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
    """Return a Connection subclass that injects a fresh OAuth token on connect.

    Args:
        tokens: Provider that supplies the current Lakebase OAuth password.

    Returns:
        A ``psycopg.Connection`` subclass whose ``connect`` sets ``password`` from
        ``tokens`` before opening the socket.
    """

    class LakebaseConnection(psycopg.Connection):
        """psycopg Connection that always authenticates with a live Lakebase token."""

        @classmethod
        def connect(cls, conninfo: str = "", **kwargs: Any):  # type: ignore[override]
            """Open a connection after injecting the current OAuth token as password.

            Args:
                conninfo: libpq connection string (may be empty when using kwargs).
                **kwargs: Extra connect kwargs; ``password`` is overwritten.

            Returns:
                An open ``psycopg.Connection`` instance.
            """
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
CREATE INDEX IF NOT EXISTS audit_events_created_idx
  ON {schema}.audit_events (created_at DESC);

CREATE TABLE IF NOT EXISTS {schema}.identity_attribute_definitions (
  attribute_key       TEXT PRIMARY KEY,
  description         TEXT,
  governed_tag_keys   JSONB NOT NULL DEFAULT '[]'::jsonb,
  allowed_values      JSONB NOT NULL DEFAULT '[]'::jsonb,
  value_source        TEXT NOT NULL DEFAULT 'governed_tags',
  value_sql           TEXT,
  created_by          TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by          TEXT,
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {schema}.identity_attributes (
  assignment_id   TEXT PRIMARY KEY,
  principal_name  TEXT NOT NULL,
  principal_type  TEXT NOT NULL,
  attribute_key   TEXT NOT NULL,
  attribute_value TEXT NOT NULL,
  source          TEXT NOT NULL,
  granted_by      TEXT,
  justification   TEXT,
  expires_at      TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL,
  revoked_at      TIMESTAMPTZ,
  revoked_by      TEXT
);
CREATE INDEX IF NOT EXISTS identity_attributes_principal_idx
  ON {schema}.identity_attributes (lower(principal_name), attribute_key);
CREATE INDEX IF NOT EXISTS identity_attributes_key_idx
  ON {schema}.identity_attributes (attribute_key, created_at DESC);

CREATE TABLE IF NOT EXISTS {schema}.sensitivity_category_approvers (
  approver_id     TEXT PRIMARY KEY,
  attribute_key   TEXT NOT NULL DEFAULT 'sensitivity',
  category_path   TEXT NOT NULL,
  principal_name  TEXT NOT NULL,
  principal_type  TEXT NOT NULL,
  created_by      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Indexes on sensitivity_category_approvers.attribute_key are created in initialize(),
-- after the column is guaranteed to exist on pre-existing tables.

CREATE TABLE IF NOT EXISTS {schema}.access_requests (
  request_id             TEXT PRIMARY KEY,
  requester_name         TEXT NOT NULL,
  attribute_key          TEXT NOT NULL,
  attribute_value        TEXT NOT NULL,
  requested_expires_at   TIMESTAMPTZ NOT NULL,
  justification          TEXT NOT NULL,
  status                 TEXT NOT NULL,
  decided_by             TEXT,
  decided_at             TIMESTAMPTZ,
  decision_note          TEXT,
  approved_expires_at    TIMESTAMPTZ,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS access_requests_status_idx
  ON {schema}.access_requests (status, created_at DESC);
CREATE INDEX IF NOT EXISTS access_requests_requester_idx
  ON {schema}.access_requests (lower(requester_name), created_at DESC);
CREATE INDEX IF NOT EXISTS access_requests_value_idx
  ON {schema}.access_requests (attribute_key, lower(attribute_value), status);

CREATE TABLE IF NOT EXISTS {schema}.renewal_dismissals (
  assignment_id   TEXT NOT NULL,
  expires_at      TIMESTAMPTZ NOT NULL,
  dismissed_by    TEXT NOT NULL,
  dismissed_at    TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (assignment_id, expires_at)
);

CREATE TABLE IF NOT EXISTS {schema}.mask_rule_definitions (
  rule_key     TEXT PRIMARY KEY,
  description  TEXT,
  kind         TEXT NOT NULL DEFAULT 'builtin',
  handler      TEXT NOT NULL,
  expression   TEXT,
  params       JSONB NOT NULL DEFAULT '{{}}'::jsonb,
  validated_at TIMESTAMPTZ,
  updated_by   TEXT,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {schema}.masking_config (
  config_key   TEXT PRIMARY KEY,
  config_value TEXT NOT NULL,
  updated_by   TEXT,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS {schema}.policy_exceptions (
  exception_id    TEXT PRIMARY KEY,
  principal_name  TEXT NOT NULL,
  principal_type  TEXT NOT NULL,
  note            TEXT,
  created_by      TEXT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS policy_exceptions_uniq
  ON {schema}.policy_exceptions (lower(principal_name), principal_type);
CREATE INDEX IF NOT EXISTS policy_exceptions_principal_idx
  ON {schema}.policy_exceptions (principal_type, lower(principal_name));
"""


class _Pg:
    """Thin helper around a psycopg connection pool for schema-scoped SQL.

    SQL templates must use ``{schema}``; it is interpolated with this helper's schema
    before execution. Connections use autocommit as configured on the pool.

    Args:
        pool: Open (or openable) psycopg connection pool.
        schema: Postgres schema name for app tables.
    """

    def __init__(self, pool: ConnectionPool, schema: str) -> None:
        self.pool = pool
        self.schema = schema

    def fetch(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a query and return all rows as dicts.

        Args:
            sql: SQL with optional ``{schema}`` placeholder and ``%(name)s`` params.
            params: Named bind parameters; defaults to an empty mapping.

        Returns:
            Row dicts when the statement has a result set; otherwise an empty list.
        """
        with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql.format(schema=self.schema), params or {})
            return cur.fetchall() if cur.description else []

    def run(self, sql: str, params: dict[str, Any] | None = None) -> None:
        """Execute a statement that does not return rows (DDL/DML).

        Args:
            sql: SQL with optional ``{schema}`` placeholder and ``%(name)s`` params.
            params: Named bind parameters; defaults to an empty mapping.
        """
        with self.pool.connection() as conn, conn.cursor() as cur:
            cur.execute(sql.format(schema=self.schema), params or {})


class LakebaseSettingsRepository(SettingsRepository):
    """Postgres-backed key/value JSON settings store.

    Args:
        pg: Schema-scoped query helper.
    """

    def __init__(self, pg: _Pg) -> None:
        self._pg = pg

    def get_all(self) -> dict[str, Any]:
        """Return every setting key mapped to its JSON value.

        Returns:
            Mapping of setting key to decoded JSON value.
        """
        rows = self._pg.fetch("SELECT setting_key, setting_value FROM {schema}.app_settings")
        return {r["setting_key"]: r["setting_value"] for r in rows}

    def put_many(self, values: dict[str, Any], actor: str) -> None:
        """Upsert multiple settings and record the actor on each row.

        Args:
            values: Setting keys to JSON-serializable values.
            actor: Principal name recorded in ``updated_by``.
        """
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
    """Append-only audit event store in Lakebase.

    Args:
        pg: Schema-scoped query helper.
    """

    def __init__(self, pg: _Pg) -> None:
        self._pg = pg

    def record(self, event: AuditEvent) -> None:
        """Persist a single audit event.

        Args:
            event: Event to insert; ``event_id`` must be unique.
        """
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
        """Return the newest audit events.

        Args:
            limit: Maximum number of events to return.

        Returns:
            Events ordered by ``created_at`` descending.
        """
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.audit_events ORDER BY created_at DESC LIMIT %(limit)s",
            {"limit": limit},
        )
        return [AuditEvent(**r) for r in rows]


class LakebaseIdentityAttributeRepository(IdentityAttributeRepository):
    """Lakebase store for attribute definitions and principal assignments.

    Definitions describe which sensitivity values the app manages; assignments are the
    grants that later sync into Unity Catalog identity attributes / tags.

    Args:
        pg: Schema-scoped query helper.
    """

    def __init__(self, pg: _Pg) -> None:
        self._pg = pg

    def list_definitions(self) -> list[AttributeDefinition]:
        """Return all attribute definitions ordered by key.

        Returns:
            Definitions sorted by ``attribute_key``.
        """
        rows = self._pg.fetch("SELECT * FROM {schema}.identity_attribute_definitions ORDER BY attribute_key")
        return [self._definition_from_row(r) for r in rows]

    def get_definition(self, key: str) -> AttributeDefinition | None:
        """Return one attribute definition by key.

        Args:
            key: Attribute key (primary key).

        Returns:
            The definition, or ``None`` if missing.
        """
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.identity_attribute_definitions WHERE attribute_key = %(key)s",
            {"key": key},
        )
        return self._definition_from_row(rows[0]) if rows else None

    def upsert_definition(self, definition: AttributeDefinition) -> AttributeDefinition:
        """Insert or update an attribute definition and return the stored row.

        On conflict, metadata and value-source fields are updated; ``created_*`` is kept
        from the insert payload only on first write.

        Args:
            definition: Definition to persist.

        Returns:
            The row as stored after upsert (falls back to ``definition`` if reload fails).
        """
        self._pg.run(
            """
            INSERT INTO {schema}.identity_attribute_definitions (
              attribute_key, description, governed_tag_keys, allowed_values,
              value_source, value_sql, created_by, created_at, updated_by, updated_at
            ) VALUES (
              %(key)s, %(description)s, %(governed_tag_keys)s, %(allowed_values)s,
              %(value_source)s, %(value_sql)s,
              %(created_by)s, %(created_at)s, %(updated_by)s, %(updated_at)s
            )
            ON CONFLICT (attribute_key) DO UPDATE SET
              description = EXCLUDED.description,
              governed_tag_keys = EXCLUDED.governed_tag_keys,
              allowed_values = EXCLUDED.allowed_values,
              value_source = EXCLUDED.value_source,
              value_sql = EXCLUDED.value_sql,
              updated_by = EXCLUDED.updated_by,
              updated_at = EXCLUDED.updated_at
            """,
            {
                "key": definition.key,
                "description": definition.description,
                "governed_tag_keys": Jsonb(definition.governed_tag_keys),
                "allowed_values": Jsonb(definition.allowed_values),
                "value_source": definition.value_source.value,
                "value_sql": definition.value_sql,
                "created_by": definition.created_by,
                "created_at": definition.created_at or datetime.now().astimezone(),
                "updated_by": definition.updated_by,
                "updated_at": definition.updated_at or datetime.now().astimezone(),
            },
        )
        return self.get_definition(definition.key) or definition

    def delete_definition(self, key: str) -> bool:
        """Delete an attribute definition if it exists.

        Args:
            key: Attribute key to remove.

        Returns:
            ``True`` if a row was present and deleted; ``False`` if missing.
        """
        before = self.get_definition(key)
        if before is None:
            return False
        self._pg.run(
            "DELETE FROM {schema}.identity_attribute_definitions WHERE attribute_key = %(key)s",
            {"key": key},
        )
        return True

    def list_assignments(self, criteria: AttributeAssignmentFilter) -> list[IdentityAttributeAssignment]:
        """Return assignments matching the filter, newest first.

        Unless ``include_inactive`` is set, revoked and expired rows are excluded.

        Args:
            criteria: Optional principal, type, key, and active-only filters.

        Returns:
            Matching assignments ordered by ``created_at`` descending.
        """
        clauses = ["1=1"]
        params: dict[str, Any] = {}
        if criteria.principal_name:
            clauses.append("lower(principal_name) = lower(%(principal_name)s)")
            params["principal_name"] = criteria.principal_name
        if criteria.principal_type:
            clauses.append("principal_type = %(principal_type)s")
            params["principal_type"] = criteria.principal_type.value
        if criteria.attribute_key:
            clauses.append("attribute_key = %(attribute_key)s")
            params["attribute_key"] = criteria.attribute_key
        if not criteria.include_inactive:
            clauses.append("revoked_at IS NULL")
            clauses.append("(expires_at IS NULL OR expires_at > now())")
        where = " AND ".join(clauses)
        rows = self._pg.fetch(
            f"SELECT * FROM {{schema}}.identity_attributes WHERE {where} ORDER BY created_at DESC",
            params,
        )
        return [self._assignment_from_row(r) for r in rows]

    def get_assignment(self, assignment_id: str) -> IdentityAttributeAssignment | None:
        """Return one assignment by id.

        Args:
            assignment_id: Primary key of the assignment.

        Returns:
            The assignment, or ``None`` if missing.
        """
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.identity_attributes WHERE assignment_id = %(id)s",
            {"id": assignment_id},
        )
        return self._assignment_from_row(rows[0]) if rows else None

    def add_assignment(self, assignment: IdentityAttributeAssignment) -> IdentityAttributeAssignment:
        """Insert a new identity attribute assignment.

        Args:
            assignment: Fully populated assignment to insert.

        Returns:
            The same ``assignment`` instance after a successful insert.
        """
        self._pg.run(
            """
            INSERT INTO {schema}.identity_attributes (
              assignment_id, principal_name, principal_type, attribute_key, attribute_value,
              source, granted_by, justification, expires_at, created_at, revoked_at, revoked_by
            ) VALUES (
              %(assignment_id)s, %(principal_name)s, %(principal_type)s, %(attribute_key)s,
              %(attribute_value)s, %(source)s, %(granted_by)s, %(justification)s, %(expires_at)s,
              %(created_at)s, %(revoked_at)s, %(revoked_by)s
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
                "expires_at": assignment.expires_at,
                "created_at": assignment.created_at,
                "revoked_at": assignment.revoked_at,
                "revoked_by": assignment.revoked_by,
            },
        )
        return assignment

    def revoke_assignment(
        self, assignment_id: str, actor: str, at: datetime
    ) -> IdentityAttributeAssignment | None:
        """Soft-revoke an assignment by setting ``revoked_at`` / ``revoked_by``.

        Args:
            assignment_id: Assignment to revoke.
            actor: Principal recorded as ``revoked_by``.
            at: Revocation timestamp.

        Returns:
            The updated assignment, or ``None`` if the id was not found.
        """
        current = self.get_assignment(assignment_id)
        if current is None:
            return None
        self._pg.run(
            """
            UPDATE {schema}.identity_attributes
            SET revoked_at = %(at)s, revoked_by = %(actor)s
            WHERE assignment_id = %(id)s
            """,
            {"id": assignment_id, "actor": actor, "at": at},
        )
        return self.get_assignment(assignment_id)

    def update_assignment_expiry(
        self, assignment_id: str, expires_at: datetime, actor: str
    ) -> IdentityAttributeAssignment | None:
        """Update an assignment's expiry and record the renewing actor as ``granted_by``.

        Args:
            assignment_id: Assignment to renew.
            expires_at: New expiry timestamp.
            actor: Principal recorded as ``granted_by``.

        Returns:
            The updated assignment, or ``None`` if the id was not found.
        """
        current = self.get_assignment(assignment_id)
        if current is None:
            return None
        self._pg.run(
            """
            UPDATE {schema}.identity_attributes
            SET expires_at = %(expires_at)s, granted_by = %(actor)s
            WHERE assignment_id = %(id)s
            """,
            {"id": assignment_id, "expires_at": expires_at, "actor": actor},
        )
        return self.get_assignment(assignment_id)

    @staticmethod
    def _definition_from_row(row: dict[str, Any]) -> AttributeDefinition:
        """Map a definition table row to an ``AttributeDefinition``.

        Args:
            row: Dict row from ``identity_attribute_definitions``.

        Returns:
            Typed definition with safe defaults for malformed JSON list columns.
        """
        values = row.get("allowed_values") or []
        if not isinstance(values, list):
            values = []
        tag_keys = row.get("governed_tag_keys") or []
        if not isinstance(tag_keys, list):
            tag_keys = []
        return AttributeDefinition(
            key=row["attribute_key"],
            description=row.get("description"),
            governed_tag_keys=[str(v) for v in tag_keys],
            allowed_values=[str(v) for v in values],
            value_source=ValueSource(row.get("value_source") or ValueSource.GOVERNED_TAGS.value),
            value_sql=row.get("value_sql"),
            created_by=row.get("created_by"),
            created_at=row.get("created_at"),
            updated_by=row.get("updated_by"),
            updated_at=row.get("updated_at"),
        )

    @staticmethod
    def _assignment_from_row(row: dict[str, Any]) -> IdentityAttributeAssignment:
        """Map an assignment table row to an ``IdentityAttributeAssignment``.

        Args:
            row: Dict row from ``identity_attributes``.

        Returns:
            Typed assignment model.
        """
        return IdentityAttributeAssignment(
            assignment_id=row["assignment_id"],
            principal_name=row["principal_name"],
            principal_type=PrincipalKind(row["principal_type"]),
            attribute_key=row["attribute_key"],
            attribute_value=row["attribute_value"],
            source=AttributeSource(row["source"]),
            granted_by=row.get("granted_by"),
            justification=row.get("justification"),
            expires_at=row.get("expires_at"),
            created_at=row["created_at"],
            revoked_at=row.get("revoked_at"),
            revoked_by=row.get("revoked_by"),
        )


class LakebaseCategoryApproverRepository(CategoryApproverRepository):
    """Lakebase store for sensitivity-category approvers.

    Approvers decide access requests for a category path; uniqueness is case-insensitive
    on path and principal name within a principal type.

    Args:
        pg: Schema-scoped query helper.
    """

    def __init__(self, pg: _Pg) -> None:
        self._pg = pg

    def list_approvers(
        self,
        category_path: str | None = None,
        *,
        attribute_key: str | None = None,
    ) -> list[CategoryApprover]:
        """List approvers, optionally filtered by path and/or attribute key.

        Args:
            category_path: When set, match path case-insensitively.
            attribute_key: When set, match attribute key case-insensitively.

        Returns:
            Approvers ordered by attribute, path, principal type, and name.
        """
        clauses: list[str] = []
        params: dict[str, str] = {}
        if attribute_key:
            clauses.append("lower(attribute_key) = lower(%(attribute_key)s)")
            params["attribute_key"] = attribute_key
        if category_path:
            clauses.append("lower(category_path) = lower(%(path)s)")
            params["path"] = category_path
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.sensitivity_category_approvers "
            f"{where} "
            "ORDER BY attribute_key, category_path, principal_type, principal_name",
            params or None,
        )
        return [self._from_row(r) for r in rows]

    def list_configured_categories(self, *, attribute_key: str | None = None) -> list[str]:
        """Return distinct paths that have at least one approver.

        Args:
            attribute_key: When set, only paths for this attribute key.

        Returns:
            Sorted path strings.
        """
        if attribute_key:
            rows = self._pg.fetch(
                "SELECT DISTINCT category_path FROM {schema}.sensitivity_category_approvers "
                "WHERE lower(attribute_key) = lower(%(attribute_key)s) "
                "ORDER BY category_path",
                {"attribute_key": attribute_key},
            )
        else:
            rows = self._pg.fetch(
                "SELECT DISTINCT category_path FROM {schema}.sensitivity_category_approvers "
                "ORDER BY category_path"
            )
        return [str(r["category_path"]) for r in rows]

    def get_approver(self, approver_id: str) -> CategoryApprover | None:
        """Return one approver by id.

        Args:
            approver_id: Primary key.

        Returns:
            The approver, or ``None`` if missing.
        """
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.sensitivity_category_approvers WHERE approver_id = %(id)s",
            {"id": approver_id},
        )
        return self._from_row(rows[0]) if rows else None

    def upsert_approver(self, approver: CategoryApprover) -> CategoryApprover:
        """Insert an approver, or return the existing row for the same unique triple.

        Unique key is (category_path, principal_name, principal_type) case-insensitive
        on path and name. Existing rows are not updated.

        Args:
            approver: Approver to insert when no duplicate exists.

        Returns:
            The existing or newly inserted approver.
        """
        existing = self._pg.fetch(
            """
            SELECT approver_id FROM {schema}.sensitivity_category_approvers
            WHERE lower(attribute_key) = lower(%(attribute_key)s)
              AND lower(category_path) = lower(%(path)s)
              AND lower(principal_name) = lower(%(principal)s)
              AND principal_type = %(ptype)s
            """,
            {
                "attribute_key": approver.attribute_key,
                "path": approver.category_path,
                "principal": approver.principal_name,
                "ptype": approver.principal_type.value,
            },
        )
        if existing:
            return self.get_approver(str(existing[0]["approver_id"])) or approver
        self._pg.run(
            """
            INSERT INTO {schema}.sensitivity_category_approvers (
              approver_id, attribute_key, category_path, principal_name, principal_type,
              created_by, created_at
            ) VALUES (
              %(approver_id)s, %(attribute_key)s, %(category_path)s, %(principal_name)s,
              %(principal_type)s, %(created_by)s, %(created_at)s
            )
            """,
            {
                "approver_id": approver.approver_id,
                "attribute_key": approver.attribute_key,
                "category_path": approver.category_path,
                "principal_name": approver.principal_name,
                "principal_type": approver.principal_type.value,
                "created_by": approver.created_by,
                "created_at": approver.created_at,
            },
        )
        return approver

    def delete_approver(self, approver_id: str) -> bool:
        """Delete an approver if it exists.

        Args:
            approver_id: Primary key to remove.

        Returns:
            ``True`` if a row was present and deleted; ``False`` if missing.
        """
        before = self.get_approver(approver_id)
        if before is None:
            return False
        self._pg.run(
            "DELETE FROM {schema}.sensitivity_category_approvers WHERE approver_id = %(id)s",
            {"id": approver_id},
        )
        return True

    @staticmethod
    def _from_row(row: dict[str, Any]) -> CategoryApprover:
        """Map an approver table row to a ``CategoryApprover``."""
        return CategoryApprover(
            approver_id=row["approver_id"],
            attribute_key=str(row.get("attribute_key") or "sensitivity"),
            category_path=row["category_path"],
            principal_name=row["principal_name"],
            principal_type=PrincipalKind(row["principal_type"]),
            created_by=row.get("created_by"),
            created_at=row["created_at"],
        )


class LakebasePolicyExceptionRepository(PolicyExceptionRepository):
    """Lakebase store for global ABAC policy exceptions.

    Exceptions mark principals that should bypass masks/row filters; uniqueness is
    case-insensitive on principal name within a principal type.

    Args:
        pg: Schema-scoped query helper.
    """

    def __init__(self, pg: _Pg) -> None:
        self._pg = pg

    def list_exceptions(self) -> list[PolicyException]:
        """Return all stored policy exceptions.

        Returns:
            Exceptions ordered by principal type and name.
        """
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.policy_exceptions ORDER BY principal_type, principal_name"
        )
        return [self._from_row(r) for r in rows]

    def get_exception(self, exception_id: str) -> PolicyException | None:
        """Return one exception by id.

        Args:
            exception_id: Primary key.

        Returns:
            The exception, or ``None`` if missing.
        """
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.policy_exceptions WHERE exception_id = %(id)s",
            {"id": exception_id},
        )
        return self._from_row(rows[0]) if rows else None

    def upsert_exception(self, exception: PolicyException) -> PolicyException:
        """Insert a new exception or update note on the existing principal.

        Args:
            exception: Exception to insert, or note/created_by to apply on conflict.

        Returns:
            The stored exception after insert or update.
        """
        existing = self._pg.fetch(
            """
            SELECT exception_id FROM {schema}.policy_exceptions
            WHERE lower(principal_name) = lower(%(principal)s)
              AND principal_type = %(ptype)s
            """,
            {
                "principal": exception.principal_name,
                "ptype": exception.principal_type.value,
            },
        )
        if existing:
            exception_id = str(existing[0]["exception_id"])
            self._pg.run(
                """
                UPDATE {schema}.policy_exceptions
                SET note = %(note)s,
                    created_by = COALESCE(%(created_by)s, created_by)
                WHERE exception_id = %(id)s
                """,
                {
                    "id": exception_id,
                    "note": exception.note,
                    "created_by": exception.created_by,
                },
            )
            return self.get_exception(exception_id) or exception
        self._pg.run(
            """
            INSERT INTO {schema}.policy_exceptions (
              exception_id, principal_name, principal_type, note, created_by, created_at
            ) VALUES (
              %(exception_id)s, %(principal_name)s, %(principal_type)s,
              %(note)s, %(created_by)s, %(created_at)s
            )
            """,
            {
                "exception_id": exception.exception_id,
                "principal_name": exception.principal_name,
                "principal_type": exception.principal_type.value,
                "note": exception.note,
                "created_by": exception.created_by,
                "created_at": exception.created_at,
            },
        )
        return exception

    def delete_exception(self, exception_id: str) -> bool:
        """Delete a policy exception if it exists.

        Args:
            exception_id: Primary key to remove.

        Returns:
            ``True`` if a row was present and deleted; ``False`` if missing.
        """
        before = self.get_exception(exception_id)
        if before is None:
            return False
        self._pg.run(
            "DELETE FROM {schema}.policy_exceptions WHERE exception_id = %(id)s",
            {"id": exception_id},
        )
        return True

    @staticmethod
    def _from_row(row: dict[str, Any]) -> PolicyException:
        """Map an exception table row to a ``PolicyException`` (``system`` always false)."""
        return PolicyException(
            exception_id=row["exception_id"],
            principal_name=row["principal_name"],
            principal_type=PrincipalKind(row["principal_type"]),
            note=row.get("note"),
            created_by=row.get("created_by"),
            created_at=row["created_at"],
            system=False,
        )


class LakebaseAccessRequestRepository(AccessRequestRepository):
    """Lakebase store for sensitivity access requests and renewal dismissals.

    Args:
        pg: Schema-scoped query helper.
    """

    def __init__(self, pg: _Pg) -> None:
        self._pg = pg

    def create(self, request: AccessRequest) -> AccessRequest:
        """Insert a new access request.

        Args:
            request: Fully populated request to insert.

        Returns:
            The same ``request`` instance after a successful insert.
        """
        self._pg.run(
            """
            INSERT INTO {schema}.access_requests (
              request_id, requester_name, attribute_key, attribute_value,
              requested_expires_at, justification, status, decided_by, decided_at,
              decision_note, approved_expires_at, created_at
            ) VALUES (
              %(request_id)s, %(requester_name)s, %(attribute_key)s, %(attribute_value)s,
              %(requested_expires_at)s, %(justification)s, %(status)s, %(decided_by)s,
              %(decided_at)s, %(decision_note)s, %(approved_expires_at)s, %(created_at)s
            )
            """,
            self._params(request),
        )
        return request

    def get(self, request_id: str) -> AccessRequest | None:
        """Return one access request by id.

        Args:
            request_id: Primary key.

        Returns:
            The request, or ``None`` if missing.
        """
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.access_requests WHERE request_id = %(id)s",
            {"id": request_id},
        )
        return self._from_row(rows[0]) if rows else None

    def list_for_requester(self, requester_name: str) -> list[AccessRequest]:
        """Return all requests for a requester, newest first.

        Args:
            requester_name: Requester principal; matched case-insensitively.

        Returns:
            Requests ordered by ``created_at`` descending.
        """
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.access_requests "
            "WHERE lower(requester_name) = lower(%(name)s) "
            "ORDER BY created_at DESC",
            {"name": requester_name},
        )
        return [self._from_row(r) for r in rows]

    def list_by_status(self, status: AccessRequestStatus) -> list[AccessRequest]:
        """Return requests with the given status, newest first.

        Args:
            status: Status filter.

        Returns:
            Matching requests ordered by ``created_at`` descending.
        """
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.access_requests WHERE status = %(status)s ORDER BY created_at DESC",
            {"status": status.value},
        )
        return [self._from_row(r) for r in rows]

    def save(self, request: AccessRequest) -> AccessRequest:
        """Persist decision fields for an existing access request.

        Updates status, decision metadata, and approved expiry; other columns are left
        unchanged.

        Args:
            request: Request whose decision fields should be written.

        Returns:
            The same ``request`` instance after update.
        """
        self._pg.run(
            """
            UPDATE {schema}.access_requests SET
              status = %(status)s,
              decided_by = %(decided_by)s,
              decided_at = %(decided_at)s,
              decision_note = %(decision_note)s,
              approved_expires_at = %(approved_expires_at)s
            WHERE request_id = %(request_id)s
            """,
            self._params(request),
        )
        return request

    def has_pending_for(self, *, requester_name: str, attribute_key: str, attribute_value: str) -> bool:
        """Return whether a PENDING request already exists for this triple.

        Args:
            requester_name: Requester principal; matched case-insensitively.
            attribute_key: Attribute key being requested.
            attribute_value: Attribute value; matched case-insensitively.

        Returns:
            ``True`` if at least one PENDING row matches.
        """
        rows = self._pg.fetch(
            """
            SELECT 1 AS ok FROM {schema}.access_requests
            WHERE lower(requester_name) = lower(%(name)s)
              AND attribute_key = %(key)s
              AND lower(attribute_value) = lower(%(value)s)
              AND status = 'PENDING'
            LIMIT 1
            """,
            {"name": requester_name, "key": attribute_key, "value": attribute_value},
        )
        return bool(rows)

    def dismiss_renewal(self, assignment_id: str, expires_at: datetime, actor: str, at: datetime) -> None:
        """Record that a renewal reminder for an assignment expiry was dismissed.

        Args:
            assignment_id: Assignment whose expiry reminder is dismissed.
            expires_at: Expiry timestamp that identifies the dismissal key.
            actor: Principal who dismissed the reminder.
            at: Dismissal timestamp.
        """
        self._pg.run(
            """
            INSERT INTO {schema}.renewal_dismissals (
              assignment_id, expires_at, dismissed_by, dismissed_at
            ) VALUES (%(id)s, %(expires_at)s, %(actor)s, %(at)s)
            ON CONFLICT (assignment_id, expires_at) DO UPDATE SET
              dismissed_by = EXCLUDED.dismissed_by,
              dismissed_at = EXCLUDED.dismissed_at
            """,
            {"id": assignment_id, "expires_at": expires_at, "actor": actor, "at": at},
        )

    def is_renewal_dismissed(self, assignment_id: str, expires_at: datetime) -> bool:
        """Return whether a renewal dismissal exists for the assignment expiry.

        Args:
            assignment_id: Assignment id.
            expires_at: Expiry timestamp that forms the dismissal primary key.

        Returns:
            ``True`` if a dismissal row exists.
        """
        rows = self._pg.fetch(
            """
            SELECT 1 AS ok FROM {schema}.renewal_dismissals
            WHERE assignment_id = %(id)s AND expires_at = %(expires_at)s
            LIMIT 1
            """,
            {"id": assignment_id, "expires_at": expires_at},
        )
        return bool(rows)

    @staticmethod
    def _params(request: AccessRequest) -> dict[str, Any]:
        """Build named SQL parameters for create/save from an ``AccessRequest``."""
        return {
            "request_id": request.request_id,
            "requester_name": request.requester_name,
            "attribute_key": request.attribute_key,
            "attribute_value": request.attribute_value,
            "requested_expires_at": request.requested_expires_at,
            "justification": request.justification,
            "status": request.status.value,
            "decided_by": request.decided_by,
            "decided_at": request.decided_at,
            "decision_note": request.decision_note,
            "approved_expires_at": request.approved_expires_at,
            "created_at": request.created_at,
        }

    @staticmethod
    def _from_row(row: dict[str, Any]) -> AccessRequest:
        """Map an access_requests table row to an ``AccessRequest``."""
        return AccessRequest(
            request_id=row["request_id"],
            requester_name=row["requester_name"],
            attribute_key=row["attribute_key"],
            attribute_value=row["attribute_value"],
            requested_expires_at=row["requested_expires_at"],
            justification=row["justification"],
            status=AccessRequestStatus(row["status"]),
            decided_by=row.get("decided_by"),
            decided_at=row.get("decided_at"),
            decision_note=row.get("decision_note"),
            approved_expires_at=row.get("approved_expires_at"),
            created_at=row["created_at"],
        )


class LakebaseMaskRuleRepository(MaskRuleRepository):
    """Lakebase store for presentation mask rule definitions and masking config.

    Args:
        pg: Schema-scoped query helper.
    """

    def __init__(self, pg: _Pg) -> None:
        self._pg = pg

    def list_rules(self) -> list[MaskRuleDefinition]:
        """Return all mask rule definitions ordered by key.

        Returns:
            Rules sorted by ``rule_key``.
        """
        rows = self._pg.fetch("SELECT * FROM {schema}.mask_rule_definitions ORDER BY rule_key")
        return [self._from_row(r) for r in rows]

    def get_rule(self, rule_key: str) -> MaskRuleDefinition | None:
        """Return one mask rule by key (case-insensitive).

        Args:
            rule_key: Rule key to look up.

        Returns:
            The rule, or ``None`` if missing.
        """
        rows = self._pg.fetch(
            "SELECT * FROM {schema}.mask_rule_definitions WHERE lower(rule_key) = lower(%(key)s)",
            {"key": rule_key},
        )
        return self._from_row(rows[0]) if rows else None

    def upsert_rule(self, rule: MaskRuleDefinition) -> MaskRuleDefinition:
        """Insert or update a mask rule definition and return the stored row.

        Args:
            rule: Rule to persist.

        Returns:
            The row as stored after upsert (falls back to ``rule`` if reload fails).
        """
        self._pg.run(
            """
            INSERT INTO {schema}.mask_rule_definitions (
              rule_key, description, kind, handler, expression, params,
              validated_at, updated_by, updated_at
            ) VALUES (
              %(rule_key)s, %(description)s, %(kind)s, %(handler)s, %(expression)s, %(params)s,
              %(validated_at)s, %(updated_by)s, %(updated_at)s
            )
            ON CONFLICT (rule_key) DO UPDATE SET
              description = EXCLUDED.description,
              kind = EXCLUDED.kind,
              handler = EXCLUDED.handler,
              expression = EXCLUDED.expression,
              params = EXCLUDED.params,
              validated_at = EXCLUDED.validated_at,
              updated_by = EXCLUDED.updated_by,
              updated_at = EXCLUDED.updated_at
            """,
            {
                "rule_key": rule.rule_key,
                "description": rule.description,
                "kind": rule.kind.value,
                "handler": rule.handler,
                "expression": rule.expression,
                "params": Jsonb(rule.params),
                "validated_at": rule.validated_at,
                "updated_by": rule.updated_by,
                "updated_at": rule.updated_at or datetime.now().astimezone(),
            },
        )
        return self.get_rule(rule.rule_key) or rule

    def delete_rule(self, rule_key: str) -> bool:
        """Delete a mask rule if it exists (key matched case-insensitively).

        Args:
            rule_key: Rule key to remove.

        Returns:
            ``True`` if a row was present and deleted; ``False`` if missing.
        """
        before = self.get_rule(rule_key)
        if before is None:
            return False
        self._pg.run(
            "DELETE FROM {schema}.mask_rule_definitions WHERE lower(rule_key) = lower(%(key)s)",
            {"key": rule_key},
        )
        return True

    def get_config(self, key: str) -> str | None:
        """Return a masking config value by key.

        Args:
            key: Config key.

        Returns:
            Config string, or ``None`` if missing.
        """
        rows = self._pg.fetch(
            "SELECT config_value FROM {schema}.masking_config WHERE config_key = %(key)s",
            {"key": key},
        )
        return None if not rows else str(rows[0]["config_value"])

    def put_config(self, key: str, value: str, actor: str) -> None:
        """Upsert a masking config entry.

        Args:
            key: Config key.
            value: Config value string.
            actor: Principal recorded in ``updated_by``.
        """
        self._pg.run(
            """
            INSERT INTO {schema}.masking_config (config_key, config_value, updated_by, updated_at)
            VALUES (%(key)s, %(value)s, %(actor)s, now())
            ON CONFLICT (config_key) DO UPDATE SET
              config_value = EXCLUDED.config_value,
              updated_by = EXCLUDED.updated_by,
              updated_at = EXCLUDED.updated_at
            """,
            {"key": key, "value": value, "actor": actor},
        )

    @staticmethod
    def _from_row(row: dict[str, Any]) -> MaskRuleDefinition:
        """Map a mask_rule_definitions row to a ``MaskRuleDefinition``."""
        params = row.get("params") or {}
        if not isinstance(params, dict):
            params = {}
        kind_raw = row.get("kind") or MaskRuleKind.BUILTIN.value
        return MaskRuleDefinition(
            rule_key=row["rule_key"],
            description=row.get("description"),
            kind=MaskRuleKind(kind_raw),
            handler=str(row["handler"]),
            expression=row.get("expression"),
            params=params,
            validated_at=row.get("validated_at"),
            updated_by=row.get("updated_by"),
            updated_at=row.get("updated_at"),
        )


class LakebaseStorageProvider(StorageProvider):
    """Stores app state in a Lakebase Postgres database.

    The app service principal creates and owns the schema on first start, which is why the
    app must be deployed once before running locally against the same database.

    Args:
        config: App config with Postgres host/db/user, Lakebase endpoint, and schema.
        factory: Workspace client factory used to mint Lakebase OAuth credentials.

    Raises:
        NotConfiguredError: When required Postgres/Lakebase settings are missing or the
            schema name is not alphanumeric (underscores allowed).
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
        self._database = config.pg_database or ""
        # Account Admin SP (sync Job run-as) needs DML on tables the app SP owns.
        self._admin_sp = (config.admin_client_id or "").strip() or None
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
        self._attributes = LakebaseIdentityAttributeRepository(self._pg)
        self._category_approvers = LakebaseCategoryApproverRepository(self._pg)
        self._access_requests = LakebaseAccessRequestRepository(self._pg)
        self._mask_rules = LakebaseMaskRuleRepository(self._pg)
        self._policy_exceptions = LakebasePolicyExceptionRepository(self._pg)

    @property
    def settings(self) -> SettingsRepository:
        """Return the settings repository for this provider."""
        return self._settings

    @property
    def audit(self) -> AuditRepository:
        """Return the audit repository for this provider."""
        return self._audit

    @property
    def identity_attributes(self) -> IdentityAttributeRepository:
        """Return the identity-attribute repository for this provider."""
        return self._attributes

    @property
    def category_approvers(self) -> CategoryApproverRepository:
        """Return the category-approver repository for this provider."""
        return self._category_approvers

    @property
    def access_requests(self) -> AccessRequestRepository:
        """Return the access-request repository for this provider."""
        return self._access_requests

    @property
    def mask_rules(self) -> MaskRuleRepository:
        """Return the mask-rule repository for this provider."""
        return self._mask_rules

    @property
    def policy_exceptions(self) -> PolicyExceptionRepository:
        """Return the policy-exception repository for this provider."""
        return self._policy_exceptions

    def initialize(self, *, apply_ddl: bool = True) -> None:
        """Open the pool, apply DDL, and backfill columns for older workspaces.

        Idempotent: safe to call on every process start. Adds columns that older
        deployments may lack (governed tag keys, value source/SQL, mask kind/expression).

        Args:
            apply_ddl: When False (sync Job), only open the pool. Schema migrations stay
                with the app SP, which owns the Postgres tables.
        """
        self._pool.open(wait=True, timeout=30)
        if not apply_ddl:
            logger.info("Lakebase pool ready at %s (DDL skipped)", self._location)
            return
        try:
            self._pg.run(_DDL)
            # Existing workspaces created the table before governed_tag_keys existed.
            self._pg.run(
                "ALTER TABLE {schema}.identity_attribute_definitions "
                "ADD COLUMN IF NOT EXISTS governed_tag_keys JSONB NOT NULL DEFAULT '[]'::jsonb"
            )
            self._pg.run(
                "ALTER TABLE {schema}.identity_attribute_definitions "
                "ADD COLUMN IF NOT EXISTS value_source TEXT NOT NULL DEFAULT 'governed_tags'"
            )
            self._pg.run(
                "ALTER TABLE {schema}.identity_attribute_definitions ADD COLUMN IF NOT EXISTS value_sql TEXT"
            )
            self._pg.run(
                "ALTER TABLE {schema}.mask_rule_definitions "
                "ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT 'builtin'"
            )
            self._pg.run(
                "ALTER TABLE {schema}.mask_rule_definitions ADD COLUMN IF NOT EXISTS expression TEXT"
            )
            self._pg.run(
                "ALTER TABLE {schema}.mask_rule_definitions ADD COLUMN IF NOT EXISTS validated_at TIMESTAMPTZ"
            )
            # Approvers gained attribute_key so row-filter dimensions can have their own reviewers.
            self._pg.run(
                "ALTER TABLE {schema}.sensitivity_category_approvers "
                "ADD COLUMN IF NOT EXISTS attribute_key TEXT NOT NULL DEFAULT 'sensitivity'"
            )
            # Indexes are (re)built here, not in _DDL: Postgres resolves index columns before the
            # IF NOT EXISTS check, so referencing attribute_key in _DDL breaks older tables.
            self._pg.run("DROP INDEX IF EXISTS {schema}.sensitivity_category_approvers_uniq")
            self._pg.run("DROP INDEX IF EXISTS {schema}.sensitivity_category_approvers_path_idx")
            self._pg.run(
                "CREATE UNIQUE INDEX IF NOT EXISTS sensitivity_category_approvers_uniq "
                "ON {schema}.sensitivity_category_approvers ("
                "lower(attribute_key), lower(category_path), lower(principal_name), principal_type)"
            )
            self._pg.run(
                "CREATE INDEX IF NOT EXISTS sensitivity_category_approvers_path_idx "
                "ON {schema}.sensitivity_category_approvers (lower(attribute_key), lower(category_path))"
            )
            self._grant_admin_sp_access()
        except psycopg.errors.InsufficientPrivilege:
            logger.warning(
                "Skipping Lakebase DDL: caller is not the table owner "
                "(tables/indexes already exist); continuing with existing schema"
            )
        logger.info("Lakebase schema %s ready", self._location)

    def _grant_admin_sp_access(self) -> None:
        """Grant the Account Admin SP DML on the app schema (for the sync Job).

        The app SP owns the tables after DDL. The sync Job runs as the Admin SP and
        needs SELECT/INSERT/UPDATE/DELETE. Idempotent; no-ops when Admin SP is unset.
        """
        if not self._admin_sp:
            return
        from psycopg import sql

        role = sql.Identifier(self._admin_sp)
        try:
            with self._pool.connection() as conn, conn.cursor() as cur:
                if self._database:
                    cur.execute(
                        sql.SQL("GRANT CREATE ON DATABASE {} TO {}").format(
                            sql.Identifier(self._database), role
                        )
                    )
                cur.execute(
                    sql.SQL("GRANT USAGE, CREATE ON SCHEMA {} TO {}").format(
                        sql.Identifier(self._schema), role
                    )
                )
                cur.execute(
                    sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}").format(
                        sql.Identifier(self._schema), role
                    )
                )
                cur.execute(
                    sql.SQL(
                        "ALTER DEFAULT PRIVILEGES IN SCHEMA {} "
                        "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
                    ).format(sql.Identifier(self._schema), role)
                )
            logger.info("Granted Lakebase DML on %s to Admin SP %s", self._schema, self._admin_sp)
        except Exception as exc:  # noqa: BLE001 - best-effort; Job may already have grants
            logger.warning("Could not grant Lakebase DML to Admin SP %s: %s", self._admin_sp, exc)

    def health(self) -> StorageHealth:
        """Probe connectivity with ``SELECT 1``.

        Returns:
            Healthy status with database.schema detail, or unhealthy with the error text.
        """
        try:
            self._pg.fetch("SELECT 1 AS ok")
            return StorageHealth(backend=self.kind, healthy=True, detail=self._location)
        except Exception as exc:  # noqa: BLE001 - surfaced to admins as a health message
            return StorageHealth(backend=self.kind, healthy=False, detail=str(exc))

    def close(self) -> None:
        """Close the connection pool and release resources."""
        self._pool.close()
