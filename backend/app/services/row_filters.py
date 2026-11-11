"""App-managed row filters driven by identity attribute dimensions.

A row-filter dimension is a non-governed-tag identity attribute definition (manual or
SQL-seeded vocabulary) whose key is also a value of the ``row_filter`` governed tag. Tagging a
table column with ``row_filter = <dimension>`` attaches it. One metastore ``ROW FILTER`` policy
matches every registered dimension and calls ``has_row_access``, which reads the Unity Catalog
identity attribute mirror: a caller sees a row when they (directly or via a group) hold the
column's value or ``*`` for that dimension, and nothing otherwise. Requests for dimension
values are always reviewed by app admins.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from datetime import UTC, datetime

from backend.app.clients.cache import TtlCache
from backend.app.clients.sql import SqlExecutor, quote_identifier, string_literal
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.core.errors import InvalidRequestError, NotFoundError, UpstreamError
from backend.app.models.audit import AuditEvent
from backend.app.models.identity_attribute import (
    AttributeDefinition,
    UpsertAttributeDefinitionRequest,
    ValueSource,
)
from backend.app.models.row_filter import (
    AttachRowFilterRequest,
    RowFilterAttachment,
    RowFilterAttachmentList,
    RowFilterDimension,
    RowFilterDimensionList,
    RowFilterPackStatus,
    SeedPreview,
    UpsertRowFilterDimensionRequest,
)
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.governed_tags import GovernedTagService
from backend.app.services.identity_attributes import IdentityAttributeService

logger = logging.getLogger(__name__)

_POLICY_NAME = re.compile(r"^[A-Za-z0-9_]+$")
_MATCHED_DIMENSION = re.compile(r"has_tag_value\(\s*'[^']*'\s*,\s*'([^']*)'\s*\)")


def _is_missing_tag_value_error(exc: Exception) -> bool:
    """Return True when Unity Catalog rejected a policy because a tag value is missing."""
    text = str(exc)
    return "Invalid tag value" in text or "UC_INVALID_POLICY_CONDITION" in text


def build_function_sql(function_name: str, mirror_table: str) -> str:
    """Return DDL for the ``has_row_access`` row-filter UDF.

    The function is default-deny: it returns true only when an active, unexpired mirror
    assignment for the dimension matches the session user (USER) or one of their account
    groups (GROUP) and holds ``*`` or the row's value (case-insensitive).

    Args:
        function_name: Fully qualified, quoted function name.
        mirror_table: Fully qualified, quoted UC identity attribute assignments mirror.

    Returns:
        A ``CREATE OR REPLACE FUNCTION`` statement.
    """
    return f"""CREATE OR REPLACE FUNCTION {function_name}(
  row_value STRING,
  dim STRING
)
RETURNS BOOLEAN
LANGUAGE SQL
READS SQL DATA
COMMENT 'App-managed row filter: true when the caller holds dim=row_value or dim=* (default deny)'
RETURN EXISTS (
  SELECT 1
  FROM {mirror_table} a
  WHERE lower(a.attribute_key) = lower(dim)
    AND a.revoked_at IS NULL
    AND (a.expires_at IS NULL OR a.expires_at > current_timestamp())
    AND (
      (a.principal_type = 'USER' AND lower(a.principal_name) = lower(session_user()))
      OR (a.principal_type = 'GROUP' AND is_account_group_member(a.principal_name))
    )
    AND (
      a.attribute_value = '*'
      OR lower(a.attribute_value) = lower(row_value)
    )
)"""


def build_policy_sql(
    policy_name: str,
    function_name: str,
    tag_key: str,
    dimensions: list[str],
    except_principals: list[str],
    *,
    enrollment_tag_key: str = "data_access_controlled",
    enrollment_tag_value: str = "true",
) -> str:
    """Return DDL for the metastore row-filter policy covering all dimensions.

    The policy applies to ``account users`` (minus EXCEPT principals) on enrollment-tagged
    catalogs when a column is tagged ``<tag_key> = <dimension>``, and passes that column's
    value and dimension to the row-filter function.

    Args:
        policy_name: Validated policy name.
        function_name: Fully qualified, quoted ``has_row_access`` name.
        tag_key: Row-filter governed tag key.
        dimensions: Registered dimension keys (must be non-empty).
        except_principals: Principals exempt from the filter.
        enrollment_tag_key: Catalog enrollment tag key used in the ``WHEN`` clause.
        enrollment_tag_value: Required enrollment tag value.

    Returns:
        A ``CREATE OR REPLACE POLICY ... ON METASTORE`` statement.
    """
    from backend.app.services.catalog_enrollment import enrollment_when_clause

    match = "\n  OR ".join(
        f"has_tag_value({string_literal(tag_key)}, {string_literal(dim)})" for dim in dimensions
    )
    except_clause = (
        "\nEXCEPT " + ", ".join(f"`{p.replace('`', '')}`" for p in except_principals)
        if except_principals
        else ""
    )
    when_clause = enrollment_when_clause(enrollment_tag_key, enrollment_tag_value)
    return f"""CREATE OR REPLACE POLICY {policy_name} ON METASTORE
