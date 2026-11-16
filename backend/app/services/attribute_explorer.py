"""Attribute Explorer: holders for a path and resources that path affects."""

from __future__ import annotations

import logging

from backend.app.clients.sql import SqlExecutor
from backend.app.core.errors import ForbiddenError, InvalidRequestError, NotFoundError
from backend.app.models.access_request import SENSITIVITY_ATTRIBUTE_KEY
from backend.app.models.attribute_explorer import (
    AnalyzeAttributeRequest,
    AttributeExplorerKind,
    AttributeExplorerResult,
    AttributeHolder,
    AttributeHolderMatch,
    AttributePrincipalSummary,
    UnlockedTable,
)
from backend.app.models.data_access import DataAccessSettings
from backend.app.models.identity_attribute import AttributeAssignmentFilter, ValueSource
from backend.app.models.principal import PrincipalKind
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.access_requests import AccessRequestService
from backend.app.services.data_access import DataAccessService
from backend.app.services.data_access_planner import group_name, parent_value
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.app.services.row_filters import RowFilterService

logger = logging.getLogger(__name__)

_SENSITIVITY_TAG_LIMIT = 5000


class AttributeExplorerService:
    """Reverse lookup for admins and path-scoped approvers.

    Args:
        storage: Assignment storage.
        identity: Attribute definitions / data-access resolution.
        access_requests: Approver authorization (``_can_decide_for``).
        data_access: Tag inventory and settings.
        sql: Warehouse executor for sensitivity column-tag lookups.
        row_filters: Attachment inventory for row-filter dimensions.
    """

    def __init__(
        self,
        storage: StorageProvider,
        identity: IdentityAttributeService,
        access_requests: AccessRequestService,
        data_access: DataAccessService,
        sql: SqlExecutor,
        row_filters: RowFilterService,
    ) -> None:
        self._storage = storage
        self._identity = identity
        self._access = access_requests
        self._data_access = data_access
        self._sql = sql
        self._row_filters = row_filters

    def analyze(
        self, body: AnalyzeAttributeRequest, actor: CurrentUser
    ) -> AttributeExplorerResult:
        """Return holders and affected resources for one attribute path.

        Row-filter dimensions accept an empty ``attribute_value`` and return every
        principal with the values they hold (one row per principal), plus every
        table column tagged with that dimension.

        Raises:
            ForbiddenError: Caller is neither admin nor approver for this path.
            NotFoundError: Attribute definition is missing.
            InvalidRequestError: Blank key, or blank value for non–row-filter kinds.
        """
        key = body.attribute_key.strip()
        value = (body.attribute_value or "").strip()
        if not key:
            raise InvalidRequestError("attribute_key is required")

        definition = self._storage.identity_attributes.get_definition(key)
        if definition is None:
            raise NotFoundError(f"Attribute definition '{key}' was not found")

        settings = self._data_access._settings()  # noqa: SLF001
        kind = self._classify(key, settings, definition.value_source)

        # Row-filter overview: no value picker — authorize on the dimension itself.
        if kind == AttributeExplorerKind.ROW_FILTER and not value:
            if not actor.is_admin and not self._access._can_decide_for(  # noqa: SLF001
                actor, key, attribute_key=key
            ):
                raise ForbiddenError("You are not an approver for this attribute")
            principals = self._principals_overview(key)
            tables = self._row_filter_resources(key)
            return AttributeExplorerResult(
                attribute_key=key,
                attribute_value="*",
                kind=kind,
                holders=[],
                principals=principals,
                tables=tables,
                note=(
                    "Row-filter dimension: one row per principal with every value they hold. "
                    "Tables below are columns tagged row_filter="
                    f"{key}."
                ),
            )

        if not value:
            raise InvalidRequestError("attribute_value is required for this attribute kind")

        if not actor.is_admin and not self._access._can_decide_for(  # noqa: SLF001
            actor, value, attribute_key=key
        ):
            raise ForbiddenError("You are not an approver for this attribute path")

        separator = (
            settings.hierarchy_separator if kind == AttributeExplorerKind.DATA_ACCESS else "."
        )

        holders = self._holders(key, value, separator)
        tables: list[UnlockedTable] = []
        group = None
        parent = None
        note = None

        if kind == AttributeExplorerKind.DATA_ACCESS:
            group = group_name(settings.group_prefix, value)
            tables = self._tables(settings, value, separator)
            known = {t.tag_value for t in tables} | {value}
            for row in self._data_access.load_tags_public(settings):
                if row.tag_name.strip().lower() == settings.access_tag_key.lower() and row.tag_value:
                    known.add(row.tag_value.strip())
            parent_val = parent_value(value, separator, known)
            parent = group_name(settings.group_prefix, parent_val) if parent_val else None
        elif kind == AttributeExplorerKind.SENSITIVITY:
            tables = self._sensitivity_resources(value)
            note = (
                "Sensitivity unmasks columns whose sensitivity_category.sensitivity path "
                "matches this value (or a descendant). It does not create dac_* table grants."
            )
        elif kind == AttributeExplorerKind.ROW_FILTER:
            tables = self._row_filter_resources(key)
            note = (
                "Row-filter dimensions scope rows via ABAC. Tables below are columns tagged "
                f"row_filter={key}."
            )
        else:
            note = "Holders only for this attribute kind."

        return AttributeExplorerResult(
            attribute_key=key,
            attribute_value=value,
            kind=kind,
            group_name=group,
            parent_group=parent,
            holders=holders,
            principals=[],
            tables=tables,
            note=note,
        )

    def classify(
        self, key: str, value_source: ValueSource, settings: DataAccessSettings | None = None
    ) -> AttributeExplorerKind:
        """Map an attribute key to explorer kind."""
        resolved = settings if settings is not None else self._data_access._settings()  # noqa: SLF001
        return self._classify(key, resolved, value_source)

    def resources_for(
        self, kind: AttributeExplorerKind, key: str, value: str
    ) -> list[UnlockedTable]:
        """Return affected tables/columns for one attribute path.

        Args:
            kind: Classified attribute kind.
            key: Attribute key (row-filter dimension for ROW_FILTER).
            value: Path or value (ignored for row-filter dimension attachment lookup).
        """
        if kind == AttributeExplorerKind.DATA_ACCESS:
            settings = self._data_access._settings()  # noqa: SLF001
            return self._tables(settings, value, settings.hierarchy_separator)
        if kind == AttributeExplorerKind.SENSITIVITY:
            return self._sensitivity_resources(value)
        if kind == AttributeExplorerKind.ROW_FILTER:
            return self._row_filter_resources(key)
        return []

    def _classify(
        self, key: str, settings: DataAccessSettings, value_source: ValueSource
    ) -> AttributeExplorerKind:
        """Map an attribute key to explorer kind."""
        if key.lower() == SENSITIVITY_ATTRIBUTE_KEY:
            return AttributeExplorerKind.SENSITIVITY
        data_access = self._identity.find_data_access_definition(settings.access_tag_key)
        if data_access is not None and key.lower() == data_access.key.lower():
            return AttributeExplorerKind.DATA_ACCESS
        if value_source != ValueSource.GOVERNED_TAGS:
            return AttributeExplorerKind.ROW_FILTER
        return AttributeExplorerKind.OTHER

    def _principals_overview(self, key: str) -> list[AttributePrincipalSummary]:
        """Aggregate active assignments for ``key`` into one row per principal."""
        items = self._storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(attribute_key=key, include_inactive=False)
        )
        by_principal: dict[tuple[str, PrincipalKind], dict[str, object]] = {}
        for item in items:
            assigned = item.attribute_value.strip()
            if not assigned:
                continue
            bucket_key = (item.principal_name, item.principal_type)
            bucket = by_principal.get(bucket_key)
            if bucket is None:
                bucket = {
                    "values": set(),
                    "expires": [],
                }
                by_principal[bucket_key] = bucket
            values_set = bucket["values"]
            assert isinstance(values_set, set)
            values_set.add(assigned)
            if item.expires_at is not None:
                expires_list = bucket["expires"]
                assert isinstance(expires_list, list)
                expires_list.append(item.expires_at)

        out: list[AttributePrincipalSummary] = []
        for (name, ptype), bucket in by_principal.items():
            values_set = bucket["values"]
            assert isinstance(values_set, set)
            expires_list = bucket["expires"]
            assert isinstance(expires_list, list)
            soonest = min(expires_list) if expires_list else None
            out.append(
                AttributePrincipalSummary(
                    principal_name=name,
                    principal_type=ptype,
                    values=sorted(values_set, key=str.lower),
                    expires_at=soonest.isoformat() if soonest is not None else None,
                )
            )
        out.sort(key=lambda row: (row.principal_type.value, row.principal_name.lower()))
        return out

    def _holders(self, key: str, value: str, separator: str) -> list[AttributeHolder]:
        """List active assignments related to ``value`` under ``key``."""
        items = self._storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(attribute_key=key, include_inactive=False)
        )
        value_l = value.lower()
        out: list[AttributeHolder] = []
        for item in items:
            assigned = item.attribute_value.strip()
            assigned_l = assigned.lower()
            match: AttributeHolderMatch | None = None
            if assigned_l == value_l or assigned == "*":
                match = AttributeHolderMatch.EXACT
            elif assigned_l.startswith(value_l + separator):
                match = AttributeHolderMatch.DESCENDANT
            elif value_l.startswith(assigned_l + separator):
                match = AttributeHolderMatch.ANCESTOR
            if match is None:
                continue
            out.append(
                AttributeHolder(
                    principal_name=item.principal_name,
                    principal_type=item.principal_type,
                    attribute_value=item.attribute_value,
                    match=match,
                    expires_at=item.expires_at.isoformat() if item.expires_at else None,
                    assignment_id=item.assignment_id,
                )
            )
        out.sort(
            key=lambda h: (h.match.value, h.principal_name.lower(), h.attribute_value.lower())
        )
        return out

    def _tables(
        self, settings: DataAccessSettings, value: str, separator: str
    ) -> list[UnlockedTable]:
        """Reviewed tables tagged with ``value`` or a descendant path."""
        access_key = settings.access_tag_key.lower()
        review_key = settings.review_tag_key.lower()
        review_value = settings.review_tag_value.strip().lower()
        value_l = value.lower()

        by_object: dict[str, dict[str, str]] = {}
        for row in self._data_access.load_tags_public(settings):
            if row.level != "table":
                continue
            key = row.full_name.lower()
            by_object.setdefault(key, {"_full": row.full_name})
            by_object[key][row.tag_name.strip().lower()] = row.tag_value.strip()

        tables: list[UnlockedTable] = []
        for tags in by_object.values():
            access = tags.get(access_key)
            if not access:
                continue
            if (tags.get(review_key) or "").lower() != review_value:
                continue
            access_l = access.lower()
            if access_l == value_l:
                match = AttributeHolderMatch.EXACT
            elif access_l.startswith(value_l + separator):
                match = AttributeHolderMatch.DESCENDANT
            else:
                continue
            tables.append(
                UnlockedTable(
                    full_name=tags["_full"],
                    tag_value=access,
                    match=match,
                    group_name=group_name(settings.group_prefix, access),
                )
            )
        tables.sort(key=lambda t: (t.match.value, t.full_name.lower()))
        return tables

    def _sensitivity_resources(self, value: str) -> list[UnlockedTable]:
        """Columns whose sensitivity path equals ``value`` or is a descendant of it."""
        if not self._sql.configured:
            return []
        value_l = value.lower()
        try:
            rows = self._sql.query(
                "SELECT catalog_name, schema_name, table_name, column_name, tag_name, tag_value "
                "FROM system.information_schema.column_tags "
                "WHERE lower(tag_name) IN ('sensitivity_category', 'sensitivity') "
                f"LIMIT {_SENSITIVITY_TAG_LIMIT}"
            )
        except Exception as exc:  # noqa: BLE001 — inventory is best-effort for analyze
            logger.warning("Could not load sensitivity column tags: %s", exc)
            return []

        by_column: dict[tuple[str, str, str, str], dict[str, str]] = {}
        for row in rows:
            catalog = str(row.get("catalog_name") or "").strip()
            schema = str(row.get("schema_name") or "").strip()
            table = str(row.get("table_name") or "").strip()
            column = str(row.get("column_name") or "").strip()
            tag_name = str(row.get("tag_name") or "").strip().lower()
            tag_value = str(row.get("tag_value") or "").strip()
            if not (catalog and schema and table and column and tag_name and tag_value):
                continue
            bucket = by_column.setdefault((catalog, schema, table, column), {})
            bucket[tag_name] = tag_value

        out: list[UnlockedTable] = []
        for (catalog, schema, table, column), tags in by_column.items():
            category = tags.get("sensitivity_category", "").strip()
            sensitivity = tags.get("sensitivity", "").strip()
            if not category or not sensitivity:
                continue
            path = f"{category}.{sensitivity}"
            path_l = path.lower()
            if path_l == value_l:
                match = AttributeHolderMatch.EXACT
            elif path_l.startswith(value_l + "."):
                match = AttributeHolderMatch.DESCENDANT
            else:
                continue
            out.append(
                UnlockedTable(
                    full_name=f"{catalog}.{schema}.{table}",
                    column_name=column,
                    tag_value=path,
                    match=match,
                )
            )
        out.sort(
            key=lambda t: (
                t.match.value,
                t.full_name.lower(),
                (t.column_name or "").lower(),
            )
        )
        return out

    def _row_filter_resources(self, dimension: str) -> list[UnlockedTable]:
        """Table columns tagged ``row_filter=<dimension>``."""
        wanted = dimension.strip().lower()
        try:
            attachments = self._row_filters.list_attachments().attachments
        except Exception as exc:  # noqa: BLE001 — inventory is best-effort for analyze
            logger.warning("Could not load row-filter attachments: %s", exc)
            return []
        out = [
            UnlockedTable(
                full_name=item.table_full_name,
                column_name=item.column_name,
                tag_value=item.dimension,
                match=AttributeHolderMatch.EXACT,
            )
            for item in attachments
            if item.dimension.strip().lower() == wanted
        ]
        out.sort(key=lambda t: (t.full_name.lower(), (t.column_name or "").lower()))
        return out
