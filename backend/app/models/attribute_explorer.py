"""Models for Attribute Explorer — who holds a path and which resources it affects."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from backend.app.models.base import ApiModel
from backend.app.models.principal import PrincipalKind


class AttributeExplorerKind(StrEnum):
    """How the looked-up attribute maps to access.

    Attributes:
        DATA_ACCESS: Tag-driven ``dac_*`` group grants on reviewed tables.
        SENSITIVITY: Column-mask ABAC (holders + columns tagged with the path).
        ROW_FILTER: Row-filter dimension (principals + attached table columns).
        OTHER: Unknown / free-form attribute.
    """

    DATA_ACCESS = "data_access"
    SENSITIVITY = "sensitivity"
    ROW_FILTER = "row_filter"
    OTHER = "other"


class AttributeHolderMatch(StrEnum):
    """How an assignment relates to the queried path."""

    EXACT = "exact"
    DESCENDANT = "descendant"
    ANCESTOR = "ancestor"
    RELATED = "related"


class AnalyzeAttributeRequest(ApiModel):
    """Body for Attribute Explorer analyze.

    Attributes:
        attribute_key: Identity attribute key (e.g. ``data_access``).
        attribute_value: Path or value to inspect (e.g. ``finance``). Empty for
            row-filter overview (all principals + their values).
    """

    attribute_key: str = Field(min_length=1)
    attribute_value: str = ""


class AttributeHolder(ApiModel):
    """One principal that holds a related assignment.

    Attributes:
        principal_name: User, group, or SP id.
        principal_type: Principal kind.
        attribute_value: Assigned value (single-value lookup).
        match: Relation to the queried path.
        expires_at: ISO expiry when present.
        assignment_id: Underlying assignment id.
    """

    principal_name: str
    principal_type: PrincipalKind
    attribute_value: str
    match: AttributeHolderMatch
    expires_at: str | None = None
    assignment_id: str


class AttributePrincipalSummary(ApiModel):
    """One principal with every value they hold for a row-filter dimension.

    Attributes:
        principal_name: User, group, or SP id.
        principal_type: Principal kind.
        values: Distinct assigned values (sorted).
        expires_at: Soonest assignment expiry when any is set; else None.
    """

    principal_name: str
    principal_type: PrincipalKind
    values: list[str] = Field(default_factory=list)
    expires_at: str | None = None


class UnlockedTable(ApiModel):
    """A resource affected by the queried attribute path.

    For data access this is a reviewed table unlocked by ``dac_*`` grants. For
    sensitivity and row filters it is a tagged column (``column_name`` set).

    Attributes:
        full_name: ``catalog.schema.table``.
        tag_value: Access tag, sensitivity path, or row-filter dimension on the resource.
        match: Exact vs descendant relation to the queried path.
        group_name: Managed ``dac_*`` group when kind is data access.
        column_name: Column carrying the sensitivity or row-filter tag, when applicable.
    """

    full_name: str
    tag_value: str
    match: AttributeHolderMatch
    group_name: str | None = None
    column_name: str | None = None


class AttributeExplorerResult(ApiModel):
    """Attribute Explorer analyze response.

    Attributes:
        attribute_key: Queried key.
        attribute_value: Queried value, or ``*`` for row-filter overview.
        kind: Access mechanism for this attribute.
        group_name: ``dac_*`` group for data_access paths.
        parent_group: Nested parent group, if any.
        holders: Principals with related assignments (path lookup).
        principals: Row-filter overview — one row per principal with value list.
        tables: Affected resources (unlocked tables, masked columns, or filtered columns).
        note: Extra context when useful.
    """

    attribute_key: str
    attribute_value: str
    kind: AttributeExplorerKind
    group_name: str | None = None
    parent_group: str | None = None
    holders: list[AttributeHolder] = Field(default_factory=list)
    principals: list[AttributePrincipalSummary] = Field(default_factory=list)
    tables: list[UnlockedTable] = Field(default_factory=list)
    note: str | None = None
