"""Account group nesting: every group a user reaches through group-to-group membership.

Workspace ``/Me`` lists only groups assigned to this workspace, and only direct memberships.
Account groups can contain other groups. ``dac_finance`` is a member of ``dac_finance_budget``,
so a direct member of ``dac_finance`` also holds ``dac_finance_budget`` for Unity Catalog. This
module reads the account group graph through Account SCIM and walks it upward from the user's
direct memberships, so attribute inheritance matches what grants give the user.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field

from databricks.sdk.errors import DatabricksError

from backend.app.clients.cache import TtlCache
from backend.app.clients.workspace import WorkspaceClientFactory

logger = logging.getLogger(__name__)

_ACCOUNT_SCIM_GROUPS = "/api/2.0/account/scim/v2/Groups"
_PAGE_SIZE = 1000
_GRAPH_KEY: tuple[str] = ("account-group-graph",)


@dataclass(frozen=True)
class AccountGroupGraph:
    """Account groups with membership edges, keyed by SCIM id.

    Attributes:
        names: Group id to display name.
        parents: Group id to ids of the groups that contain it as a member.
        direct: User id to ids of the groups the user is a direct member of.
    """

    names: dict[str, str] = field(default_factory=dict)
    parents: dict[str, set[str]] = field(default_factory=dict)
    direct: dict[str, set[str]] = field(default_factory=dict)


def build_graph(resources: list[dict]) -> AccountGroupGraph:
    """Build the membership graph from Account SCIM group resources.

    Members are classified by their ``$ref`` (``Users/{id}`` or ``Groups/{id}``). Members
    without a recognizable reference are ignored.

    Args:
        resources: Group resources as returned by Account SCIM with ``members`` included.

    Returns:
        The graph used by ``groups_for_user``.
    """
    graph = AccountGroupGraph()
    for resource in resources:
        group_id = str(resource.get("id") or "")
        if not group_id:
            continue
        graph.names[group_id] = str(resource.get("displayName") or group_id)
        for member in resource.get("members") or []:
            ref = str(member.get("$ref") or "").lower()
            member_id = str(member.get("value") or "")
            if not member_id:
                continue
            if ref.startswith("users/") or "/users/" in ref:
                graph.direct.setdefault(member_id, set()).add(group_id)
            elif ref.startswith("groups/") or "/groups/" in ref:
                graph.parents.setdefault(member_id, set()).add(group_id)
    return graph


def groups_for_user(graph: AccountGroupGraph, user_id: str) -> list[str]:
    """Display names of every group the user is in, directly or through nesting.

    Args:
        graph: Membership graph from ``build_graph``.
        user_id: Account SCIM id of the user.

    Returns:
        Sorted, de-duplicated group display names. Empty when the user is in no account group.
    """
    seeds = graph.direct.get(user_id, set())
    seen = set(seeds)
    queue = deque(seeds)
    while queue:
        group_id = queue.popleft()
        for parent in graph.parents.get(group_id, set()):
            if parent not in seen:
                seen.add(parent)
                queue.append(parent)
    names = {graph.names[g] for g in seen if g in graph.names}
    return sorted(names, key=str.lower)


class AccountGroupResolver:
    """Resolves account group memberships for a user, including nested groups.

    The whole account graph is read once per cache TTL, so per-user lookups stay cheap. When
    Account SCIM is unavailable the resolver returns no extra groups and the caller falls back
    to the workspace groups from ``/Me``.

    Args:
        factory: Workspace client factory; the Account Admin SP reads Account SCIM.
        cache: TTL cache shared with identity lookups.
    """

    def __init__(self, factory: WorkspaceClientFactory, cache: TtlCache) -> None:
        self._factory = factory
        self._cache = cache

    def groups_for(self, user_id: str | None) -> list[str]:
        """Account groups the user belongs to, directly or through nesting.

        Args:
            user_id: Account SCIM id of the user, or None when unknown.

        Returns:
            Group display names; empty when Account SCIM is not configured or cannot be read.
        """
        if not user_id or not self._factory.admin_configured:
            return []
        try:
            graph = self._cache.get_or_load(_GRAPH_KEY, self._load_graph)
        except DatabricksError as exc:
            logger.warning("Reading account group memberships failed; using workspace groups: %s", exc)
            return []
        return groups_for_user(graph, user_id)

    def _load_graph(self) -> AccountGroupGraph:
        """Read every account group with its members, following SCIM pagination."""
        admin = self._factory.admin()
        resources: list[dict] = []
        start = 1
        while True:
            payload = admin.api_client.do(
                "GET",
                _ACCOUNT_SCIM_GROUPS,
                query={"attributes": "id,displayName,members", "startIndex": start, "count": _PAGE_SIZE},
            )
            page = list((payload.get("Resources") if isinstance(payload, dict) else None) or [])
            resources.extend(page)
            total = payload.get("totalResults") if isinstance(payload, dict) else None
            if not page or not isinstance(total, int) or len(resources) >= total:
                break
            start += len(page)
        return build_graph(resources)
