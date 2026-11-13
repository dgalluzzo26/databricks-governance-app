"""Unity Catalog Delta mirror of identity attributes for ABAC UDFs.

Column-mask and row-filter UDFs run inside Unity Catalog and cannot query Lakebase, so the
app mirrors identity attribute definitions and assignments into Delta tables in the app
schema. This module owns the mirror tables' DDL, exposes a Delta-backed repository for
incremental writes, and supports a full resync from primary storage.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime

from backend.app.clients.sql import SqlExecutor, string_literal
from backend.app.core.config import AppConfig
from backend.app.models.identity_attribute import (
    AttributeAssignmentFilter,
    AttributeDefinition,
    IdentityAttributeAssignment,
)
from backend.app.repositories.base import IdentityAttributeRepository
from backend.app.repositories.delta import DeltaIdentityAttributeRepository, _DeltaTable

logger = logging.getLogger(__name__)

_DEFINITIONS_DDL = """
CREATE TABLE IF NOT EXISTS {fqn} (
  attribute_key STRING NOT NULL,
  description STRING,
  governed_tag_keys STRING NOT NULL,
  allowed_values STRING NOT NULL,
  created_by STRING,
  created_at TIMESTAMP,
  updated_by STRING,
  updated_at TIMESTAMP
)
COMMENT 'UC mirror of Lakebase identity attribute definitions for mask/row-filter UDFs'
"""

_ASSIGNMENTS_DDL = """
CREATE TABLE IF NOT EXISTS {fqn} (
  assignment_id STRING NOT NULL,
  principal_name STRING NOT NULL,
  principal_type STRING NOT NULL,
  attribute_key STRING NOT NULL,
  attribute_value STRING NOT NULL,
  source STRING NOT NULL,
  granted_by STRING,
  justification STRING,
  expires_at TIMESTAMP,
  created_at TIMESTAMP,
  revoked_at TIMESTAMP,
  revoked_by STRING
)
COMMENT 'UC mirror of Lakebase identity attribute assignments for mask/row-filter UDFs'
"""


class IdentityUcSync:
    """Mirrors app-managed identity attributes into Unity Catalog Delta tables.

    Lakebase remains the OLTP source of truth for the app UI. Column-mask and row-filter
    UDFs can only read UC tables, so this mirror is what those UDFs look up.

    Args:
        sql: SQL executor used for mirror DDL and writes.
        config: App configuration providing the app catalog and schema.

    Attributes:
        repo: Delta-backed repository for incremental mirror writes.
        lock: Held by ``full_sync`` and by callers around incremental writes, so a write made
            while a resync is reading primary storage is not overwritten by it.
    """

    def __init__(self, sql: SqlExecutor, config: AppConfig) -> None:
        self._sql = sql
        self.lock = threading.RLock()
        self._catalog = config.app_catalog
        self._schema = config.app_schema
        self._definitions = _DeltaTable(sql, self._catalog, self._schema, "identity_attribute_definitions")
        self._assignments = _DeltaTable(sql, self._catalog, self._schema, "identity_attributes")
        self.repo = DeltaIdentityAttributeRepository(self._definitions, self._assignments)

    @property
    def location(self) -> str:
        """The ``catalog.schema`` holding the mirror tables."""
        return f"{self._catalog}.{self._schema}"

    def ensure_tables(self) -> None:
        """Create the mirror definition and assignment tables if they do not exist.

        Runs ``CREATE TABLE IF NOT EXISTS`` DDL against Unity Catalog.

        Raises:
            UpstreamError: If the DDL fails.
            NotConfiguredError: If no SQL warehouse is configured.
        """
        self._sql.execute(_DEFINITIONS_DDL.format(fqn=self._definitions.fqn))
        self._sql.execute(_ASSIGNMENTS_DDL.format(fqn=self._assignments.fqn))
        logger.info("UC identity mirror tables ready in %s", self.location)

    def full_sync(self, source: IdentityAttributeRepository) -> dict[str, int]:
        """Replace the UC mirror with the current Lakebase (or other primary) contents.

        Each table is replaced by one ``INSERT OVERWRITE`` (Delta commits it atomically), so
        UDFs never see a partly empty mirror. Definitions are stored with empty allowed values
        (live tag resolution stays in the app); assignments include inactive ones so
        revocation history is preserved. Above ``_OVERWRITE_CHUNK`` rows, chunks are loaded
        into a staging table first and swapped in with a single overwrite.

        Args:
            source: Primary identity attribute repository to copy from.

        Returns:
            Counts of mirrored ``definitions`` and ``assignments``.

        Raises:
            UpstreamError: If any mirror statement fails.
            NotConfiguredError: If no SQL warehouse is configured.
        """
        self.ensure_tables()
        with self.lock:
            definitions = source.list_definitions()
            assignments = source.list_assignments(AttributeAssignmentFilter(include_inactive=True))
            self._overwrite(
                self._definitions,
                "identity_attribute_definitions",
                _DEFINITION_COLUMNS,
                [_definition_row(d) for d in definitions],
            )
            self._overwrite(
                self._assignments,
                "identity_attributes",
                _ASSIGNMENT_COLUMNS,
                [_assignment_row(a) for a in assignments],
            )

        counts = {"definitions": len(definitions), "assignments": len(assignments)}
        logger.info(
            "Synced identity attributes to UC (%s): %s definitions, %s assignments",
            self.location,
            counts["definitions"],
            counts["assignments"],
        )
        return counts

    def _overwrite(self, table: _DeltaTable, name: str, columns: str, rows: list[str]) -> None:
        """Atomically replace every row of ``table`` with ``rows`` (SQL value tuples)."""
        if not rows:
            self._sql.execute(f"INSERT OVERWRITE {table.fqn} SELECT * FROM {table.fqn} WHERE false")
            return
        if len(rows) <= _OVERWRITE_CHUNK:
            self._sql.execute(f"INSERT OVERWRITE {table.fqn} ({columns}) VALUES {', '.join(rows)}")
            return
        staging = _DeltaTable(self._sql, self._catalog, self._schema, f"{name}__staging")
        self._sql.execute(f"CREATE OR REPLACE TABLE {staging.fqn} AS SELECT * FROM {table.fqn} WHERE false")
        try:
            for start in range(0, len(rows), _OVERWRITE_CHUNK):
                chunk = rows[start : start + _OVERWRITE_CHUNK]
                self._sql.execute(f"INSERT INTO {staging.fqn} ({columns}) VALUES {', '.join(chunk)}")
            self._sql.execute(
                f"INSERT OVERWRITE {table.fqn} ({columns}) SELECT {columns} FROM {staging.fqn}"
            )
        finally:
            self._sql.execute(f"DROP TABLE IF EXISTS {staging.fqn}")


# Rows per VALUES statement; keeps statements well under the Statement Execution size limit.
_OVERWRITE_CHUNK = 1000

_DEFINITION_COLUMNS = (
    "attribute_key, description, governed_tag_keys, allowed_values, "
    "created_by, created_at, updated_by, updated_at"
)
_ASSIGNMENT_COLUMNS = (
    "assignment_id, principal_name, principal_type, attribute_key, attribute_value, "
    "source, granted_by, justification, expires_at, created_at, revoked_at, revoked_by"
)


def _text(value: str | None) -> str:
    return "NULL" if value is None else string_literal(value)


def _timestamp(value: datetime | None) -> str:
    return "NULL" if value is None else f"CAST({string_literal(value.isoformat())} AS TIMESTAMP)"


def _definition_row(definition: AttributeDefinition) -> str:
    now = datetime.now(UTC)
    return "(" + ", ".join(
        (
            _text(definition.key),
            _text(definition.description),
            _text(json.dumps(definition.governed_tag_keys)),
            _text(json.dumps([])),
            _text(definition.created_by),
            _timestamp(definition.created_at or now),
            _text(definition.updated_by),
            _timestamp(definition.updated_at or now),
        )
    ) + ")"


def _assignment_row(assignment: IdentityAttributeAssignment) -> str:
    return "(" + ", ".join(
        (
            _text(assignment.assignment_id),
            _text(assignment.principal_name),
            _text(assignment.principal_type.value),
            _text(assignment.attribute_key),
            _text(assignment.attribute_value),
            _text(assignment.source.value),
            _text(assignment.granted_by),
            _text(assignment.justification),
            _timestamp(assignment.expires_at),
            _timestamp(assignment.created_at),
            _timestamp(assignment.revoked_at),
            _text(assignment.revoked_by),
        )
    ) + ")"