COMMENT 'App-managed row filter over {tag_key} dimensions: {", ".join(dimensions)}'
ROW FILTER {function_name}
TO `account users`{except_clause}
FOR TABLES
{when_clause}
MATCH COLUMNS (
  {match}
) AS c
USING COLUMNS (c, get_column_tag_value(c, {string_literal(tag_key)}))"""


def split_table_name(full_name: str) -> tuple[str, str, str]:
    """Split and validate a ``catalog.schema.table`` name (backticks are stripped).

    Args:
        full_name: Three-part table name.

    Returns:
        The catalog, schema, and table names.

    Raises:
        InvalidRequestError: If the name is not three non-empty parts or a part is not a
            valid identifier.
    """
    parts = [part.strip().strip("`") for part in full_name.strip().split(".")]
    if len(parts) != 3 or not all(parts):
        raise InvalidRequestError("Table must be a three-part name: catalog.schema.table")
    for part in parts:
        quote_identifier(part)
    return parts[0], parts[1], parts[2]


class RowFilterService:
    """Row-filter dimensions: definitions + governed tag values + one metastore policy.

    Args:
        config: App configuration (tag key, policy name, catalog/schema names).
        workspace: Workspace client factory providing SP ids for the EXCEPT clause.
        sql: SQL executor for UDF/policy DDL, tagging, and information_schema queries.
        storage: Primary storage for definitions and audit events.
        governed_tags: Governed tag service used to register dimensions as tag values.
        identity_attributes: Identity attribute service that owns dimension definitions.
        metadata_cache: Cache whose ABAC policy entries are invalidated after policy changes.
        policy_exceptions: Optional ``PolicyExceptionService`` supplying EXCEPT principals.

    Raises:
        ValueError: If the configured row-filter policy name is not alphanumeric/underscore.
    """

    def __init__(
        self,
        config: AppConfig,
        workspace: WorkspaceClientFactory,
        sql: SqlExecutor,
        storage: StorageProvider,
        governed_tags: GovernedTagService,
        identity_attributes: IdentityAttributeService,
        metadata_cache: TtlCache | None = None,
        policy_exceptions=None,
    ) -> None:
        self._config = config
        self._workspace = workspace
        self._sql = sql
        self._storage = storage
        self._governed_tags = governed_tags
        self._identity = identity_attributes
        self._metadata_cache = metadata_cache
        self._policy_exceptions = policy_exceptions
        if not _POLICY_NAME.match(config.row_filter_policy_name):
            raise ValueError(f"Invalid row filter policy name {config.row_filter_policy_name!r}")

    @property
    def tag_key(self) -> str:
        """The governed tag key whose values name row-filter dimensions."""
        return self._config.row_filter_tag_key

    @property
    def function_name(self) -> str:
        """Quoted FQN of the ``has_row_access`` UDF."""
        return ".".join(
            quote_identifier(part)
            for part in (self._config.app_catalog, self._config.abac_function_schema, "has_row_access")
        )

    @property
    def mirror_table(self) -> str:
        """Quoted FQN of the UC identity attribute assignments mirror read by the UDF."""
        return ".".join(
            quote_identifier(part)
            for part in (self._config.app_catalog, self._config.app_schema, "identity_attributes")
        )

    # -- dimensions ---------------------------------------------------------------------

    def list_dimensions(self) -> RowFilterDimensionList:
        """List registered dimensions with value samples and attachment counts.

        Returns:
            Dimensions; attachment counts are zero if the tag lookup fails.

        Raises:
            InvalidRequestError: If the row-filter governed tag does not exist.
            UpstreamError: If governed tags cannot be read.
        """
        definitions = self._dimension_definitions()
        counts = self._attachment_counts()
        dims = [self._to_dimension(d, counts.get(d.key.lower(), 0)) for d in definitions]
        return RowFilterDimensionList(dimensions=dims, total=len(dims))

    def upsert_dimension(
        self, request: UpsertRowFilterDimensionRequest, actor: CurrentUser
    ) -> RowFilterDimension:
        """Create or update a dimension and apply it to Unity Catalog.

        Upserts the identity attribute definition (mirrored to UC and audited), adds the key
        to the row-filter governed tag, and runs ``ensure_pack`` to recreate the UDF and
        metastore policy.

        Args:
            request: Dimension key, description, and manual or SQL value source.
            actor: The admin making the change.

        Returns:
            The saved dimension.

        Raises:
            InvalidRequestError: If the key already exists as a governed-tag definition or
                the seed query is invalid.
            NotConfiguredError: If a SQL seed is requested without a SQL warehouse.
            UpstreamError: If governed tag updates or UC SQL fail.
        """
        existing = self._storage.identity_attributes.get_definition(request.key)
        if existing is not None and existing.value_source == ValueSource.GOVERNED_TAGS:
            raise InvalidRequestError(
                f"Attribute '{request.key}' already exists as a governed-tag definition"
            )
        definition = self._identity.upsert_definition(
            UpsertAttributeDefinitionRequest(
                key=request.key,
                description=request.description,
                value_source=request.value_source,
                allowed_values=request.allowed_values,
                value_sql=request.value_sql,
            ),
            actor,
        )
        self._governed_tags.ensure_values(self.tag_key, [definition.key])
        self.ensure_pack(actor)
        return self._to_dimension(definition, self._attachment_counts().get(definition.key, 0))

    def delete_dimension(self, key: str, actor: CurrentUser) -> None:
        """Delete an unattached dimension and reapply the policy pack.

        The governed tag value is left in place. Deleting the last dimension drops the
        metastore row-filter policy.

        Args:
            key: Dimension key (case-insensitive).
            actor: The admin making the change.

        Raises:
            NotFoundError: If the dimension does not exist.
            InvalidRequestError: If columns are still attached or active assignments exist.
            UpstreamError: If UC SQL fails.
        """
        dim = self._require_dimension(key)
        attached = self._attachment_counts().get(dim.key, 0)
        if attached:
            raise InvalidRequestError(
                f"Detach '{dim.key}' from {attached} column(s) before deleting the dimension"
            )
        self._identity.delete_definition(dim.key, actor)
        self.ensure_pack(actor)

    def preview_seed(self, value_sql: str) -> SeedPreview:
        """Preview the values a SQL seed query would produce, without saving.

        Args:
            value_sql: Candidate SELECT/WITH seed query.

        Returns:
            Up to 100 sample values and the total count.

        Raises:
            NotConfiguredError: If no SQL warehouse is configured.
            InvalidRequestError: If the query is invalid, too large, or returns nothing.
        """
        values = self._identity.preview_seed(value_sql)
        return SeedPreview(values=values[:100], total=len(values))

    def refresh_dimension(self, key: str, actor: CurrentUser) -> RowFilterDimension:
        """Re-run a SQL-seeded dimension's query and store the new vocabulary.

        Args:
            key: Dimension key (case-insensitive).
            actor: The admin triggering the refresh.

        Returns:
            The refreshed dimension.

        Raises:
            NotFoundError: If the dimension does not exist.
            InvalidRequestError: If it is not SQL-seeded or the seed query fails validation.
            NotConfiguredError: If no SQL warehouse is configured.
        """
        dim = self._require_dimension(key)
        refreshed = self._identity.refresh_definition(dim.key, actor)
        return self._to_dimension(refreshed, self._attachment_counts().get(dim.key, 0))

    # -- policy pack --------------------------------------------------------------------

    def pack_status(self) -> RowFilterPackStatus:
        """Report whether tag values and the metastore policy match the dimensions.

        In sync means every dimension is a tag value, the policy exists exactly when there
        are dimensions, its MATCH clause covers exactly the registered dimensions, and its
        EXCEPT list includes every required exempt principal.

        Returns:
            The policy's current state, the registered dimensions, and ``in_sync``.

        Raises:
            InvalidRequestError: If the row-filter governed tag does not exist.
            UpstreamError: If governed tags or policy definitions cannot be read.
        """
        dims = [d.key for d in self._dimension_definitions()]
        tag_values = self._governed_tags.get_tag(self.tag_key).values
        rows = self._sql.query(
            "SELECT match_columns, except_principals FROM system.information_schema.abac_policy_definitions "
            "WHERE policy_name = :name AND upper(on_securable_type) = 'METASTORE'",
            {"name": self._config.row_filter_policy_name},
        )
        match = str(rows[0].get("match_columns") or "") if rows else None
        except_principals = _parse_list(rows[0].get("except_principals")) if rows else []
        matched = set(_MATCHED_DIMENSION.findall(match or ""))
        missing_tag = [d for d in dims if d not in tag_values]
        in_sync = (
            not missing_tag
            and (bool(rows) if dims else not rows)
            and matched == set(dims)
            and set(self._except_principals()) <= set(except_principals)
        )
        return RowFilterPackStatus(
            tag_key=self.tag_key,
            tag_values=tag_values,
            function_name=self.function_name,
            policy_name=self._config.row_filter_policy_name,
            policy_exists=bool(rows),
            policy_match_columns=match,
            policy_except_principals=except_principals,
            dimensions=dims,
            missing_tag_values=missing_tag,
            in_sync=in_sync,
        )

    def ensure_pack(self, actor: CurrentUser) -> RowFilterPackStatus:
        """Make the tag values, UDF and metastore policy match the current dimensions.

        With dimensions: ensures tag values, recreates ``has_row_access``, and recreates the
        metastore policy with current EXCEPT principals. Without dimensions: drops the
        policy. Either way invalidates cached ABAC policy definitions and records a
        ``row_filter.pack_ensured`` audit event.

        Args:
            actor: The admin applying the pack.

        Returns:
            The pack status after applying.

        Raises:
            UpstreamError: If governed tag updates or UC SQL fail.
        """
        dims = [d.key for d in self._dimension_definitions()]
        policy_name = self._config.row_filter_policy_name
        if dims:
            self._governed_tags.ensure_values(self.tag_key, dims)
            self._sql.execute(build_function_sql(self.function_name, self.mirror_table))
            self._create_row_filter_policy(policy_name, dims)
        else:
            self._sql.execute(f"DROP POLICY IF EXISTS {policy_name} ON METASTORE")
        if self._metadata_cache is not None:
            self._metadata_cache.invalidate(("abac-policy-definitions",))
        self._audit("row_filter.pack_ensured", actor.user_name, {"dimensions": dims})
        return self.pack_status()

    def _create_row_filter_policy(self, policy_name: str, dims: list[str]) -> None:
        """Create or replace the metastore row-filter policy after tag values exist.

        Unity Catalog rejects ``has_tag_value`` for a value that is not yet on the governed
        tag. If that happens, add the missing values and retry.

        Args:
            policy_name: Metastore policy name.
            dims: Registered dimension keys used in the MATCH clause.

        Raises:
            UpstreamError: If policy DDL fails for a reason other than a missing tag value,
                or still fails after retries.
        """
        enrollment_key, enrollment_value = self._enrollment_tag()
        # Enrollment may be an ungoverned catalog tag when tag-policy quota is exhausted.
        try:
            self._governed_tags.ensure_values(enrollment_key, [enrollment_value])
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not ensure governed enrollment tag %s=%s: %s",
                enrollment_key,
                enrollment_value,
                exc,
            )
        statement = build_policy_sql(
            policy_name,
            self.function_name,
            self.tag_key,
            dims,
            self._except_principals(),
            enrollment_tag_key=enrollment_key,
            enrollment_tag_value=enrollment_value,
        )
        last_error: UpstreamError | None = None
        for attempt in range(5):
            try:
                self._sql.execute(statement)
                return
            except UpstreamError as exc:
                last_error = exc
                if not _is_missing_tag_value_error(exc):
                    raise
                logger.warning(
                    "Row-filter policy rejected missing tag value (attempt %s); adding values and retrying",
                    attempt + 1,
                )
                self._governed_tags.ensure_values(self.tag_key, dims)
                time.sleep(0.6 * (attempt + 1))
        assert last_error is not None
        raise last_error

    # -- attachments --------------------------------------------------------------------

    def list_attachments(self) -> RowFilterAttachmentList:
        """List columns tagged with a registered dimension (first 1000).

        Returns:
            Table, column, and dimension for each attachment.

        Raises:
            UpstreamError: If governed tags or column tags cannot be read.
        """
        dims = [d.key for d in self._dimension_definitions()]
        rows = self._attachment_rows(dims)
        items = [
            RowFilterAttachment(
                table_full_name=f"{r['catalog_name']}.{r['schema_name']}.{r['table_name']}",
                column_name=str(r["column_name"]),
                dimension=str(r["tag_value"]),
            )
            for r in rows
        ]
        return RowFilterAttachmentList(attachments=items, total=len(items))

    def attach(self, request: AttachRowFilterRequest, actor: CurrentUser) -> RowFilterAttachment:
        """Attach a dimension to a table column by setting the row-filter column tag.

        Runs ``ALTER TABLE ... ALTER COLUMN ... SET TAGS`` in Unity Catalog, so the existing
        metastore policy starts filtering the table immediately. Only one filtered column
        per table is supported; re-tagging the same column replaces its dimension. Records a
        ``row_filter.attached`` audit event.

        Args:
            request: Table full name, column name, and dimension key.
            actor: The admin making the change.

        Returns:
            The new attachment.

        Raises:
            NotFoundError: If the dimension does not exist.
            InvalidRequestError: If the table or column name is invalid or another column on
                the table already has a dimension.
            UpstreamError: If the tagging SQL fails.
        """
        dim = self._require_dimension(request.dimension)
        catalog, schema, table = split_table_name(request.table_full_name)
        column = request.column_name.strip()
        quote_identifier(column)
        dims = [d.key for d in self._dimension_definitions()]
        for row in self._attachment_rows(dims, table=(catalog, schema, table)):
            if str(row["column_name"]).lower() == column.lower():
                continue
            raise InvalidRequestError(
                f"Table already has row filter dimension '{row['tag_value']}' on column "
                f"'{row['column_name']}'. Only one dimension per table is supported."
            )
        self._sql.execute(
            f"ALTER TABLE {_fqn(catalog, schema, table)} ALTER COLUMN {quote_identifier(column)} "
            f"SET TAGS ({string_literal(self.tag_key)} = {string_literal(dim.key)})"
        )
        attachment = RowFilterAttachment(
            table_full_name=f"{catalog}.{schema}.{table}", column_name=column, dimension=dim.key
        )
        self._audit("row_filter.attached", actor.user_name, attachment.model_dump())
        return attachment

    def detach(self, table_full_name: str, column_name: str, actor: CurrentUser) -> None:
        """Remove the row-filter tag from a column so the policy no longer filters the table.

        Runs ``ALTER TABLE ... ALTER COLUMN ... UNSET TAGS`` in Unity Catalog and records a
        ``row_filter.detached`` audit event.

        Args:
            table_full_name: Three-part table name.
            column_name: Tagged column.
            actor: The admin making the change.

        Raises:
            InvalidRequestError: If the table or column name is invalid.
            UpstreamError: If the SQL fails.
        """
        catalog, schema, table = split_table_name(table_full_name)
        column = column_name.strip()
        self._sql.execute(
            f"ALTER TABLE {_fqn(catalog, schema, table)} ALTER COLUMN {quote_identifier(column)} "
            f"UNSET TAGS ({string_literal(self.tag_key)})"
        )
        self._audit(
            "row_filter.detached",
            actor.user_name,
            {"table_full_name": f"{catalog}.{schema}.{table}", "column_name": column},
        )

    # -- helpers ------------------------------------------------------------------------

    def _dimension_definitions(self) -> list[AttributeDefinition]:
        """Return non-governed-tag definitions whose key is a row-filter tag value."""
        tag_values = {value.lower() for value in self._governed_tags.get_tag(self.tag_key).values}
        return [
            d
            for d in self._identity.list_definitions().definitions
            if d.value_source != ValueSource.GOVERNED_TAGS and d.key.lower() in tag_values
        ]

    def _require_dimension(self, key: str) -> AttributeDefinition:
        """Return the dimension definition matching ``key`` (case-insensitive).

        Raises:
            NotFoundError: If no registered dimension matches.
        """
        wanted = key.strip().lower()
        for definition in self._dimension_definitions():
            if definition.key.lower() == wanted:
                return definition
        raise NotFoundError(f"Row filter dimension '{key}' was not found")

    def _attachment_rows(
        self, dims: list[str], *, table: tuple[str, str, str] | None = None
    ) -> list[dict]:
        """Query column tags carrying any of ``dims``, optionally for a single table."""
        if not dims:
            return []
        values = ", ".join(string_literal(d) for d in dims)
        where = [f"tag_name = {string_literal(self.tag_key)}", f"lower(tag_value) IN ({values})"]
        if table is not None:
            where += [
                f"catalog_name = {string_literal(table[0])}",
                f"schema_name = {string_literal(table[1])}",
                f"table_name = {string_literal(table[2])}",
            ]
        return self._sql.query(
            "SELECT catalog_name, schema_name, table_name, column_name, tag_value "
            "FROM system.information_schema.column_tags "
            f"WHERE {' AND '.join(where)} ORDER BY catalog_name, schema_name, table_name LIMIT 1000"
        )

    def _attachment_counts(self) -> dict[str, int]:
        """Count attached columns per lower-cased dimension; empty if the lookup fails."""
        counts: dict[str, int] = {}
        dims = [d.key for d in self._dimension_definitions()]
        try:
            rows = self._attachment_rows(dims)
        except Exception as exc:  # noqa: BLE001 - counts are informational
            logger.warning("Could not count row filter attachments: %s", exc)
            return counts
        for row in rows:
            key = str(row["tag_value"]).lower()
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _enrollment_tag(self) -> tuple[str, str]:
        """Return the catalog enrollment tag key/value from data-access settings."""
        from backend.app.services.catalog_enrollment import enrollment_tag_from_storage

        return enrollment_tag_from_storage(self._storage.settings.get_all())

    def _except_principals(self) -> list[str]:
        """Built-in SPs plus configured global exceptions bypass the row filter."""
        if self._policy_exceptions is not None:
            return self._policy_exceptions.except_principals()
        ids = [self._workspace.app_client_id, self._workspace.admin_client_id]
        seen: list[str] = []
        for item in ids:
            if item and item not in seen:
                seen.append(item)
        return seen

    def _to_dimension(self, definition: AttributeDefinition, attachments: int) -> RowFilterDimension:
        """Convert a definition into a dimension view, excluding ``*`` from value counts."""
        concrete = [v for v in definition.allowed_values if v != "*"]
        return RowFilterDimension(
            key=definition.key,
            description=definition.description,
            value_source=definition.value_source,
            value_sql=definition.value_sql,
            allowed_value_count=len(concrete),
            sample_values=concrete[:20],
            attachment_count=attachments,
        )

    def _audit(self, event_type: str, actor: str, payload: dict) -> None:
        """Record an audit event in storage."""
        self._storage.audit.record(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=event_type,
                actor=actor,
                payload=payload,
                created_at=datetime.now(UTC),
            )
        )


def _fqn(catalog: str, schema: str, table: str) -> str:
    """Return the backtick-quoted three-part table name."""
    return ".".join(quote_identifier(part) for part in (catalog, schema, table))


def _parse_list(raw: object) -> list[str]:
    """Parse a list or JSON value from information_schema into strings."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v) for v in raw]
    try:
        parsed = json.loads(str(raw))
    except ValueError:
        return [str(raw)]
    return [str(v) for v in parsed] if isinstance(parsed, list) else [str(parsed)]
