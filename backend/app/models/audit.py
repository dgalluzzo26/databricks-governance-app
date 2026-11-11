"""Audit trail event model."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.models.base import ApiModel


class AuditEvent(ApiModel):
    """Record of a state-changing action performed through the app.

    Attributes:
        event_id: Unique identifier of the event (a UUID string).
        event_type: Machine-readable action name, e.g. an attribute assignment or policy change.
        actor: User name of the principal who performed the action.
        payload: Event-specific JSON-serializable details; empty when there is nothing extra.
        created_at: When the action happened.
    """

    event_id: str
    event_type: str
    actor: str
    payload: dict[str, Any] = {}
    created_at: datetime
