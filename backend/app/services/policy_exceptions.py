"""Global ABAC policy exceptions for principals that need unmasked / unfiltered access."""

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
    """Manage principals listed on ABAC policy EXCEPT clauses."""

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
        """Ordered unique principal names for ABAC EXCEPT clauses."""
        seen: list[str] = []
        for name in [*self.system_principal_names(), *self._configured_principal_names()]:
            if name and name not in seen:
                seen.append(name)
        return seen

    def system_principal_names(self) -> list[str]:
        ids = [self._workspace.app_client_id, self._workspace.admin_client_id]
        seen: list[str] = []
        for item in ids:
            if item and item not in seen:
                seen.append(item)
        return seen

    def apply(self, actor: CurrentUser) -> ApplyPolicyExceptionsResult:
        """Rebuild mask + row-filter packs so EXCEPT lists pick up the registry."""
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
        return [item.principal_name for item in self._storage.policy_exceptions.list_exceptions()]

    def _system_exceptions(self) -> list[PolicyException]:
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
        self._storage.audit.record(
            AuditEvent(
                event_id=str(uuid.uuid4()),
                event_type=event_type,
                actor=actor,
                payload=payload,
                created_at=datetime.now(UTC),
            )
        )
