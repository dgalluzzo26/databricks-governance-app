"""Unity Catalog Delta mirror of identity attributes for ABAC UDFs.

Column-mask and row-filter UDFs run inside Unity Catalog and cannot query Lakebase, so the
app mirrors identity attribute definitions and assignments into Delta tables in the app
schema. This module owns the mirror tables' DDL, exposes a Delta-backed repository for
incremental writes, and supports a full resync from primary storage.
"""

from __future__ import annotations

import logging

from backend.app.clients.sql import SqlExecutor
from backend.app.core.config import AppConfig
from backend.app.models.identity_attribute import AttributeAssignmentFilter, AttributeDefinition
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
    """

    def __init__(self, sql: SqlExecutor, config: AppConfig) -> None:
        self._sql = sql
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

        Ensures the tables exist, deletes all mirror rows, then re-inserts every definition
        (with empty allowed values) and every assignment, including inactive ones so
        revocation history is preserved. Not transactional: UDFs may briefly see an empty
        mirror while the resync runs.

        Args:
            source: Primary identity attribute repository to copy from.

        Returns:
            Counts of mirrored ``definitions`` and ``assignments``.

        Raises:
            UpstreamError: If any mirror statement fails.
            NotConfiguredError: If no SQL warehouse is configured.
        """
        self.ensure_tables()
        definitions = source.list_definitions()
        assignments = source.list_assignments(AttributeAssignmentFilter(include_inactive=True))

        self._sql.execute(f"DELETE FROM {self._definitions.fqn}")
        for definition in definitions:
            # Store empty allowed_values; live tag resolution stays in the app.
            self.repo.upsert_definition(
                AttributeDefinition(
                    key=definition.key,
                    description=definition.description,
                    governed_tag_keys=definition.governed_tag_keys,
                    allowed_values=[],
                    created_by=definition.created_by,
                    created_at=definition.created_at,
                    updated_by=definition.updated_by,
                    updated_at=definition.updated_at,
                )
            )

        self._sql.execute(f"DELETE FROM {self._assignments.fqn}")
        for assignment in assignments:
            self.repo.add_assignment(assignment)

        counts = {"definitions": len(definitions), "assignments": len(assignments)}
        logger.info(
            "Synced identity attributes to UC (%s): %s definitions, %s assignments",
            self.location,
            counts["definitions"],
            counts["assignments"],
        )
        return counts
