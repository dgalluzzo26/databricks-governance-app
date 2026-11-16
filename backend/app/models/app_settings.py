"""App-wide runtime settings (not tied to data-access sync)."""

from __future__ import annotations

from backend.app.models.base import ApiModel


class AppSettings(ApiModel):
    """Admin-editable app runtime settings.

    Attributes:
        disable_metadata_cache: When true, shared Unity Catalog metadata reads (Access Explorer,
            Attribute Explorer, policy lists, enrolled catalogs) bypass the in-process cache so
            tag and policy changes show up on the next request. Intended for demos; leave false
            in normal use.
    """

    disable_metadata_cache: bool = False
