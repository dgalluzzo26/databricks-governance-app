"""Give app-managed account groups access to this workspace.

``dac_*`` and ``dac_subscription_*`` groups are account groups, so their members cannot
open the workspace until the group is assigned to it. Assignment uses the workspace
permission-assignment API (``USER`` permission); entitlements are then added through
workspace SCIM, where an identity-federated account group keeps its account id.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_ASSIGNMENTS = "/api/2.0/preview/permissionassignments"
_WORKSPACE_GROUPS = "/api/2.0/preview/scim/v2/Groups"
_PATCH_SCHEMA = "urn:ietf:params:scim:api:messages:2.0:PatchOp"


class WorkspaceGroupAccess:
    """Assigns account groups to the workspace with a minimum set of entitlements.

    Args:
        api_client: Callable returning an API client with workspace admin rights
            (``factory.admin().api_client``).
    """

    def __init__(self, api_client: Any) -> None:
        self._api_client = api_client

    def assigned_principal_ids(self) -> set[str]:
        """Principal ids already assigned to the workspace."""
        payload = self._api_client().do("GET", _ASSIGNMENTS)
        items = payload.get("permission_assignments") if isinstance(payload, dict) else None
        return {
            str((item.get("principal") or {}).get("principal_id"))
            for item in items or []
            if (item.get("principal") or {}).get("principal_id") is not None
        }

    def group_entitlements(self, group_ids: set[str]) -> dict[str, set[str]]:
        """Current entitlements of the given workspace groups (missing ids are absent)."""
        out: dict[str, set[str]] = {}
        if not group_ids:
            return out
        payload = self._api_client().do(
            "GET", _WORKSPACE_GROUPS, query={"attributes": "id,entitlements", "count": 10000}
        )
        for group in (payload.get("Resources") if isinstance(payload, dict) else None) or []:
            gid = str(group.get("id") or "")
            if gid in group_ids:
                out[gid] = {str(e.get("value")) for e in group.get("entitlements") or [] if e.get("value")}
        return out

    def ensure(
        self,
        group_ids: dict[str, str],
        entitlements: list[str],
        errors: list[str],
        *,
        assigned: set[str] | None = None,
    ) -> int:
        """Assign each group to the workspace and add any missing entitlements.

        Args:
            group_ids: Group display name to account (and workspace) SCIM id.
            entitlements: Entitlements every group must hold; empty skips the step.
            errors: Failure messages are appended here.
            assigned: Pre-loaded assigned principal ids; loaded when None.

        Returns:
            Number of groups changed (assigned or given entitlements).
        """
        if not entitlements or not group_ids:
            return 0
        try:
            current = assigned if assigned is not None else self.assigned_principal_ids()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"list workspace assignments: {exc}")
            return 0
        changed = 0
        api = self._api_client()
        for name, gid in sorted(group_ids.items()):
            if gid in current:
                continue
            try:
                api.do("PUT", f"{_ASSIGNMENTS}/principals/{gid}", body={"permissions": ["USER"]})
                current.add(gid)
                changed += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"assign {name} to workspace: {exc}")
        try:
            held = self.group_entitlements(set(group_ids.values()))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"read group entitlements: {exc}")
            return changed
        wanted = set(entitlements)
        for name, gid in sorted(group_ids.items()):
            if gid not in current:
                continue
            missing = sorted(wanted - held.get(gid, set()))
            if not missing:
                continue
            try:
                api.do(
                    "PATCH",
                    f"{_WORKSPACE_GROUPS}/{gid}",
                    body={
                        "schemas": [_PATCH_SCHEMA],
                        "Operations": [
                            {"op": "add", "path": "entitlements", "value": [{"value": e} for e in missing]}
                        ],
                    },
                )
                changed += 1
            except Exception as exc:  # noqa: BLE001
                errors.append(f"add entitlements to {name}: {exc}")
        if changed:
            logger.info("Workspace access: %d managed group(s) assigned or entitled", changed)
        return changed
