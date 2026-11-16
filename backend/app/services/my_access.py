"""Personal access overview: groups, effective attributes, and on-demand resources."""

from __future__ import annotations

from backend.app.core.errors import ForbiddenError, InvalidRequestError, NotFoundError
from backend.app.models.attribute_explorer import AttributeExplorerKind
from backend.app.models.data_access_pending import MembershipState, StructureState
from backend.app.models.identity_attribute import ValueSource
from backend.app.models.my_access import (
    MyAccessAttribute,
    MyAccessGroup,
    MyAccessResources,
    MyAccessResourcesRequest,
    MyAccessSnapshot,
)
from backend.app.models.principal import PrincipalKind
from backend.app.models.user import CurrentUser
from backend.app.services.attribute_explorer import AttributeExplorerService
from backend.app.services.data_access import DataAccessService
from backend.app.services.identity_attributes import IdentityAttributeService


class MyAccessService:
    """Build the signed-in user's access snapshot and lazy resource lookups.

    Args:
        identity: Effective assignments (user + groups).
        data_access: Data-access status for ``dac_*`` paths.
        attribute_explorer: Kind classification and resource inventory.
    """

    def __init__(
        self,
        identity: IdentityAttributeService,
        data_access: DataAccessService,
        attribute_explorer: AttributeExplorerService,
    ) -> None:
        self._identity = identity
        self._data_access = data_access
        self._explorer = attribute_explorer

    def snapshot(self, user: CurrentUser) -> MyAccessSnapshot:
        """Groups the user belongs to and every attribute they hold (direct or via group)."""
        assignments = self._identity.list_for_user(user).assignments
        settings = self._data_access._settings()  # noqa: SLF001
        definitions = {
            d.key.lower(): d for d in self._identity.list_definitions().definitions
        }
        statuses = {
            s.assignment_id: s for s in self._data_access.access_status(assignments)
        }

        group_counts: dict[str, int] = {g: 0 for g in user.attribute_groups}
        attributes: list[MyAccessAttribute] = []
        direct = 0
        inherited = 0
        ready = 0

        for item in assignments:
            definition = definitions.get(item.attribute_key.lower())
            value_source = (
                definition.value_source if definition is not None else ValueSource.FREE_FORM
            )
            kind = self._explorer.classify(item.attribute_key, value_source, settings)
            status = statuses.get(item.assignment_id)
            if (
                status is not None
                and status.membership == MembershipState.MEMBER
                and status.structure == StructureState.READY
            ):
                ready += 1
            if item.principal_type == PrincipalKind.USER:
                direct += 1
            else:
                inherited += 1
                if item.principal_name in group_counts:
                    group_counts[item.principal_name] += 1
                else:
                    # Group name from assignment may differ in casing from /Me.
                    matched = next(
                        (
                            g
                            for g in group_counts
                            if g.lower() == item.principal_name.lower()
                        ),
                        None,
                    )
                    if matched is not None:
                        group_counts[matched] += 1
            attributes.append(
                MyAccessAttribute(
                    assignment_id=item.assignment_id,
                    attribute_key=item.attribute_key,
                    attribute_value=item.attribute_value,
                    kind=kind,
                    principal_type=item.principal_type,
                    principal_name=item.principal_name,
                    expires_at=item.expires_at.isoformat() if item.expires_at else None,
                    access_status=status,
                )
            )

        # Direct first, then by group name, then key/value for stable scanning.
        attributes.sort(
            key=lambda row: (
                0 if row.principal_type == PrincipalKind.USER else 1,
                row.principal_name.lower(),
                row.attribute_key.lower(),
                row.attribute_value.lower(),
            )
        )
        groups = [
            MyAccessGroup(name=name, attribute_count=group_counts.get(name, 0))
            for name in user.attribute_groups
        ]
        return MyAccessSnapshot(
            groups=groups,
            attributes=attributes,
            group_count=len(groups),
            attribute_count=len(attributes),
            direct_attribute_count=direct,
            inherited_attribute_count=inherited,
            data_access_ready_count=ready,
        )

    def resources(self, body: MyAccessResourcesRequest, user: CurrentUser) -> MyAccessResources:
        """Load tables/columns for one attribute the caller holds.

        Raises:
            InvalidRequestError: Blank key or value.
            ForbiddenError: Caller does not hold this attribute (or key for row filters).
            NotFoundError: Attribute definition is missing.
        """
        key = body.attribute_key.strip()
        value = body.attribute_value.strip()
        if not key or not value:
            raise InvalidRequestError("attribute_key and attribute_value are required")

        held = self._identity.list_for_user(user).assignments
        held_for_key = [a for a in held if a.attribute_key.lower() == key.lower()]
        if not held_for_key:
            raise ForbiddenError("You do not hold this attribute")

        try:
            definition = self._identity.get_definition(key)
        except NotFoundError:
            definition = self._identity.get_definition(held_for_key[0].attribute_key)

        settings = self._data_access._settings()  # noqa: SLF001
        kind = self._explorer.classify(definition.key, definition.value_source, settings)

        if kind == AttributeExplorerKind.ROW_FILTER:
            # Resources are by dimension; any held value for the key authorizes.
            tables = self._explorer.resources_for(kind, definition.key, value)
            return MyAccessResources(
                attribute_key=definition.key,
                attribute_value=value,
                kind=kind,
                tables=tables,
            )

        exact = next(
            (
                a
                for a in held_for_key
                if a.attribute_value.lower() == value.lower()
            ),
            None,
        )
        if exact is None:
            raise ForbiddenError("You do not hold this attribute value")

        tables = self._explorer.resources_for(kind, definition.key, exact.attribute_value)
        return MyAccessResources(
            attribute_key=definition.key,
            attribute_value=exact.attribute_value,
            kind=kind,
            tables=tables,
        )
