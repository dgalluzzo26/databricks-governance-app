from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import UTC, datetime

from backend.app.clients.cache import TtlCache
from backend.app.clients.sql import SqlExecutor, quote_identifier, string_literal
from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.config import AppConfig
from backend.app.core.errors import InvalidRequestError, NotFoundError
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


def build_function_sql(function_name: str, mirror_table: str) -> str:
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
) -> str:
    match = "\n  OR ".join(
        f"has_tag_value({string_literal(tag_key)}, {string_literal(dim)})" for dim in dimensions
    )
    except_clause = (
        "\nEXCEPT " + ", ".join(f"`{p.replace('`', '')}`" for p in except_principals)
        if except_principals
        else ""
    )
    return f"""CREATE OR REPLACE POLICY {policy_name} ON METASTORE
COMMENT 'App-managed row filter over {tag_key} dimensions: {", ".join(dimensions)}'
ROW FILTER {function_name}
TO `account users`{except_clause}
FOR TABLES
MATCH COLUMNS (
  {match}
) AS c
USING COLUMNS (c, get_column_tag_value(c, {string_literal(tag_key)}))"""


def split_table_name(full_name: str) -> tuple[str, str, str]:
    parts = [part.strip().strip("`") for part in full_name.strip().split(".")]
    if len(parts) != 3 or not all(parts):
        raise InvalidRequestError("Table must be a three-part name: catalog.schema.table")
    for part in parts:
        quote_identifier(part)
    return parts[0], parts[1], parts[2]


class RowFilterService:
    """Row-filter dimensions: definitions + governed tag values + one metastore policy."""

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
        return self._config.row_filter_tag_key

    @property
    def function_name(self) -> str:
        return ".".join(
            quote_identifier(part)
            for part in (self._config.app_catalog, self._config.abac_function_schema, "has_row_access")
        )

    @property
    def mirror_table(self) -> str:
        return ".".join(
            quote_identifier(part)
            for part in (self._config.app_catalog, self._config.app_schema, "identity_attributes")
        )

    # -- dimensions ---------------------------------------------------------------------

    def list_dimensions(self) -> RowFilterDimensionList:
        definitions = self._dimension_definitions()
        counts = self._attachment_counts()
        dims = [self._to_dimension(d, counts.get(d.key.lower(), 0)) for d in definitions]
        return RowFilterDimensionList(dimensions=dims, total=len(dims))

    def upsert_dimension(
        self, request: UpsertRowFilterDimensionRequest, actor: CurrentUser
    ) -> RowFilterDimension:
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
        dim = self._require_dimension(key)
        attached = self._attachment_counts().get(dim.key, 0)
        if attached:
            raise InvalidRequestError(
                f"Detach '{dim.key}' from {attached} column(s) before deleting the dimension"
            )
        self._identity.delete_definition(dim.key, actor)
        self.ensure_pack(actor)

    def preview_seed(self, value_sql: str) -> SeedPreview:
        values = self._identity.preview_seed(value_sql)
        return SeedPreview(values=values[:100], total=len(values))

    def refresh_dimension(self, key: str, actor: CurrentUser) -> RowFilterDimension:
        dim = self._require_dimension(key)
        refreshed = self._identity.refresh_definition(dim.key, actor)
        return self._to_dimension(refreshed, self._attachment_counts().get(dim.key, 0))

    # -- policy pack --------------------------------------------------------------------

    def pack_status(self) -> RowFilterPackStatus:
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
        """Make the tag values, UDF and metastore policy match the current dimensions."""
        dims = [d.key for d in self._dimension_definitions()]
        policy_name = self._config.row_filter_policy_name
        if dims:
            self._governed_tags.ensure_values(self.tag_key, dims)
            self._sql.execute(build_function_sql(self.function_name, self.mirror_table))
            self._sql.execute(
                build_policy_sql(
                    policy_name,
                    self.function_name,
                    self.tag_key,
                    dims,
                    self._except_principals(),
                )
            )
        else:
            self._sql.execute(f"DROP POLICY IF EXISTS {policy_name} ON METASTORE")
        if self._metadata_cache is not None:
            self._metadata_cache.invalidate(("abac-policy-definitions",))
        self._audit("row_filter.pack_ensured", actor.user_name, {"dimensions": dims})
        return self.pack_status()

    # -- attachments --------------------------------------------------------------------

    def list_attachments(self) -> RowFilterAttachmentList:
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
        tag_values = {value.lower() for value in self._governed_tags.get_tag(self.tag_key).values}
        return [
            d
            for d in self._identity.list_definitions().definitions
            if d.value_source != ValueSource.GOVERNED_TAGS and d.key.lower() in tag_values
        ]

    def _require_dimension(self, key: str) -> AttributeDefinition:
        wanted = key.strip().lower()
        for definition in self._dimension_definitions():
            if definition.key.lower() == wanted:
                return definition
        raise NotFoundError(f"Row filter dimension '{key}' was not found")

    def _attachment_rows(
        self, dims: list[str], *, table: tuple[str, str, str] | None = None
    ) -> list[dict]:
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
    return ".".join(quote_identifier(part) for part in (catalog, schema, table))


def _parse_list(raw: object) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(v) for v in raw]
    try:
        parsed = json.loads(str(raw))
    except ValueError:
        return [str(raw)]
    return [str(v) for v in parsed] if isinstance(parsed, list) else [str(parsed)]
