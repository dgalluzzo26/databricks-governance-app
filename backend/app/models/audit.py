from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.app.models.base import ApiModel


class AuditEvent(ApiModel):
    event_id: str
    event_type: str
    actor: str
    payload: dict[str, Any] = {}
    created_at: datetime
