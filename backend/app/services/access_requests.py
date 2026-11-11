"""Sensitivity access requests, category approvers, and renewal inbox.

Users request identity attribute values (hierarchical ``sensitivity`` paths or row-filter
dimension values) with an expiry and justification. Sensitivity requests route to the
approvers of the most-specific configured ``sensitivity_category`` covering the path, else to
app admins; row-filter and other non-sensitivity requests always route to app admins. Approval
grants the attribute through ``IdentityAttributeService`` (which mirrors it to Unity Catalog),
and sensitivity assignments nearing expiry surface in the same inbox for renewal. Every write
records an audit event.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from backend.app.core.errors import ForbiddenError, InvalidRequestError, NotFoundError
from backend.app.models.access_request import (
    RENEWAL_WINDOW_DAYS,
    SENSITIVITY_ATTRIBUTE_KEY,
    AccessRequest,
    AccessRequestApprover,
    AccessRequestList,
    AccessRequestRouteKind,
    AccessRequestStatus,
    ApprovalInbox,
    ApprovalInboxItem,
    ApprovalItemKind,
    CategoryApprover,
    CategoryApproverList,
    CreateAccessRequestBody,
    DecideAccessRequestBody,
    UpsertCategoryApproverRequest,
)
from backend.app.models.audit import AuditEvent
from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    AttributeAssignmentFilter,
    AttributeSource,
    ValueSource,
)
from backend.app.models.principal import PrincipalKind
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.identity_attributes import IdentityAttributeService


def path_prefixes(path: str) -> list[str]:
    """Longest-first dotted prefixes of a sensitivity path.

    Args:
        path: Dotted sensitivity path, e.g. ``pii.contact.high``.

    Returns:
        Prefixes from the full path down to the first segment (``["pii.contact.high",
        "pii.contact", "pii"]``); empty for a blank path. Empty segments are dropped.
    """
    parts = [part for part in path.split(".") if part]
    if not parts:
        return []
    return [".".join(parts[:index]) for index in range(len(parts), 0, -1)]


def resolve_category(path: str, configured_categories: list[str]) -> str | None:
    """Most-specific configured category that prefixes ``path`` (case-insensitive).

    Args:
        path: Dotted sensitivity path being requested or renewed.
        configured_categories: Category paths that have approvers configured.

    Returns:
        The matching category in its configured casing, or None if no category covers
        ``path``.
    """
    by_lower = {item.lower(): item for item in configured_categories}
    for prefix in path_prefixes(path):
        hit = by_lower.get(prefix.lower())
        if hit is not None:
            return hit
    return None


class AccessRequestService:
    """Access request lifecycle, category approver registry, and approvals inbox.

    Args:
        storage: Storage provider for requests, approvers, assignments, and audit events.
        identity_attributes: Service used to validate vocabularies and grant or renew
            attribute assignments (including the Unity Catalog mirror).
    """

    def __init__(
        self,
        storage: StorageProvider,
        identity_attributes: IdentityAttributeService,
    ) -> None:
        self._storage = storage
        self._identity = identity_attributes

    # --- category approvers ---

    def list_approvers(
        self,
        category_path: str | None = None,
        *,
        attribute_key: str | None = None,
    ) -> CategoryApproverList:
        """List configured approvers.

        Args:
            category_path: Restrict to one path; None lists every path.
            attribute_key: Restrict to ``sensitivity`` or a row-filter dimension; None lists all.

        Returns:
            The approvers and their count.
        """
        items = self._storage.category_approvers.list_approvers(
            category_path, attribute_key=attribute_key
        )
        return CategoryApproverList(approvers=items, total=len(items))

    def is_category_approver(self, actor: CurrentUser) -> bool:
        """True when the actor is listed (or in a listed group) for any approver scope.

        Covers sensitivity categories and row-filter dimensions. App admins always qualify.
        Used to decide whether to show the approvals inbox.

        Args:
            actor: The signed-in user.

        Returns:
            Whether the actor can approve requests for at least one configured scope.
        """
        if actor.is_admin:
            return True
        actor_groups = {group.lower() for group in actor.groups}
        for approver in self._storage.category_approvers.list_approvers():
            if (
                approver.principal_type == PrincipalKind.USER
                and approver.principal_name.lower() == actor.user_name.lower()
            ):
                return True
            if (
                approver.principal_type == PrincipalKind.GROUP
                and approver.principal_name.lower() in actor_groups
            ):
                return True
        return False

    def upsert_approver(
        self, request: UpsertCategoryApproverRequest, actor: CurrentUser
    ) -> CategoryApprover:
        """Add or update an approver for sensitivity, data-access, or a row-filter dimension.

        Sensitivity categories must be current ``sensitivity_category`` values. Data-access
        paths must be allowed values on the data-access definition (hierarchical routing).
        Row-filter dimensions must already exist as non-governed-tag attribute definitions;
        the stored path is the dimension key. Records a ``sensitivity_category_approver.upserted``
        audit event (legacy name; payload includes ``attribute_key``).

        Args:
            request: Attribute key, path, and the USER or GROUP principal to register.
            actor: The admin making the change.

        Returns:
            The saved approver.

        Raises:
            InvalidRequestError: If the principal is not a USER or GROUP, or the path / dimension
                is not valid.
        """
        if request.principal_type not in {PrincipalKind.USER, PrincipalKind.GROUP}:
            raise InvalidRequestError("Approvers must be USER or GROUP principals")
        attribute_key = (request.attribute_key or SENSITIVITY_ATTRIBUTE_KEY).strip().lower()
        data_access = self._identity.find_data_access_definition()
        data_access_key = data_access.key.lower() if data_access is not None else None
        if attribute_key == SENSITIVITY_ATTRIBUTE_KEY:
            categories = self._sensitivity_category_values()
            allowed = {value.lower(): value for value in categories}
            if request.category_path.lower() not in allowed:
                raise InvalidRequestError(
                    f"Category '{request.category_path}' is not a sensitivity_category value. "
                    f"Allowed: {', '.join(categories)}"
                )
            category = allowed[request.category_path.lower()]
        elif data_access_key is not None and attribute_key == data_access_key:
            # Hierarchical paths from the data-access vocabulary (most-specific wins at decide time).
            allowed_values = [
                value for value in data_access.allowed_values if value and value != "*"
            ]
            allowed = {value.lower(): value for value in allowed_values}
            if request.category_path.lower() not in allowed:
                raise InvalidRequestError(
                    f"Path '{request.category_path}' is not an allowed data-access value. "
                    f"Allowed: {', '.join(allowed_values) or '(none — configure the definition)'}"
                )
            category = allowed[request.category_path.lower()]
            attribute_key = data_access.key
        else:
            definition = self._storage.identity_attributes.get_definition(attribute_key)
            if definition is None or definition.value_source == ValueSource.GOVERNED_TAGS:
                raise InvalidRequestError(
                    f"'{attribute_key}' is not a configured row-filter dimension or data-access attribute"
                )
            # Dimension approvers are exact-match on the dimension key.
            if request.category_path.strip().lower() not in {attribute_key, definition.key.lower()}:
                raise InvalidRequestError(
                    f"Row-filter approvers use the dimension key as the path "
                    f"(expected '{definition.key}')"
                )
            category = definition.key
            attribute_key = definition.key
        saved = self._storage.category_approvers.upsert_approver(
            CategoryApprover(
                approver_id=str(uuid.uuid4()),
                attribute_key=attribute_key,
                category_path=category,
                principal_name=request.principal_name,
                principal_type=request.principal_type,
                created_by=actor.user_name,
                created_at=datetime.now(UTC),
            )
        )
        self._audit(
            "sensitivity_category_approver.upserted",
            actor.user_name,
            {
                "approver_id": saved.approver_id,
                "attribute_key": saved.attribute_key,
                "category_path": saved.category_path,
                "principal_name": saved.principal_name,
                "principal_type": saved.principal_type.value,
            },
        )
        return saved

    def delete_approver(self, approver_id: str, actor: CurrentUser) -> None:
        """Remove a category approver and record a ``sensitivity_category_approver.deleted`` audit.

        Args:
            approver_id: Identifier of the approver entry.
            actor: The admin making the change.

        Raises:
            NotFoundError: If no approver has that identifier.
        """
        deleted = self._storage.category_approvers.delete_approver(approver_id)
        if not deleted:
            raise NotFoundError(f"Approver {approver_id} was not found")
        self._audit(
            "sensitivity_category_approver.deleted",
            actor.user_name,
            {"approver_id": approver_id},
        )

    # --- requests ---

    def create_request(self, body: CreateAccessRequestBody, actor: CurrentUser) -> AccessRequest:
        """Submit a pending request for an identity attribute value.

        The attribute key defaults to ``sensitivity``; the value is canonicalized to the
        definition's vocabulary casing. Sensitivity requests record the resolved category;
        other attributes (row-filter dimensions) route to app admins. Records an
        ``access_request.created`` audit event.

        Args:
            body: Attribute key/value, requested expiry, and justification.
            actor: The requesting user.

        Returns:
            The created request annotated with its current routing and approvers.

        Raises:
            InvalidRequestError: If the expiry is not in the future, the attribute definition
                is missing, the value is not in its vocabulary, or the actor already has a
                pending request for the same key and value.
        """
        expires = self._require_future(body.requested_expires_at, field="requested_expires_at")
        key = (body.attribute_key or SENSITIVITY_ATTRIBUTE_KEY).strip().lower()
        value = self._canonicalize_request_value(key, body.attribute_value)
        if self._storage.access_requests.has_pending_for(
            requester_name=actor.user_name,
            attribute_key=key,
            attribute_value=value,
        ):
            raise InvalidRequestError(
                f"You already have a pending request for {key}='{value}'"
            )
        now = datetime.now(UTC)
        routing = self.resolve_attribute_routing(key, value)
        created = self._storage.access_requests.create(
            AccessRequest(
                request_id=str(uuid.uuid4()),
                requester_name=actor.user_name,
                attribute_key=key,
                attribute_value=value,
                requested_expires_at=expires,
                justification=body.justification,
                status=AccessRequestStatus.PENDING,
                created_at=now,
                resolved_category=routing["resolved_category"],
            )
        )
        self._audit(
            "access_request.created",
            actor.user_name,
            {
                "request_id": created.request_id,
                "attribute_key": created.attribute_key,
                "attribute_value": created.attribute_value,
                "requested_expires_at": created.requested_expires_at.isoformat(),
                "routed_to": routing["routed_to"].value,
                "resolved_category": routing["resolved_category"],
            },
        )
        return self._with_routing(created)

    def list_mine(self, actor: CurrentUser) -> AccessRequestList:
        """List the actor's own requests with routing resolved against current approvers.

        Args:
            actor: The signed-in user.

        Returns:
            The actor's requests in every status.
        """
        items = [
            self._with_routing(item)
            for item in self._storage.access_requests.list_for_requester(actor.user_name)
        ]
        return AccessRequestList(requests=items, total=len(items))

    def resolve_routing(self, path: str) -> dict:
        """Who reviews a sensitivity request for ``path``.

        Most-specific configured category with at least one approver wins. If none is
        configured (or the matched category has no approvers), app admins decide.

        Args:
            path: Sensitivity path being requested.

        Returns:
            A dict with ``resolved_category`` (str or None), ``routed_to``
            (``AccessRequestRouteKind``), and ``approvers`` (list of
            ``AccessRequestApprover``, empty when routed to app admins).
        """
        return self.resolve_attribute_routing(SENSITIVITY_ATTRIBUTE_KEY, path)

    def resolve_attribute_routing(self, attribute_key: str, attribute_value: str) -> dict:
        """Who reviews a request for ``attribute_key`` / ``attribute_value``.

        Sensitivity and data-access use most-specific path routing. Row-filter dimensions use
        exact designated approvers for that dimension key; when none are configured, app admins
        decide.

        Args:
            attribute_key: ``sensitivity``, the data-access key, or a row-filter dimension key.
            attribute_value: Requested value (path or dimension value).

        Returns:
            A dict with ``resolved_category`` (str or None), ``routed_to``
            (``AccessRequestRouteKind``), and ``approvers`` (list of
            ``AccessRequestApprover``, empty when routed to app admins).
        """
        key = (attribute_key or SENSITIVITY_ATTRIBUTE_KEY).strip().lower()
        data_access = self._identity.find_data_access_definition()
        data_access_key = data_access.key.lower() if data_access is not None else None
        if key == SENSITIVITY_ATTRIBUTE_KEY or (
            data_access_key is not None and key == data_access_key
        ):
            route_key = SENSITIVITY_ATTRIBUTE_KEY if key == SENSITIVITY_ATTRIBUTE_KEY else data_access.key
            configured = self._storage.category_approvers.list_configured_categories(
                attribute_key=route_key
            )
            category = resolve_category(attribute_value, configured)
            approvers: list[AccessRequestApprover] = []
            if category is not None:
                approvers = [
                    AccessRequestApprover(
                        principal_name=item.principal_name,
                        principal_type=item.principal_type,
                    )
                    for item in self._storage.category_approvers.list_approvers(
                        category, attribute_key=route_key
                    )
                ]
            if not approvers:
                return {
                    "resolved_category": category,
                    "routed_to": AccessRequestRouteKind.APP_ADMINS,
                    "approvers": [],
                }
            return {
                "resolved_category": category,
                "routed_to": AccessRequestRouteKind.CATEGORY_APPROVERS,
                "approvers": approvers,
            }

        # Row-filter (or other) dimension: designated approvers for this attribute key.
        dimension_approvers = self._storage.category_approvers.list_approvers(
            key, attribute_key=key
        )
        if not dimension_approvers:
            # Also accept rows stored with attribute_key only (path may match key casing).
            dimension_approvers = [
                item
                for item in self._storage.category_approvers.list_approvers(attribute_key=key)
            ]
        if not dimension_approvers:
            return {
                "resolved_category": None,
                "routed_to": AccessRequestRouteKind.APP_ADMINS,
                "approvers": [],
            }
        return {
            "resolved_category": key,
            "routed_to": AccessRequestRouteKind.CATEGORY_APPROVERS,
            "approvers": [
                AccessRequestApprover(
                    principal_name=item.principal_name,
                    principal_type=item.principal_type,
                )
                for item in dimension_approvers
            ],
        }

    def _with_routing(self, request: AccessRequest) -> AccessRequest:
        """Return a copy of ``request`` with live routing fields."""
        routing = self.resolve_attribute_routing(request.attribute_key, request.attribute_value)
        return request.model_copy(
            update={
                "resolved_category": routing["resolved_category"],
                "routed_to": routing["routed_to"],
                "approvers": routing["approvers"],
            }
        )

    def cancel(self, request_id: str, actor: CurrentUser) -> AccessRequest:
        """Cancel a pending request and record an ``access_request.cancelled`` audit event.

        Args:
            request_id: Identifier of the request.
            actor: The requester or an app admin.

        Returns:
            The cancelled request.

        Raises:
            NotFoundError: If the request does not exist.
            ForbiddenError: If the actor is neither the requester nor an app admin.
            InvalidRequestError: If the request is no longer pending.
        """
        current = self._storage.access_requests.get(request_id)
        if current is None:
            raise NotFoundError(f"Request {request_id} was not found")
        if current.requester_name.lower() != actor.user_name.lower() and not actor.is_admin:
            raise ForbiddenError("Only the requester can cancel this request")
        if current.status != AccessRequestStatus.PENDING:
            raise InvalidRequestError("Only pending requests can be cancelled")
        updated = current.model_copy(
            update={
                "status": AccessRequestStatus.CANCELLED,
                "decided_by": actor.user_name,
                "decided_at": datetime.now(UTC),
            }
        )
        saved = self._storage.access_requests.save(updated)
        self._audit("access_request.cancelled", actor.user_name, {"request_id": request_id})
        return saved

    def approve_request(
        self, request_id: str, body: DecideAccessRequestBody, actor: CurrentUser
    ) -> AccessRequest:
        """Approve a pending request and grant the attribute to the requester.

        Creates (or extends) a USER assignment with source ``ACCESS_REQUEST``, which is
        persisted and mirrored to Unity Catalog by the identity service, then records an
        ``access_request.approved`` audit event.

        Args:
            request_id: Identifier of the request.
            body: Optional approved expiry (defaults to the requested expiry) and note.
            actor: The deciding approver or app admin.

        Returns:
            The approved request.

        Raises:
            NotFoundError: If the request does not exist.
            InvalidRequestError: If the request is not pending, the expiry is not in the
                future, or the value is no longer valid for the attribute.
            ForbiddenError: If the actor may not decide this request (sensitivity: not an
                approver of the resolved category nor an app admin; other attributes: not an
                app admin).
        """
        current = self._require_pending(request_id)
        self._require_can_decide(
            actor, current.attribute_value, attribute_key=current.attribute_key
        )
        expires = self._require_future(
            body.expires_at or current.requested_expires_at, field="expires_at"
        )
        self._identity.assign(
            AssignAttributeRequest(
                principal_name=current.requester_name,
                principal_type=PrincipalKind.USER,
                attribute_key=current.attribute_key,
                attribute_value=current.attribute_value,
                justification=current.justification,
                expires_at=expires,
            ),
            actor,
            source=AttributeSource.ACCESS_REQUEST,
        )
        updated = current.model_copy(
            update={
                "status": AccessRequestStatus.APPROVED,
                "decided_by": actor.user_name,
                "decided_at": datetime.now(UTC),
                "decision_note": body.note,
                "approved_expires_at": expires,
            }
        )
        saved = self._storage.access_requests.save(updated)
        self._audit(
            "access_request.approved",
            actor.user_name,
            {
                "request_id": request_id,
                "attribute_value": saved.attribute_value,
                "approved_expires_at": expires.isoformat(),
            },
        )
        return saved

    def deny_request(
        self, request_id: str, body: DecideAccessRequestBody, actor: CurrentUser
    ) -> AccessRequest:
        """Deny a pending request and record an ``access_request.denied`` audit event.

        Args:
            request_id: Identifier of the request.
            body: Optional decision note.
            actor: The deciding approver or app admin.

        Returns:
            The denied request.

        Raises:
            NotFoundError: If the request does not exist.
            InvalidRequestError: If the request is not pending.
            ForbiddenError: If the actor may not decide this request.
        """
        current = self._require_pending(request_id)
        self._require_can_decide(
            actor, current.attribute_value, attribute_key=current.attribute_key
        )
        updated = current.model_copy(
            update={
                "status": AccessRequestStatus.DENIED,
                "decided_by": actor.user_name,
                "decided_at": datetime.now(UTC),
                "decision_note": body.note,
            }
        )
        saved = self._storage.access_requests.save(updated)
        self._audit(
            "access_request.denied",
            actor.user_name,
            {"request_id": request_id, "attribute_value": saved.attribute_value},
        )
        return saved

    def approve_renewal(
        self, assignment_id: str, body: DecideAccessRequestBody, actor: CurrentUser
    ):
        """Extend an expiring sensitivity assignment from the approvals inbox.

        Updates the assignment's expiry through the identity service (persisted and
        mirrored to Unity Catalog) and records an ``access_request.renewal_approved`` audit.

        Args:
            assignment_id: Identifier of the active sensitivity assignment.
            body: Optional new expiry (defaults to the current expiry) and note.
            actor: The deciding approver or app admin.

        Returns:
            The updated ``IdentityAttributeAssignment``.

        Raises:
            NotFoundError: If the assignment does not exist or is inactive.
            InvalidRequestError: If it is not a sensitivity assignment, has no expiry, is
                outside the renewal window, or the new expiry is not in the future.
            ForbiddenError: If the actor is not an approver for the path's category nor an
                app admin.
        """
        current = self._storage.identity_attributes.get_assignment(assignment_id)
        if current is None or not current.active:
            raise NotFoundError(f"Assignment {assignment_id} was not found")
        if current.attribute_key != SENSITIVITY_ATTRIBUTE_KEY:
            raise InvalidRequestError("Only sensitivity assignments can be renewed here")
        if current.expires_at is None:
            raise InvalidRequestError("Assignment has no expiry to renew")
        self._require_can_decide(actor, current.attribute_value)
        if not self._in_renewal_window(current.expires_at):
            raise InvalidRequestError(
                f"Assignment is not within the {RENEWAL_WINDOW_DAYS}-day renewal window"
            )
        expires = self._require_future(
            body.expires_at or current.expires_at, field="expires_at"
        )
        updated = self._identity.update_expiry(assignment_id, expires, actor)
        self._audit(
            "access_request.renewal_approved",
            actor.user_name,
            {
                "assignment_id": assignment_id,
                "attribute_value": updated.attribute_value,
                "expires_at": expires.isoformat(),
            },
        )
        return updated

    def dismiss_renewal(self, assignment_id: str, actor: CurrentUser) -> None:
        """Hide an expiring assignment from the renewal inbox for its current expiry.

        The dismissal is keyed on the assignment's current ``expires_at``, so the item
        reappears if the expiry later changes. The assignment itself is untouched and still
        expires. Records an ``access_request.renewal_dismissed`` audit event.

        Args:
            assignment_id: Identifier of the active, expiring assignment.
            actor: The deciding approver or app admin.

        Raises:
            NotFoundError: If the assignment does not exist, is inactive, or has no expiry.
            ForbiddenError: If the actor is not an approver for the path's category nor an
                app admin.
        """
        current = self._storage.identity_attributes.get_assignment(assignment_id)
        if current is None or not current.active or current.expires_at is None:
            raise NotFoundError(f"Assignment {assignment_id} was not found")
        self._require_can_decide(actor, current.attribute_value)
        self._storage.access_requests.dismiss_renewal(
            assignment_id, current.expires_at, actor.user_name, datetime.now(UTC)
        )
        self._audit(
            "access_request.renewal_dismissed",
            actor.user_name,
            {"assignment_id": assignment_id, "attribute_value": current.attribute_value},
        )

    def approvals_inbox(self, actor: CurrentUser) -> ApprovalInbox:
        """Collect pending requests and due renewals that the actor may decide.

        Renewals are active sensitivity assignments expiring within the renewal window
        that have not been dismissed for their current expiry and have no pending request
        for the same value. App admins see everything; category approvers see items whose
        path resolves to one of their categories.

        Args:
            actor: The signed-in approver or app admin.

        Returns:
            Inbox items sorted by soonest expiry.
        """
        configured = self._storage.category_approvers.list_configured_categories(
            attribute_key=SENSITIVITY_ATTRIBUTE_KEY
        )
        pending = self._storage.access_requests.list_by_status(AccessRequestStatus.PENDING)
        items: list[ApprovalInboxItem] = []

        for request in pending:
            if not self._can_decide_for(
                actor, request.attribute_value, attribute_key=request.attribute_key
            ):
                continue
            if request.attribute_key == SENSITIVITY_ATTRIBUTE_KEY:
                category = resolve_category(request.attribute_value, configured)
            else:
                routing = self.resolve_attribute_routing(
                    request.attribute_key, request.attribute_value
                )
                category = routing["resolved_category"]
            items.append(
                ApprovalInboxItem(
                    kind=ApprovalItemKind.REQUEST,
                    request=self._with_routing(request),
                    resolved_category=category,
                    attribute_value=request.attribute_value,
                    principal_name=request.requester_name,
                    expires_at=request.requested_expires_at,
                )
            )

        now = datetime.now(UTC)
        window_end = now + timedelta(days=RENEWAL_WINDOW_DAYS)
        assignments = self._storage.identity_attributes.list_assignments(
            AttributeAssignmentFilter(
                attribute_key=SENSITIVITY_ATTRIBUTE_KEY,
                include_inactive=False,
            )
        )
        for assignment in assignments:
            if assignment.expires_at is None:
                continue
            expires = assignment.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=UTC)
            if not (now < expires <= window_end):
                continue
            if self._storage.access_requests.is_renewal_dismissed(
                assignment.assignment_id, assignment.expires_at
            ):
                continue
            if self._storage.access_requests.has_pending_for(
                requester_name=assignment.principal_name,
                attribute_key=assignment.attribute_key,
                attribute_value=assignment.attribute_value,
            ):
                continue
            category = resolve_category(assignment.attribute_value, configured)
            if not self._can_decide(
                actor,
                assignment.attribute_value,
                category,
                attribute_key=SENSITIVITY_ATTRIBUTE_KEY,
            ):
                continue
            items.append(
                ApprovalInboxItem(
                    kind=ApprovalItemKind.RENEWAL,
                    assignment=assignment,
                    resolved_category=category,
                    attribute_value=assignment.attribute_value,
                    principal_name=assignment.principal_name,
                    expires_at=assignment.expires_at,
                )
            )

        items.sort(
            key=lambda item: item.expires_at or datetime.max.replace(tzinfo=UTC)
        )
        return ApprovalInbox(items=items, total=len(items))

    # --- helpers ---

    def _canonicalize_request_value(self, attribute_key: str, attribute_value: str) -> str:
        """Map a requested value to its vocabulary casing.

        Args:
            attribute_key: Attribute definition key.
            attribute_value: Requested value (case-insensitive).

        Returns:
            The value as spelled in the definition's allowed values.

        Raises:
            InvalidRequestError: If the definition is missing or the value is not allowed.
        """
        definition = self._storage.identity_attributes.get_definition(attribute_key)
        if definition is None:
            raise InvalidRequestError(
                f"Attribute definition '{attribute_key}' is not configured"
            )
        enriched = self._identity._enrich(definition)  # noqa: SLF001
        allowed = {value.lower(): value for value in enriched.allowed_values}
        if attribute_value.lower() not in allowed:
            raise InvalidRequestError(
                f"Value '{attribute_value}' is not allowed for '{attribute_key}'"
            )
        return allowed[attribute_value.lower()]

    def _sensitivity_allowed_values(self) -> list[str]:
        """Return the sensitivity vocabulary.

        Raises:
            InvalidRequestError: If the sensitivity attribute definition is not configured.
        """
        definition = self._storage.identity_attributes.get_definition(SENSITIVITY_ATTRIBUTE_KEY)
        if definition is None:
            raise InvalidRequestError(
                "Sensitivity attribute definition is not configured"
            )
        enriched = self._identity._enrich(definition)  # noqa: SLF001
        return enriched.allowed_values

    def _sensitivity_category_values(self) -> list[str]:
        """Return the ``sensitivity_category`` governed tag values.

        Raises:
            InvalidRequestError: If the governed tag cannot be read.
        """
        try:
            return self._identity._governed_tags.get_tag("sensitivity_category").values  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            raise InvalidRequestError(
                f"Could not load sensitivity_category values: {exc}"
            ) from exc

    def _require_pending(self, request_id: str) -> AccessRequest:
        """Load a request that must still be pending.

        Raises:
            NotFoundError: If the request does not exist.
            InvalidRequestError: If the request is not pending.
        """
        current = self._storage.access_requests.get(request_id)
        if current is None:
            raise NotFoundError(f"Request {request_id} was not found")
        if current.status != AccessRequestStatus.PENDING:
            raise InvalidRequestError("Request is not pending")
        return current

    def _require_can_decide(
        self, actor: CurrentUser, path: str, *, attribute_key: str = SENSITIVITY_ATTRIBUTE_KEY
    ) -> None:
        """Ensure the actor may decide a request or renewal for ``path``.

        Raises:
            ForbiddenError: If the actor is not authorized for this attribute and path.
        """
        if not self._can_decide_for(actor, path, attribute_key=attribute_key):
            if attribute_key == SENSITIVITY_ATTRIBUTE_KEY:
                raise ForbiddenError("You are not an approver for this sensitivity category")
            data_access = self._identity.find_data_access_definition()
            if data_access is not None and attribute_key.lower() == data_access.key.lower():
                raise ForbiddenError("You are not an approver for this data-access path")
            raise ForbiddenError("You are not an approver for this row-filter dimension")

    def _can_decide_for(
        self, actor: CurrentUser, path: str, *, attribute_key: str = SENSITIVITY_ATTRIBUTE_KEY
    ) -> bool:
        """Whether the actor may decide a request for this attribute and path/value."""
        key = (attribute_key or SENSITIVITY_ATTRIBUTE_KEY).strip().lower()
        data_access = self._identity.find_data_access_definition()
        data_access_key = data_access.key.lower() if data_access is not None else None
        if key == SENSITIVITY_ATTRIBUTE_KEY or (
            data_access_key is not None and key == data_access_key
        ):
            route_key = (
                SENSITIVITY_ATTRIBUTE_KEY if key == SENSITIVITY_ATTRIBUTE_KEY else data_access.key
            )
            category = resolve_category(
                path,
                self._storage.category_approvers.list_configured_categories(attribute_key=route_key),
            )
            return self._can_decide(actor, path, category, attribute_key=route_key)
        routing = self.resolve_attribute_routing(key, path)
        # No designated dimension approvers → app admins only.
        if routing["routed_to"] == AccessRequestRouteKind.APP_ADMINS:
            return actor.is_admin
        return self._can_decide(actor, path, key, attribute_key=key)

    def _can_decide(
        self,
        actor: CurrentUser,
        path: str,
        category: str | None,
        *,
        attribute_key: str = SENSITIVITY_ATTRIBUTE_KEY,
    ) -> bool:
        """Whether the actor is an app admin or a USER/GROUP approver of ``category``."""
        # App admins always see and decide unrouted requests and can override.
        if actor.is_admin:
            return True
        if category is None:
            return False
        approvers = self._storage.category_approvers.list_approvers(
            category, attribute_key=attribute_key
        )
        if not approvers:
            # Category / dimension exists but no reviewers — only app admins (handled above).
            return False
        actor_groups = {group.lower() for group in actor.groups}
        for approver in approvers:
            if (
                approver.principal_type == PrincipalKind.USER
                and approver.principal_name.lower() == actor.user_name.lower()
            ):
                return True
            if (
                approver.principal_type == PrincipalKind.GROUP
                and approver.principal_name.lower() in actor_groups
            ):
                return True
        return False

    @staticmethod
    def _require_future(value: datetime, *, field: str) -> datetime:
        """Return ``value`` as an aware UTC datetime (naive treated as UTC) that is in the future.

        Raises:
            InvalidRequestError: If the value is not in the future.
        """
        expires = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            raise InvalidRequestError(f"{field} must be in the future")
        return expires

    @staticmethod
    def _in_renewal_window(expires_at: datetime) -> bool:
        """Whether ``expires_at`` is in the future but within the renewal window."""
        expires = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return now < expires <= now + timedelta(days=RENEWAL_WINDOW_DAYS)

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
