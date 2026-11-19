"""App-wide runtime settings (not tied to data-access sync)."""

from __future__ import annotations

from pydantic import Field

from backend.app.models.base import ApiModel


class AppSettings(ApiModel):
    """Admin-editable app runtime settings.

    Attributes:
        disable_metadata_cache: When true, shared Unity Catalog metadata reads (Access Explorer,
            Attribute Explorer, policy lists, enrolled catalogs) bypass the in-process cache so
            tag and policy changes show up on the next request. Intended for demos; leave false
            in normal use.
        subscriptions_require_admin_approval: When true, subscriptions created by approvers
            wait for an app admin to approve them. Admin-created subscriptions are always
            approved on creation.
        subscription_max_days: Longest allowed subscription window (start to end), in days.
    """

    disable_metadata_cache: bool = False
    subscriptions_require_admin_approval: bool = True
    subscription_max_days: int = Field(default=365, ge=1, le=3650)
