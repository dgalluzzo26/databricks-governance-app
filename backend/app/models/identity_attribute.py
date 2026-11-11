"""App-managed identity attributes.

Unity Catalog cannot attach attributes to identities yet, so the app stores them. Each
assignment says principal X holds value V of attribute K. Attribute definitions are tied to
one or more governed tags; allowed values come from those tag policies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import Field, computed_field, field_validator, model_validator

from backend.app.models.base import ApiModel
from backend.app.models.principal import PrincipalKind


class AttributeSource(StrEnum):
    """How an attribute assignment came to exist.

    Attributes:
        MANUAL: Assigned directly by an admin.
        ACCESS_REQUEST: Created when an access request was approved.
        IDENTITY_POLICY: Granted automatically by an identity policy; reserved, not currently
            produced by the backend.
    """

    MANUAL = "MANUAL"
    ACCESS_REQUEST = "ACCESS_REQUEST"
    IDENTITY_POLICY = "IDENTITY_POLICY"


class ValueSource(StrEnum):
    """Where an attribute definition's allowed values come from.

    Attributes:
        GOVERNED_TAGS: Resolved live from the allowed values of linked governed tags.
        FREE_FORM: Admin-entered list stored by the app.
        SQL: Seeded from a SQL query and stored; refreshed on demand.
    """

    GOVERNED_TAGS = "governed_tags"
    FREE_FORM = "free_form"
    SQL = "sql"


ALL_VALUES = "*"


class GovernedTagSummary(ApiModel):
    """Unity Catalog governed tag and its allowed values.

    Attributes:
        tag_key: Governed tag key.
        description: Tag policy description, or None.
        values: Allowed values defined by the tag policy.
        value_count: Number of allowed values.
    """

    tag_key: str
    description: str | None = None
    values: list[str] = []
    value_count: int = 0


class GovernedTagList(ApiModel):
    """Page of governed tags.

    Attributes:
        tags: Returned tags, capped at the requested limit.
        total: Total number of matching tags before the limit.
        truncated: True when ``tags`` holds fewer entries than ``total``.
    """

    tags: list[GovernedTagSummary]
    total: int
    truncated: bool = False


class AttributeDefinition(ApiModel):
    """Definition of an identity attribute key and its vocabulary.

    Attributes:
        key: Attribute key that assignments refer to (e.g. ``sensitivity``, ``region``).
        description: Optional human-readable description.
        governed_tag_keys: Governed tags supplying allowed values; empty unless
            ``value_source`` is ``governed_tags``.
        value_source: Where allowed values come from.
        value_sql: Seed query when ``value_source`` is ``sql``; otherwise None.
        allowed_values: Values that may be assigned. Resolved live for governed tags; stored
            for free-form / SQL definitions, which always include the ``*`` wildcard.
        created_by: User who created the definition.
        created_at: Creation time.
        updated_by: User who last changed the definition.
        updated_at: Last change time.
    """

    key: str
    description: str | None = None
    governed_tag_keys: list[str] = []
    value_source: ValueSource = ValueSource.GOVERNED_TAGS
    # Seed query for value_source=sql; refreshed into allowed_values.
    value_sql: str | None = None
    # Governed tags: resolved live. Free-form / SQL: stored, and always include "*".
    allowed_values: list[str] = []
    created_by: str | None = None
    created_at: datetime | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None


def _clean_list(value: list[str]) -> list[str]:
    """Strip items and drop blanks and duplicates, preserving first-seen order.

    Args:
        value: Raw list of strings.

    Returns:
        The cleaned list.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = item.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        cleaned.append(text)
    return cleaned


