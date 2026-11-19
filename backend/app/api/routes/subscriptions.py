"""Subscriptions API under ``/api/subscriptions``.

App admins and approvers create subscriptions; approvers are limited to their access paths
and only see their own. Decisions, extensions, and deletes of decided subscriptions are
admin-only. Writes that change what a subscription grants reconcile it right away; the
scheduled job catches up everything else (start and end times).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from backend.app.api.deps import Container, User
from backend.app.models.subscription import (
    SecurableKind,
    Subscription,
    SubscriptionDecision,
    SubscriptionDraft,
    SubscriptionExtendRequest,
    SubscriptionList,
    SubscriptionOptions,
    SubscriptionPreview,
    SubscriptionStatus,
    SubscriptionSyncState,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def _reconcile(container: Container, subscription: Subscription) -> Subscription:
    """Sync one subscription now; a failure is recorded on it rather than raised."""
    if subscription.status not in (SubscriptionStatus.APPROVED, SubscriptionStatus.REVOKED):
        return subscription
    try:
        container.subscriptions.reconcile(subscription.subscription_id)
    except Exception:  # noqa: BLE001 - the decision is saved; the next scheduled run retries
        logger.exception("Immediate sync of subscription %s failed", subscription.name)
    return container.storage.subscriptions.get_subscription(subscription.subscription_id) or subscription


@router.get("/options", response_model=SubscriptionOptions)
def get_options(user: User, container: Container) -> SubscriptionOptions:
    """Wizard choices for the signed-in user. Any signed-in user."""
    return container.subscriptions.options(user)


@router.get("", response_model=SubscriptionList)
def list_subscriptions(user: User, container: Container) -> SubscriptionList:
    """List subscriptions: all for admins, own for approvers."""
    return container.subscriptions.list(user)


@router.post("/preview", response_model=SubscriptionPreview)
def preview_subscription(body: SubscriptionDraft, user: User, container: Container) -> SubscriptionPreview:
    """Show the objects and grants a draft would reach now. Admins and approvers."""
    return container.subscriptions.preview(body, user)


@router.get("/browse")
def browse_objects(
    kind: SecurableKind, user: User, container: Container, parent: str | None = None
) -> dict[str, list[dict[str, str]]]:
    """List objects of ``kind`` under ``parent`` for the object picker. Admins and approvers."""
    return {"items": container.subscriptions.browse(user, kind, parent)}


@router.post("", response_model=Subscription)
def create_subscription(body: SubscriptionDraft, user: User, container: Container) -> Subscription:
    """Create a subscription. Admins' are approved and applied at once."""
    created = container.subscriptions.create(body, user)
    return _reconcile(container, created)


@router.get("/{subscription_id}", response_model=Subscription)
def get_subscription(subscription_id: str, user: User, container: Container) -> Subscription:
    """Open one subscription."""
    return container.subscriptions.get(subscription_id, user)


@router.put("/{subscription_id}", response_model=Subscription)
def update_subscription(
    subscription_id: str, body: SubscriptionDraft, user: User, container: Container
) -> Subscription:
    """Edit a subscription (admins, or the creator while pending)."""
    updated = container.subscriptions.update(subscription_id, body, user)
    return _reconcile(container, updated)


@router.post("/{subscription_id}/decision", response_model=Subscription)
def decide_subscription(
    subscription_id: str, body: SubscriptionDecision, user: User, container: Container
) -> Subscription:
    """Approve or deny a pending subscription. Requires app admin."""
    decided = container.subscriptions.decide(subscription_id, body, user)
    return _reconcile(container, decided)


@router.post("/{subscription_id}/extend", response_model=Subscription)
def extend_subscription(
    subscription_id: str, body: SubscriptionExtendRequest, user: User, container: Container
) -> Subscription:
    """Move the end time (admins, or the creator while pending)."""
    updated = container.subscriptions.extend(subscription_id, body.expires_at, user)
    return _reconcile(container, updated)


@router.post("/{subscription_id}/revoke", response_model=Subscription)
def revoke_subscription(subscription_id: str, user: User, container: Container) -> Subscription:
    """End a subscription now and strip its access (admins or the creator)."""
    revoked = container.subscriptions.revoke(subscription_id, user)
    return _reconcile(container, revoked)


@router.post("/{subscription_id}/sync", response_model=SubscriptionSyncState)
def sync_subscription(subscription_id: str, user: User, container: Container) -> SubscriptionSyncState:
    """Reconcile one subscription now (admins or the creator)."""
    return container.subscriptions.sync_now(subscription_id, user)


@router.delete("/{subscription_id}", status_code=204)
def delete_subscription(subscription_id: str, user: User, container: Container) -> None:
    """Delete a subscription that no longer holds access. Requires app admin."""
    container.subscriptions.delete(subscription_id, user)
