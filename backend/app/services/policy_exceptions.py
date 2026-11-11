"""Global ABAC policy exceptions for principals that need unmasked / unfiltered access.

Maintains the registry of principals placed on the EXCEPT clause of the app-managed metastore
column-mask and row-filter policies. The app and admin service principals are always exempt
(built-in, not deletable) so the app can read governed data, e.g. to run seed queries.
Changes rebuild both policy packs so the EXCEPT lists take effect immediately.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from backend.app.clients.workspace import WorkspaceClientFactory
from backend.app.core.errors import InvalidRequestError, NotFoundError
from backend.app.models.audit import AuditEvent
from backend.app.models.policy_exception import (
    ApplyPolicyExceptionsResult,
    PolicyException,
    PolicyExceptionList,
    UpsertPolicyExceptionRequest,
)
from backend.app.models.principal import PrincipalKind
from backend.app.models.user import CurrentUser
from backend.app.repositories.base import StorageProvider


class PolicyExceptionService:
    """Manage principals listed on ABAC policy EXCEPT clauses.

    Args:
        storage: Primary storage for configured exceptions and audit events.
        workspace: Workspace client factory providing the built-in SP ids.
        mask_rules: Optional ``MaskRuleService`` whose pack is rebuilt on changes.
        row_filters: Optional ``RowFilterService`` whose pack is rebuilt on changes.
    """

    def __init__(
        self,
        storage: StorageProvider,
        workspace: WorkspaceClientFactory,
        *,
        mask_rules=None,
        row_filters=None,
    ) -> None:
        self._storage = storage
        self._workspace = workspace
        self._mask_rules = mask_rules
        self._row_filters = row_filters

    def list_exceptions(self) -> PolicyExceptionList:
        """List built-in system exceptions followed by configured ones.

        Configured entries that duplicate a system principal are hidden.

        Returns:
            All exceptions, their count, and the system principal names.
        """
        configured = self._storage.policy_exceptions.list_exceptions()
        system = self._system_exceptions()
        # Hide configured duplicates of system SPs so the UI doesn't double-count.
        system_names = {item.principal_name.lower() for item in system}
        configured = [
            item for item in configured if item.principal_name.lower() not in system_names
        ]
        items = [*system, *configured]
        return PolicyExceptionList(
            exceptions=items,
            total=len(items),
            system_principals=[item.principal_name for item in system],
        )

    def upsert(self, body: UpsertPolicyExceptionRequest, actor: CurrentUser) -> PolicyException:
        """Add or update a configured exception and rebuild the policy packs.

        Records a ``policy_exception.upserted`` audit event, then calls ``apply`` which
        recreates the mask and row-filter policies in Unity Catalog.

        Args:
            body: Principal name, type, and note.
            actor: The admin making the change.

        Returns:
            The saved exception.

        Raises:
            InvalidRequestError: If the principal type is not USER, GROUP, or
                SERVICE_PRINCIPAL, or the principal is already a built-in exception.
            UpstreamError: If rebuilding a policy pack fails (the exception stays saved).
        """
        if body.principal_type not in {
            PrincipalKind.USER,
            PrincipalKind.GROUP,
            PrincipalKind.SERVICE_PRINCIPAL,
        }:
            raise InvalidRequestError("Exceptions must be USER, GROUP, or SERVICE_PRINCIPAL")
        if body.principal_name.lower() in {
            p.lower() for p in self.system_principal_names()
        }:
            raise InvalidRequestError(
                f"'{body.principal_name}' is already a built-in system exception"
            )
        saved = self._storage.policy_exceptions.upsert_exception(
            PolicyException(
                exception_id=str(uuid.uuid4()),
                principal_name=body.principal_name,
                principal_type=body.principal_type,
                note=body.note,
                created_by=actor.user_name,
                created_at=datetime.now(UTC),
                system=False,
            )
        )
        self._audit(
            "policy_exception.upserted",
            actor.user_name,
            {
                "exception_id": saved.exception_id,
                "principal_name": saved.principal_name,
                "principal_type": saved.principal_type.value,
            },
        )
        # Rebuild packs so EXCEPT lists take effect immediately.
        self.apply(actor)
        return saved

    def delete(self, exception_id: str, actor: CurrentUser) -> None:
        """Remove a configured exception and rebuild the policy packs.

        Records a ``policy_exception.deleted`` audit event, then calls ``apply``.

        Args:
            exception_id: Identifier of the configured exception.
            actor: The admin making the change.

        Raises:
            InvalidRequestError: If the id refers to a built-in system exception.
            NotFoundError: If no configured exception has that id.
            UpstreamError: If rebuilding a policy pack fails (the deletion stays saved).
        """
        if exception_id.startswith("system:"):
            raise InvalidRequestError("Built-in system exceptions cannot be deleted")
        deleted = self._storage.policy_exceptions.delete_exception(exception_id)
        if not deleted:
            raise NotFoundError(f"Exception {exception_id} was not found")
        self._audit(
            "policy_exception.deleted",
            actor.user_name,
            {"exception_id": exception_id},
        )
        self.apply(actor)

    def except_principals(self) -> list[str]:
        """Ordered unique principal names for ABAC EXCEPT clauses.

        Returns:
            System SP ids first, then configured principals, without duplicates.
        """
        seen: list[str] = []
        for name in [*self.system_principal_names(), *self._configured_principal_names()]:
            if name and name not in seen:
                seen.append(name)
        return seen

    def system_principal_names(self) -> list[str]:
        """Return the configured app and admin SP client ids, de-duplicated.

        Returns:
            The built-in exempt principals (empty entries skipped).
        """
        ids = [self._workspace.app_client_id, self._workspace.admin_client_id]
        seen: list[str] = []
        for item in ids:
            if item and item not in seen:
                seen.append(item)
        return seen

    def apply(self, actor: CurrentUser) -> ApplyPolicyExceptionsResult:
        """Rebuild mask + row-filter packs so EXCEPT lists pick up the registry.

        Runs each wired service's ``ensure_pack`` (recreating the metastore policies in
        Unity Catalog) and records a ``policy_exception.applied`` audit event.

        Args:
            actor: The admin applying the change.

        Returns:
            The EXCEPT principals and which packs were rebuilt.

        Raises:
            UpstreamError: If a pack rebuild fails.
        """
        principals = self.except_principals()
        mask_synced = False
        row_synced = False
        if self._mask_rules is not None:
            self._mask_rules.ensure_pack(actor)
            mask_synced = True
        if self._row_filters is not None:
            self._row_filters.ensure_pack(actor)
            row_synced = True
        self._audit(
            "policy_exception.applied",
            actor.user_name,
            {
                "except_principals": principals,
                "mask_pack_synced": mask_synced,
                "row_filter_pack_synced": row_synced,
            },
        )
        return ApplyPolicyExceptionsResult(
            except_principals=principals,
            mask_pack_synced=mask_synced,
            row_filter_pack_synced=row_synced,
        )

    def _configured_principal_names(self) -> list[str]:
        """Return principal names of the stored (non-system) exceptions."""
        return [item.principal_name for item in self._storage.policy_exceptions.list_exceptions()]

    def _system_exceptions(self) -> list[PolicyException]:
        """Build synthetic, non-deletable exception entries for the app and admin SPs."""
        now = datetime.now(UTC)
        items: list[PolicyException] = []
        mapping = [
            (self._workspace.app_client_id, "App service principal (built-in)"),
            (self._workspace.admin_client_id, "Admin service principal (built-in)"),
        ]
        for name, note in mapping:
            if not name:
                continue
            items.append(
                PolicyException(
                    exception_id=f"system:{name}",
                    principal_name=name,
                    principal_type=PrincipalKind.SERVICE_PRINCIPAL,
                    note=note,
                    created_by=None,
                    created_at=now,
                    system=True,
                )
            )
        return items

    def _audit(self, event_type: str, actor: str, payload: dict) -> None:
        """Record an audit event in storage."""
        self._storage.audit.record(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=event_type,
                actor=actor,
                payload=payload,
                created_at=datetime.now(UTC),
            )
        )
