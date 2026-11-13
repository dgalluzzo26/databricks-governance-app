"""Identity attribute definitions and principal assignments.

Identity attributes (e.g. hierarchical ``sensitivity`` paths or row-filter dimension values)
are what the ABAC column-mask and row-filter UDFs evaluate. Definitions draw their vocabulary
from governed tags, a fixed list, or a SQL seed query; assignments grant a value to a user or
group, optionally with an expiry. Primary storage (Lakebase) is the source of truth and every
write is mirrored to Unity Catalog Delta tables and audited.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from backend.app.clients.sql import SqlExecutor
from backend.app.core.errors import InvalidRequestError, NotFoundError
from backend.app.models.access_request import SENSITIVITY_ATTRIBUTE_KEY
from backend.app.models.audit import AuditEvent
from backend.app.models.data_access import DataAccessSettings
from backend.app.models.identity_attribute import (
    ALL_VALUES,
    AssignAttributeRequest,
    AttributeAssignmentFilter,
    AttributeAssignmentList,
    AttributeDefinition,
    AttributeDefinitionList,
    AttributeSource,
    GovernedTagList,
    IdentityAttributeAssignment,
    UpsertAttributeDefinitionRequest,
    ValueSource,
)
from backend.app.models.principal import PrincipalKind
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.governed_tags import GovernedTagService
from backend.app.services.identity_uc_sync import IdentityUcSync
from backend.app.services.value_seed import run_seed_sql

logger = logging.getLogger(__name__)

_DATA_ACCESS_SETTINGS_KEY = "data_access"
_DEFAULT_ACCESS_TAG_KEY = "data_access_control"

ASSIGNMENT_ASSIGNED = "assigned"
ASSIGNMENT_REVOKED = "revoked"
AssignmentListener = Callable[[str, IdentityAttributeAssignment, CurrentUser], None]


class IdentityAttributeService:
    """App-managed identity attribute definitions and assignments.

    Definitions are bound to governed tags; assignment values must come from those tags.
    When Lakebase is primary, writes are mirrored to Unity Catalog Delta so column-mask
    UDFs can look up attributes (UC SQL cannot query Lakebase).

    Args:
        storage: Primary storage provider for definitions, assignments, and audit events.
        governed_tags: Governed tag reader used to resolve tag-bound vocabularies.
        uc_sync: Unity Catalog mirror; when None, writes are not mirrored.
        sql: SQL executor used to run SQL seed queries; required for SQL-seeded definitions.
    """

    def __init__(
        self,
        storage: StorageProvider,
        governed_tags: GovernedTagService,
        uc_sync: IdentityUcSync | None = None,
        sql: SqlExecutor | None = None,
    ) -> None:
        self._storage = storage
        self._governed_tags = governed_tags
        self._uc_sync = uc_sync
        self._sql = sql
        self._listeners: list[AssignmentListener] = []

    def add_listener(self, listener: AssignmentListener) -> None:
        """Call ``listener(event, assignment, actor)`` after each new assignment or revoke.

        Listener failures are logged and never fail the assignment.

        Args:
            listener: Callback receiving ``assigned`` or ``revoked``, the saved assignment, and the actor.
        """
        self._listeners.append(listener)

    def _notify(self, event: str, assignment: IdentityAttributeAssignment, actor: CurrentUser) -> None:
        """Run listeners, isolating their failures from the caller."""
        for listener in self._listeners:
            try:
                listener(event, assignment, actor)
            except Exception:  # noqa: BLE001 - side effects must not undo the assignment
                logger.exception("Assignment listener failed for %s %s", event, assignment.assignment_id)

    def list_governed_tags(self, *, q: str | None = None, limit: int = 50) -> GovernedTagList:
        """List governed tags that definitions can bind to.

        Args:
            q: Optional case-insensitive search over key, description, and values.
            limit: Maximum tags to return.

        Returns:
            Matching governed tags.

        Raises:
            UpstreamError: If tag policies cannot be listed.
        """
        return self._governed_tags.list_tags(q=q, limit=limit)

    def list_definitions(self) -> AttributeDefinitionList:
        """List all attribute definitions with their current allowed values resolved.

        Returns:
            Definitions whose governed-tag vocabularies are resolved live.
        """
        definitions = [self._enrich(item) for item in self._storage.identity_attributes.list_definitions()]
        return AttributeDefinitionList(definitions=definitions, total=len(definitions))

    def get_definition(self, key: str) -> AttributeDefinition:
        """Return one attribute definition with its allowed values resolved.

        Args:
            key: Attribute key.

        Returns:
            The enriched definition.

        Raises:
            NotFoundError: If no definition exists for the key.
        """
        definition = self._storage.identity_attributes.get_definition(key)
        if definition is None:
            raise NotFoundError(f"Attribute definition '{key}' was not found")
        return self._enrich(definition)

    def access_tag_key(self) -> str:
        """Return the configured data-access UC tag key (default ``data_access_control``)."""
        raw = self._storage.settings.get_all().get(_DATA_ACCESS_SETTINGS_KEY)
        if not raw:
            return _DEFAULT_ACCESS_TAG_KEY
        try:
            return DataAccessSettings.model_validate(raw).access_tag_key.lower()
        except Exception:  # noqa: BLE001
            return _DEFAULT_ACCESS_TAG_KEY

    def find_sensitivity_definition(self) -> AttributeDefinition | None:
        """Return the sensitivity definition when configured, else None."""
        definition = self._storage.identity_attributes.get_definition(SENSITIVITY_ATTRIBUTE_KEY)
        return self._enrich(definition) if definition is not None else None

    def find_data_access_definition(
        self, access_tag_key: str | None = None
    ) -> AttributeDefinition | None:
        """Return the definition linked to the data-access UC tag, if any.

        Matches by attribute key or ``governed_tag_keys`` equal to the access tag.

        Args:
            access_tag_key: Tag to match; defaults to Data Access settings.

        Returns:
            The enriched definition, or None when unset.
        """
        needle = (access_tag_key or self.access_tag_key()).strip().lower()
        if not needle:
            return None
        for item in self._storage.identity_attributes.list_definitions():
            if item.key.lower() == needle:
                return self._enrich(item)
            if any(tag.lower() == needle for tag in item.governed_tag_keys):
                return self._enrich(item)
        return None

    def preview_seed(self, value_sql: str) -> list[str]:
        """Run a seed query without saving anything, for previewing a SQL vocabulary.

        Args:
            value_sql: Candidate SELECT/WITH seed query.

        Returns:
            Distinct values the query would seed.

        Raises:
            NotConfiguredError: If no SQL warehouse is configured.
            InvalidRequestError: If the query is invalid, too large, or returns nothing.
        """
        return run_seed_sql(self._sql, value_sql)

    def refresh_definition(self, key: str, actor: CurrentUser) -> AttributeDefinition:
        """Re-run a SQL definition's seed query and store the result.

        Delegates to ``upsert_definition``, so the definition is mirrored to Unity Catalog
        and audited.

        Args:
            key: Attribute key of a SQL-seeded definition.
            actor: The admin triggering the refresh.

        Returns:
            The definition with its refreshed vocabulary.

        Raises:
            NotFoundError: If the definition does not exist.
            InvalidRequestError: If it is not SQL-seeded or the seed query fails validation.
            NotConfiguredError: If no SQL warehouse is configured.
        """
        current = self.get_definition(key)
        if current.value_source != ValueSource.SQL:
            raise InvalidRequestError(f"Attribute '{key}' is not SQL-seeded")
        return self.upsert_definition(
            UpsertAttributeDefinitionRequest(
                key=current.key,
                description=current.description,
                value_source=ValueSource.SQL,
                value_sql=current.value_sql,
            ),
            actor,
        )

    def upsert_definition(
        self, request: UpsertAttributeDefinitionRequest, actor: CurrentUser
    ) -> AttributeDefinition:
        """Create or replace an attribute definition.

        Governed-tag definitions store only the tag keys (values are resolved live and the
        description defaults to the first tag description); SQL definitions store the seed
        query's result; manual definitions store the supplied list. SQL and manual
        vocabularies always include ``*``. Mirrors the definition to Unity Catalog (without values) and
        records an ``identity_attribute_definition.upserted`` audit event.

        Args:
            request: Key, description, value source, and source-specific inputs.
            actor: The admin making the change.

        Returns:
            The saved, enriched definition.

        Raises:
            InvalidRequestError: If a referenced governed tag does not exist, the seed
                query is invalid, or a second sensitivity / data-access definition is created.
            NotConfiguredError: If a SQL seed is requested without a SQL warehouse.
            UpstreamError: If governed tags cannot be read.
        """
        repo = self._storage.identity_attributes
        existing = repo.get_definition(request.key)
        now = datetime.now(UTC)
        description = request.description
        tag_keys: list[str] = []
        stored_values: list[str] = []
        if request.value_source == ValueSource.GOVERNED_TAGS:
            tags = self._governed_tags.require_tags(request.governed_tag_keys)
            tag_keys = [tag.tag_key for tag in tags]
            if not description:
                description = next((tag.description for tag in tags if tag.description), None)
        elif request.value_source == ValueSource.SQL:
            stored_values = run_seed_sql(self._sql, request.value_sql or "")
        else:
            stored_values = request.allowed_values
        self._enforce_purpose_uniqueness(request.key, tag_keys, existing=existing)
        definition = AttributeDefinition(
            key=request.key,
            description=description,
            governed_tag_keys=tag_keys,
            value_source=request.value_source,
            value_sql=request.value_sql,
            # Governed-tag values are resolved live by _enrich, so only other sources store them.
            allowed_values=(
                [] if request.value_source == ValueSource.GOVERNED_TAGS else _with_all_values(stored_values)
            ),
            created_by=existing.created_by if existing else actor.user_name,
            created_at=existing.created_at if existing else now,
            updated_by=actor.user_name,
            updated_at=now,
        )
        saved = self._enrich(repo.upsert_definition(definition))
        self._mirror_definition(saved)
        self._audit(
            "identity_attribute_definition.upserted",
            actor.user_name,
            {
                "key": saved.key,
                "governed_tag_keys": saved.governed_tag_keys,
                "value_source": saved.value_source.value,
                "value_sql": saved.value_sql,
                "allowed_value_count": len(saved.allowed_values),
            },
        )
        return saved

    def delete_definition(self, key: str, actor: CurrentUser) -> None:
        """Delete an attribute definition that has no active assignments.

        Also removes it from the Unity Catalog mirror and records an
        ``identity_attribute_definition.deleted`` audit event.

        Args:
            key: Attribute key.
            actor: The admin making the change.

        Raises:
            InvalidRequestError: If active assignments still reference the attribute.
            NotFoundError: If no definition exists for the key.
        """
        active = self._storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(attribute_key=key, include_inactive=False)
        )
        if active:
            raise InvalidRequestError(
                f"Cannot delete attribute '{key}' while {len(active)} active assignment(s) exist"
            )
        if not self._storage.identity_attributes.delete_definition(key):
            raise NotFoundError(f"Attribute definition '{key}' was not found")
        if self._uc_sync is not None:
            self._uc_sync.repo.delete_definition(key)
        self._audit("identity_attribute_definition.deleted", actor.user_name, {"key": key})

    def get_assignment(self, assignment_id: str) -> IdentityAttributeAssignment:
        """Return one assignment by id.

        Args:
            assignment_id: Assignment identifier.

        Returns:
            The assignment.

        Raises:
            NotFoundError: If the assignment does not exist.
        """
        current = self._storage.identity_attributes.get_assignment(assignment_id)
        if current is None:
            raise NotFoundError(f"Assignment {assignment_id} was not found")
        return current

    def list_assignments(
        self,
        *,
        principal_name: str | None = None,
        principal_type: PrincipalKind | None = None,
        attribute_key: str | None = None,
        include_inactive: bool = False,
    ) -> AttributeAssignmentList:
        """List assignments matching optional filters.

        Args:
            principal_name: Restrict to one principal.
            principal_type: Restrict to USER or GROUP principals.
            attribute_key: Restrict to one attribute.
            include_inactive: Include revoked and expired assignments.

        Returns:
            Matching assignments.
        """
        items = self._storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(
                principal_name=principal_name,
                principal_type=principal_type,
                attribute_key=attribute_key,
                include_inactive=include_inactive,
            )
        )
        return AttributeAssignmentList(assignments=items, total=len(items))

    def list_for_user(self, user: CurrentUser) -> AttributeAssignmentList:
        """Active attributes for the signed-in user and their groups.

        Mirrors what the UDFs see: direct USER assignments plus assignments to any of the
        user's groups, de-duplicated by (key, value) with direct grants taking precedence.

        Args:
            user: The signed-in user, including resolved group memberships.

        Returns:
            The user's effective active assignments.
        """
        repo = self._storage.identity_attributes
        direct = repo.list_assignments(
            AttributeAssignmentFilter(
                principal_name=user.user_name,
                principal_type=PrincipalKind.USER,
                include_inactive=False,
            )
        )
        via_groups: list[IdentityAttributeAssignment] = []
        for group in user.groups:
            via_groups.extend(
                repo.list_assignments(
                    AttributeAssignmentFilter(
                        principal_name=group,
                        principal_type=PrincipalKind.GROUP,
                        include_inactive=False,
                    )
                )
            )
        seen: set[tuple[str, str]] = set()
        merged: list[IdentityAttributeAssignment] = []
        for item in [*direct, *via_groups]:
            key = (item.attribute_key, item.attribute_value)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
        return AttributeAssignmentList(assignments=merged, total=len(merged))

    def assign(
        self,
        request: AssignAttributeRequest,
        actor: CurrentUser,
        *,
        source: AttributeSource = AttributeSource.MANUAL,
    ) -> IdentityAttributeAssignment:
        """Grant an attribute value to a user or group.

        The value is validated case-insensitively against the definition's vocabulary and
        stored in canonical casing. Idempotent: if the principal already holds the value,
        the existing assignment is returned, or its expiry is updated when a different one
        is requested. New assignments are persisted, mirrored to Unity Catalog, and recorded
        as an ``identity_attribute.assigned`` audit event.

        Args:
            request: Principal, attribute key/value, justification, and optional expiry.
            actor: The admin or approver granting the value.
            source: How the grant originated (manual or via an approved access request).

        Returns:
            The new, existing, or renewed assignment.

        Raises:
            InvalidRequestError: If the definition is unknown, the value is not allowed, or a
                governed-tag definition is not linked to any tag.
            NotFoundError: If an existing assignment disappears while updating its expiry.
        """
        definition = self._storage.identity_attributes.get_definition(request.attribute_key)
        if definition is None:
            raise InvalidRequestError(
                f"Unknown attribute key '{request.attribute_key}'. Create the definition first."
            )
        enriched = self._enrich(definition)
        allowed = {value.lower(): value for value in enriched.allowed_values}
        requested_value = request.attribute_value.strip()
        if allowed and requested_value.lower() not in allowed:
            if enriched.value_source == ValueSource.GOVERNED_TAGS:
                origin = f"governed tag(s) {', '.join(enriched.governed_tag_keys)}"
            else:
                origin = "the definition's value list"
            raise InvalidRequestError(
                f"Value '{request.attribute_value}' is not allowed for '{request.attribute_key}'. "
                f"Allowed values come from {origin}: {', '.join(enriched.allowed_values[:50])}"
            )
        if (
            enriched.value_source == ValueSource.GOVERNED_TAGS
            and not enriched.governed_tag_keys
            and not enriched.allowed_values
        ):
            raise InvalidRequestError(
                f"Attribute '{request.attribute_key}' is not linked to any governed tags"
            )
        canonical_value = allowed.get(requested_value.lower(), requested_value)

        existing = self._storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(
                principal_name=request.principal_name,
                principal_type=request.principal_type,
                attribute_key=request.attribute_key,
                include_inactive=False,
            )
        )
        now = datetime.now(UTC)
        for item in existing:
            if item.attribute_value.lower() == canonical_value.lower() and item.active:
                if request.expires_at is not None and request.expires_at != item.expires_at:
                    return self.update_expiry(item.assignment_id, request.expires_at, actor)
                return item

        assignment = IdentityAttributeAssignment(
            assignment_id=str(uuid.uuid4()),
            principal_name=request.principal_name.strip(),
            principal_type=request.principal_type,
            attribute_key=request.attribute_key,
            attribute_value=canonical_value,
            source=source,
            granted_by=actor.user_name,
            justification=request.justification,
            expires_at=request.expires_at,
            created_at=now,
        )
        saved = self._storage.identity_attributes.add_assignment(assignment)
        self._mirror_assignment(saved)
        self._audit(
            "identity_attribute.assigned",
            actor.user_name,
            {
                "assignment_id": saved.assignment_id,
                "principal_name": saved.principal_name,
                "principal_type": saved.principal_type.value,
                "attribute_key": saved.attribute_key,
                "attribute_value": saved.attribute_value,
                "source": saved.source.value,
            },
        )
        self._notify(ASSIGNMENT_ASSIGNED, saved, actor)
        return saved

    def update_expiry(
        self, assignment_id: str, expires_at: datetime, actor: CurrentUser
    ) -> IdentityAttributeAssignment:
        """Change the expiry of an active assignment.

        Persists the change, mirrors it to Unity Catalog, and records an
        ``identity_attribute.renewed`` audit event.

        Args:
            assignment_id: Identifier of the active assignment.
            expires_at: New expiry.
            actor: The admin or approver making the change.

        Returns:
            The updated assignment.

        Raises:
            NotFoundError: If the assignment does not exist or is inactive.
        """
        current = self._storage.identity_attributes.get_assignment(assignment_id)
        if current is None or not current.active:
            raise NotFoundError(f"Assignment {assignment_id} was not found")
        updated = self._storage.identity_attributes.update_assignment_expiry(
            assignment_id, expires_at, actor.user_name
        )
        if updated is None:
            raise NotFoundError(f"Assignment {assignment_id} was not found")
        self._mirror_assignment(updated)
        self._audit(
            "identity_attribute.renewed",
            actor.user_name,
            {
                "assignment_id": updated.assignment_id,
                "principal_name": updated.principal_name,
                "attribute_key": updated.attribute_key,
                "attribute_value": updated.attribute_value,
                "expires_at": updated.expires_at.isoformat() if updated.expires_at else None,
            },
        )
        return updated

    def revoke(self, assignment_id: str, actor: CurrentUser) -> IdentityAttributeAssignment:
        """Revoke an assignment so UDFs stop honoring it.

        Idempotent for already-inactive assignments (returned unchanged, no audit). Otherwise
        marks it revoked in storage and in the Unity Catalog mirror and records an
        ``identity_attribute.revoked`` audit event.

        Args:
            assignment_id: Identifier of the assignment.
            actor: The admin revoking it.

        Returns:
            The revoked (or already inactive) assignment.

        Raises:
            NotFoundError: If the assignment does not exist.
        """
        current = self._storage.identity_attributes.get_assignment(assignment_id)
        if current is None:
            raise NotFoundError(f"Assignment {assignment_id} was not found")
        if not current.active:
            return current
        updated = self._storage.identity_attributes.revoke_assignment(
            assignment_id, actor.user_name, datetime.now(UTC)
        )
        if updated is None:
            raise NotFoundError(f"Assignment {assignment_id} was not found")
        if self._uc_sync is not None:
            revoked_at = updated.revoked_at or datetime.now(UTC)
            self._uc_sync.repo.revoke_assignment(assignment_id, actor.user_name, revoked_at)
        self._audit(
            "identity_attribute.revoked",
            actor.user_name,
            {
                "assignment_id": assignment_id,
                "principal_name": updated.principal_name,
                "attribute_key": updated.attribute_key,
                "attribute_value": updated.attribute_value,
            },
        )
        self._notify(ASSIGNMENT_REVOKED, updated, actor)
        return updated

    def sync_to_uc(self) -> dict[str, int]:
        """Full replace of the UC Delta mirror from primary storage.

        Use to repair drift; regular writes are mirrored incrementally.

        Returns:
            Counts of mirrored ``definitions`` and ``assignments``.

        Raises:
            InvalidRequestError: If no Unity Catalog mirror is configured.
            UpstreamError: If the mirror DDL or writes fail.
        """
        if self._uc_sync is None:
            raise InvalidRequestError("UC identity sync is not configured for this deployment")
        return self._uc_sync.full_sync(self._storage.identity_attributes)

    def _mirror_definition(self, definition: AttributeDefinition) -> None:
        """Upsert the definition into the UC mirror without allowed values, if configured."""
        if self._uc_sync is None:
            return
        self._uc_sync.repo.upsert_definition(
            definition.model_copy(update={"allowed_values": []})
        )

    def _mirror_assignment(self, assignment: IdentityAttributeAssignment) -> None:
        """Upsert the assignment into the UC mirror, if one is configured."""
        if self._uc_sync is None:
            return
        self._uc_sync.repo.upsert_assignment(assignment)

    def _enforce_purpose_uniqueness(
        self,
        key: str | None,
        tag_keys: list[str],
        *,
        existing: AttributeDefinition | None,
    ) -> None:
        """Allow only one sensitivity and one data-access definition.

        Sensitivity is the definition whose key is ``sensitivity``. Data access is any
        definition that includes the configured access UC tag among ``governed_tag_keys``.

        Args:
            key: Attribute key being saved.
            tag_keys: Governed tags on the definition (empty for free-form / SQL).
            existing: Prior row when updating; None on create.

        Raises:
            InvalidRequestError: When a second purpose-bound definition would be created, or
                when editing a data-access definition would drop the access tag.
        """
        attr_key = (key or "").strip().lower()
        access_tag = self.access_tag_key()
        tags_lower = {tag.lower() for tag in tag_keys}
        links_access_tag = access_tag in tags_lower

        if attr_key == SENSITIVITY_ATTRIBUTE_KEY:
            # Key uniqueness already prevents a second sensitivity row; nothing else to do.
            return

        if links_access_tag or attr_key == access_tag:
            if not links_access_tag:
                raise InvalidRequestError(
                    f"Data-access definitions must include governed tag '{access_tag}'"
                )
            current = self.find_data_access_definition(access_tag)
            if current is not None and current.key.lower() != attr_key:
                raise InvalidRequestError(
                    f"A data-access definition already exists as '{current.key}'. "
                    "Edit that definition instead of creating another."
                )
            return

        # Editing the existing data-access def must not drop the access tag.
        if existing is not None and any(
            tag.lower() == access_tag for tag in existing.governed_tag_keys
        ):
            raise InvalidRequestError(
                f"Data-access definition '{existing.key}' must keep governed tag '{access_tag}'"
            )

    def _enrich(self, definition: AttributeDefinition) -> AttributeDefinition:
        """Return a copy of the definition with its effective allowed values.

        Non-tag definitions get ``*`` added to their stored values. Governed-tag definitions
        resolve values live from their tags (legacy rows without tag keys fall back to a tag
        named after the definition key); if the lookup fails, the stored values are kept and
        the error is surfaced in the description so the definition stays usable.

        Args:
            definition: Definition as stored.

        Returns:
            The enriched copy.
        """
        if definition.value_source != ValueSource.GOVERNED_TAGS:
            return definition.model_copy(
                update={"allowed_values": _with_all_values(definition.allowed_values)}
            )
        tag_keys = definition.governed_tag_keys or (
            [definition.key] if definition.key and not definition.governed_tag_keys else []
        )
        # Legacy rows may only have manual allowed_values and no tag link yet.
        if not tag_keys:
            return definition
        try:
            values = self._allowed_values_for_tags(tag_keys)
        except Exception as exc:  # noqa: BLE001
            # Keep the definition usable even if tag lookup fails temporarily.
            return definition.model_copy(
                update={
                    "governed_tag_keys": tag_keys,
                    "allowed_values": definition.allowed_values,
                    "description": definition.description or str(exc),
                }
            )
        return definition.model_copy(
            update={"governed_tag_keys": tag_keys, "allowed_values": values}
        )

    def _allowed_values_for_tags(self, tag_keys: list[str]) -> list[str]:
        """Resolve assignment vocabulary from governed tags.

        When a definition binds both ``sensitivity_category`` and ``sensitivity``,
        allowed values are category prefixes plus composed ``category.leaf`` paths
        so one ``sensitivity`` attribute can hold hierarchical grants.

        Args:
            tag_keys: Governed tag keys bound to the definition.

        Returns:
            The allowed values, de-duplicated in tag order.

        Raises:
            InvalidRequestError: If a tag does not exist.
            UpstreamError: If tag policies cannot be read.
        """
        key_set = set(tag_keys)
        if "sensitivity_category" in key_set and "sensitivity" in key_set:
            categories = self._governed_tags.get_tag("sensitivity_category").values
            leaves = self._governed_tags.get_tag("sensitivity").values
            values: list[str] = []
            seen: set[str] = set()
            for category in categories:
                if category not in seen:
                    values.append(category)
                    seen.add(category)
            for category in categories:
                for leaf in leaves:
                    path = f"{category}.{leaf}"
                    if path in seen:
                        continue
                    values.append(path)
                    seen.add(path)
            return values
        return self._governed_tags.values_for_keys(tag_keys)

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


def _with_all_values(values: list[str]) -> list[str]:
    """Free-form / SQL vocabularies always allow ``*`` (all rows for a row-filter dimension).

    Args:
        values: Stored vocabulary values.

    Returns:
        The values with ``*`` first and any other ``*`` occurrences removed.
    """
    rest = [value for value in values if value != ALL_VALUES]
    return [ALL_VALUES, *rest]