class UpsertAttributeDefinitionRequest(ApiModel):
    """Request body to create or update an attribute definition.

    Validation normalizes the body per ``value_source``: governed-tag definitions require at
    least one tag key (and default ``key`` to the first one); free-form and SQL definitions
    require ``key`` and have ``governed_tag_keys`` cleared; ``value_sql`` is required and
    stripped for SQL definitions and cleared otherwise.

    Attributes:
        key: Attribute key (max 256 chars); optional only for governed-tag definitions.
        description: Optional human-readable description.
        value_source: Where allowed values come from.
        governed_tag_keys: Linked governed tags; stripped and de-duplicated.
        allowed_values: Values for free-form definitions; stripped and de-duplicated.
        value_sql: Seed query for SQL definitions.
    """

    key: str | None = Field(default=None, max_length=256)
    description: str | None = None
    value_source: ValueSource = ValueSource.GOVERNED_TAGS
    governed_tag_keys: list[str] = []
    allowed_values: list[str] = []
    value_sql: str | None = None

    @field_validator("governed_tag_keys", "allowed_values")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        """Strip, drop blank and de-duplicate list entries.

        Args:
            value: Raw list.

        Returns:
            The cleaned list.
        """
        return _clean_list(value)

    @model_validator(mode="after")
    def _validate_source(self) -> UpsertAttributeDefinitionRequest:
        """Enforce and normalize the fields required by ``value_source``.

        Returns:
            This request with ``key``, ``governed_tag_keys`` and ``value_sql`` normalized.

        Raises:
            ValueError: If governed-tag definitions have no tag keys, free-form / SQL
                definitions have no key, or SQL definitions have no ``value_sql``.
        """
        key = (self.key or "").strip()
        if self.value_source == ValueSource.GOVERNED_TAGS:
            if not self.governed_tag_keys:
                raise ValueError("At least one governed tag key is required")
            key = key or self.governed_tag_keys[0]
        else:
            self.governed_tag_keys = []
            if not key:
                raise ValueError("key is required for free-form and SQL definitions")
        if self.value_source == ValueSource.SQL:
            if not (self.value_sql or "").strip():
                raise ValueError("value_sql is required when value_source is sql")
            self.value_sql = self.value_sql.strip()
        else:
            self.value_sql = None
        self.key = key
        return self


class IdentityAttributeAssignment(ApiModel):
    """Statement that a principal holds one value of an attribute.

    Attributes:
        assignment_id: Unique identifier of the assignment.
        principal_name: User name, group name or service principal application ID.
        principal_type: Kind of principal.
        attribute_key: Attribute key the value belongs to.
        attribute_value: Assigned value; ``*`` grants every value of the attribute.
        source: How the assignment was created.
        granted_by: User who granted it, or None when unknown.
        justification: Optional business justification.
        expires_at: Expiry time; None means it never expires. Naive values are read as UTC.
        created_at: Creation time.
        revoked_at: Revocation time; None while not revoked.
        revoked_by: User who revoked it.
    """

    assignment_id: str
    principal_name: str
    principal_type: PrincipalKind
    attribute_key: str
    attribute_value: str
    source: AttributeSource = AttributeSource.MANUAL
    granted_by: str | None = None
    justification: str | None = None
    expires_at: datetime | None = None
    created_at: datetime
    revoked_at: datetime | None = None
    revoked_by: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def active(self) -> bool:
        """Whether the assignment is in effect: not revoked and not yet expired."""
        if self.revoked_at is not None:
            return False
        if self.expires_at is None:
            return True
        expires = self.expires_at
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=UTC)
        return expires > datetime.now(UTC)


class AssignAttributeRequest(ApiModel):
    """Request body for an admin to assign an attribute value to a principal.

    Attributes:
        principal_name: Principal receiving the value.
        principal_type: Kind of principal.
        attribute_key: Attribute key; must have a definition.
        attribute_value: Value to assign; must be allowed by the definition.
        justification: Optional business justification.
        expires_at: Optional expiry; None means the assignment never expires.
    """

    principal_name: str = Field(min_length=1)
    principal_type: PrincipalKind
    attribute_key: str = Field(min_length=1)
    attribute_value: str = Field(min_length=1)
    justification: str | None = None
    expires_at: datetime | None = None


class AttributeAssignmentFilter(ApiModel):
    """Filter for listing attribute assignments; None fields do not filter.

    Attributes:
        principal_name: Only assignments for this principal.
        principal_type: Only assignments for this kind of principal.
        attribute_key: Only assignments of this attribute key.
        include_inactive: Include revoked and expired assignments when True.
    """

    principal_name: str | None = None
    principal_type: PrincipalKind | None = None
    attribute_key: str | None = None
    include_inactive: bool = False


class AttributeDefinitionList(ApiModel):
    """All attribute definitions.

    Attributes:
        definitions: Attribute definitions.
        total: Number of definitions.
    """

    definitions: list[AttributeDefinition]
    total: int


class AttributeAssignmentList(ApiModel):
    """Attribute assignments matching a filter.

    Attributes:
        assignments: Matching assignments.
        total: Number of assignments returned.
    """

    assignments: list[IdentityAttributeAssignment]
    total: int
