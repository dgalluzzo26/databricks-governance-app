"""Sensitivity access requests, category approvers, and renewal inbox."""

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
)
from backend.app.models.principal import PrincipalKind
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider
from backend.app.services.identity_attributes import IdentityAttributeService


def path_prefixes(path: str) -> list[str]:
    """Longest-first dotted prefixes of a sensitivity path."""
    parts = [part for part in path.split(".") if part]
    if not parts:
        return []
    return [".".join(parts[:index]) for index in range(len(parts), 0, -1)]


def resolve_category(path: str, configured_categories: list[str]) -> str | None:
    """Most-specific configured category that prefixes ``path`` (case-insensitive)."""
    by_lower = {item.lower(): item for item in configured_categories}
    for prefix in path_prefixes(path):
        hit = by_lower.get(prefix.lower())
        if hit is not None:
            return hit
    return None


class AccessRequestService:
    def __init__(
        self,
        storage: StorageProvider,
        identity_attributes: IdentityAttributeService,
    ) -> None:
        self._storage = storage
        self._identity = identity_attributes

    # --- category approvers ---

    def list_approvers(self, category_path: str | None = None) -> CategoryApproverList:
        items = self._storage.category_approvers.list_approvers(category_path)
        return CategoryApproverList(approvers=items, total=len(items))

    def upsert_approver(
        self, request: UpsertCategoryApproverRequest, actor: CurrentUser
    ) -> CategoryApprover:
        if request.principal_type not in {PrincipalKind.USER, PrincipalKind.GROUP}:
            raise InvalidRequestError("Approvers must be USER or GROUP principals")
        categories = self._sensitivity_category_values()
        allowed = {value.lower(): value for value in categories}
        if request.category_path.lower() not in allowed:
            raise InvalidRequestError(
                f"Category '{request.category_path}' is not a sensitivity_category value. "
                f"Allowed: {', '.join(categories)}"
            )
        category = allowed[request.category_path.lower()]
        saved = self._storage.category_approvers.upsert_approver(
            CategoryApprover(
                approver_id=str(uuid.uuid4()),
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
                "category_path": saved.category_path,
                "principal_name": saved.principal_name,
                "principal_type": saved.principal_type.value,
            },
        )
        return saved

    def delete_approver(self, approver_id: str, actor: CurrentUser) -> None:
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
        routing = self.resolve_routing(value if key == SENSITIVITY_ATTRIBUTE_KEY else key)
        # Row-filter / non-sensitivity attributes have no category hierarchy — app admins.
        if key != SENSITIVITY_ATTRIBUTE_KEY:
            routing = {
                "resolved_category": None,
                "routed_to": AccessRequestRouteKind.APP_ADMINS,
                "approvers": [],
            }
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
        items = [
            self._with_routing(item)
            for item in self._storage.access_requests.list_for_requester(actor.user_name)
        ]
        return AccessRequestList(requests=items, total=len(items))

    def resolve_routing(self, path: str) -> dict:
        """Who reviews a sensitivity request for ``path``.

        Most-specific configured category with at least one approver wins. If none is
        configured (or the matched category has no approvers), app admins decide.
        """
        configured = self._storage.category_approvers.list_configured_categories()
        category = resolve_category(path, configured)
        approvers: list[AccessRequestApprover] = []
        if category is not None:
            approvers = [
                AccessRequestApprover(
                    principal_name=item.principal_name,
                    principal_type=item.principal_type,
                )
                for item in self._storage.category_approvers.list_approvers(category)
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

    def _with_routing(self, request: AccessRequest) -> AccessRequest:
        if request.attribute_key != SENSITIVITY_ATTRIBUTE_KEY:
            routing = {
                "resolved_category": None,
                "routed_to": AccessRequestRouteKind.APP_ADMINS,
                "approvers": [],
            }
        else:
            routing = self.resolve_routing(request.attribute_value)
        return request.model_copy(
            update={
                "resolved_category": routing["resolved_category"],
                "routed_to": routing["routed_to"],
                "approvers": routing["approvers"],
            }
        )

    def cancel(self, request_id: str, actor: CurrentUser) -> AccessRequest:
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
        configured = self._storage.category_approvers.list_configured_categories()
        pending = self._storage.access_requests.list_by_status(AccessRequestStatus.PENDING)
        items: list[ApprovalInboxItem] = []

        for request in pending:
            if not self._can_decide_for(
                actor, request.attribute_value, attribute_key=request.attribute_key
            ):
                continue
            category = (
                resolve_category(request.attribute_value, configured)
                if request.attribute_key == SENSITIVITY_ATTRIBUTE_KEY
                else None
            )
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
            if not self._can_decide(actor, assignment.attribute_value, category):
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
        definition = self._storage.identity_attributes.get_definition(SENSITIVITY_ATTRIBUTE_KEY)
        if definition is None:
            raise InvalidRequestError(
                "Sensitivity attribute definition is not configured"
            )
        enriched = self._identity._enrich(definition)  # noqa: SLF001
        return enriched.allowed_values

    def _sensitivity_category_values(self) -> list[str]:
        try:
            return self._identity._governed_tags.get_tag("sensitivity_category").values  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001
            raise InvalidRequestError(
                f"Could not load sensitivity_category values: {exc}"
            ) from exc

    def _require_pending(self, request_id: str) -> AccessRequest:
        current = self._storage.access_requests.get(request_id)
        if current is None:
            raise NotFoundError(f"Request {request_id} was not found")
        if current.status != AccessRequestStatus.PENDING:
            raise InvalidRequestError("Request is not pending")
        return current

    def _require_can_decide(
        self, actor: CurrentUser, path: str, *, attribute_key: str = SENSITIVITY_ATTRIBUTE_KEY
    ) -> None:
        if not self._can_decide_for(actor, path, attribute_key=attribute_key):
            if attribute_key == SENSITIVITY_ATTRIBUTE_KEY:
                raise ForbiddenError("You are not an approver for this sensitivity category")
            raise ForbiddenError("Only app admins can decide this attribute request")

    def _can_decide_for(
        self, actor: CurrentUser, path: str, *, attribute_key: str = SENSITIVITY_ATTRIBUTE_KEY
    ) -> bool:
        if attribute_key != SENSITIVITY_ATTRIBUTE_KEY:
            return actor.is_admin
        category = resolve_category(
            path, self._storage.category_approvers.list_configured_categories()
        )
        return self._can_decide(actor, path, category)

    def _can_decide(
        self, actor: CurrentUser, path: str, category: str | None
    ) -> bool:
        # App admins always see and decide unrouted requests and can override.
        if actor.is_admin:
            return True
        if category is None:
            return False
        approvers = self._storage.category_approvers.list_approvers(category)
        if not approvers:
            # Category exists but no reviewers — only app admins (handled above).
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
        expires = value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            raise InvalidRequestError(f"{field} must be in the future")
        return expires

    @staticmethod
    def _in_renewal_window(expires_at: datetime) -> bool:
        expires = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=UTC)
        now = datetime.now(UTC)
        return now < expires <= now + timedelta(days=RENEWAL_WINDOW_DAYS)

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
