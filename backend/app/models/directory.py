"""Directory search results for users and groups."""

from __future__ import annotations

from backend.app.models.base import ApiModel
from backend.app.models.principal import PrincipalKind


class DirectoryPrincipal(ApiModel):
    """A user or group from the workspace/account directory.

    Attributes:
        name: Value to store as ``principal_name`` (userName for users, displayName for groups).
        display_name: Human-readable label shown in the picker.
        principal_type: ``USER`` or ``GROUP``.
        email: User email when known; None for groups.
    """

    name: str
    display_name: str
    principal_type: PrincipalKind
    email: str | None = None


class DirectoryPrincipalList(ApiModel):
    """Paged search results for the principal picker.

    Attributes:
        principals: Matching principals, truncated to the request limit.
        total: Number of principals returned (not a full directory count).
        truncated: True when more matches may exist beyond the limit.
    """

    principals: list[DirectoryPrincipal]
    total: int
    truncated: bool = False
