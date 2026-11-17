"""Expand Account SCIM groups to the user names they contain, nested groups included.

The usage rollup needs every *holder* of a data-access grant, including members who never read
anything, so it cannot rely on per-user lookups (those only cover users seen in usage). This module
lists groups once with their members and resolves member ids to account ``userName`` values.

Member entries carry the person's display name (``David Galluzzo``), not their email, so USER members
are resolved through a paged Account SCIM user listing keyed by SCIM id.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any, Protocol

logger = logging.getLogger(__name__)

ACCOUNT_SCIM_GROUPS = "/api/2.0/account/scim/v2/Groups"
ACCOUNT_SCIM_USERS = "/api/2.0/account/scim/v2/Users"
# Match DataAccessService Account SCIM listings: one large page, warn if truncated.
PAGE_SIZE = 10000


class ScimApi(Protocol):
    """The slice of ``WorkspaceClient.api_client`` used for SCIM listings."""

    def do(self, method: str, path: str, query: dict[str, Any] | None = None) -> Any:
        """Perform one REST call and return the decoded JSON body."""
        ...


class ScimClient(Protocol):
    """Anything exposing ``api_client`` (the Databricks SDK ``WorkspaceClient`` does)."""

    api_client: ScimApi


def list_scim(client: ScimClient, path: str, attributes: str) -> list[dict[str, Any]]:
    """Read Account SCIM resources, paging until exhausted (count capped per request)."""
    resources: list[dict[str, Any]] = []
    start = 1
    total = 0
    while True:
        payload = client.api_client.do(
            "GET",
            path,
            query={"attributes": attributes, "startIndex": start, "count": PAGE_SIZE},
        )
        if not isinstance(payload, dict):
            break
        page = [r for r in (payload.get("Resources") or []) if isinstance(r, dict)]
        resources.extend(page)
        try:
            total = int(payload.get("totalResults") or 0)
        except (TypeError, ValueError):
            total = 0
        start += len(page)
        if not page:
            break
        if total and start > total:
            break
        if not total and len(page) < PAGE_SIZE:
            break
    if total and len(resources) < total:
        logger.warning(
            "Account SCIM %s returned %s of %s resources; later pages may be incomplete",
            path,
            len(resources),
            total,
        )
    return resources


def expand_group_members(client: ScimClient, group_names: Iterable[str]) -> dict[str, set[str]]:
    """Map each requested group (lowercase name) to the lowercase user names it contains.

    Nested groups are followed (cycle-safe). Groups that do not exist in the account map to an
    empty set and are logged, so a stale assignment never fails the whole rollup.
    """
    wanted = sorted({str(name).strip().lower() for name in group_names if str(name or "").strip()})
    if not wanted:
        return {}
    groups = list_scim(client, ACCOUNT_SCIM_GROUPS, "id,displayName,members")
    users = {
        str(u["id"]): str(u.get("userName") or "").strip().lower()
        for u in list_scim(client, ACCOUNT_SCIM_USERS, "id,userName")
        if u.get("id")
    }
    by_id = {str(g["id"]): g for g in groups if g.get("id")}
    by_name: dict[str, dict[str, Any]] = {}
    for group in groups:
        name = str(group.get("displayName") or "").strip().lower()
        if name and name not in by_name:
            by_name[name] = group

    result: dict[str, set[str]] = {}
    for name in wanted:
        root = by_name.get(name)
        if root is None:
            logger.warning("Group %s holds data access but was not found in Account SCIM", name)
            result[name] = set()
            continue
        result[name] = _collect_users(root, by_id, users)
    return result


def _ref_is(ref: str, resource: str) -> bool:
    """True when a SCIM ``$ref`` such as ``Groups/123`` (or ``/Groups/123``) names ``resource``."""
    return ref.startswith(f"{resource}/") or f"/{resource}/" in ref


def _collect_users(root: dict[str, Any], by_id: dict[str, dict[str, Any]], users: dict[str, str]) -> set[str]:
    found: set[str] = set()
    seen: set[str] = set()
    stack = [root]
    while stack:
        group = stack.pop()
        group_id = str(group.get("id") or "")
        if group_id in seen:
            continue
        seen.add(group_id)
        for entry in group.get("members") or []:
            if not isinstance(entry, dict):
                continue
            member_id = str(entry.get("value") or "").strip()
            ref = str(entry.get("$ref") or "").lower()
            kind = str(entry.get("type") or "").upper()
            if kind == "GROUP" or _ref_is(ref, "groups"):
                child = by_id.get(member_id)
                if child is not None:
                    stack.append(child)
                continue
            if kind == "SERVICE_PRINCIPAL" or _ref_is(ref, "serviceprincipals"):
                continue
            email = users.get(member_id, "")
            if not email:
                display = str(entry.get("display") or "").strip().lower()
                email = display if "@" in display else ""
            if "@" in email:
                found.add(email)
    return found
