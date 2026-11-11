"""Global ABAC policy exceptions (principals exempt from masks and row filters)."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator

from backend.app.models.base import ApiModel
from backend.app.models.principal import PrincipalKind


class PolicyException(ApiModel):
    """Principal listed in ``EXCEPT`` of the app-managed mask and row-filter policies.

    Attributes:
        exception_id: Unique identifier; ``system:<name>`` for built-in entries.
        principal_name: User name, group name or service principal application ID.
        principal_type: Kind of principal.
        note: Optional admin note explaining why the principal is exempt.
        created_by: User who added the exception; None for built-in entries.
        created_at: When the exception was added (request time for built-in entries).
        system: True for built-in exceptions (the app and admin service principals) that are
            always applied and cannot be removed.
    """

    exception_id: str
    principal_name: str
    principal_type: PrincipalKind
    note: str | None = None
    created_by: str | None = None
    created_at: datetime
    system: bool = False


class UpsertPolicyExceptionRequest(ApiModel):
    """Request body to add or update a policy exception.

    Attributes:
        principal_name: Principal to exempt; surrounding whitespace is stripped, 1-256 chars.
        principal_type: Kind of principal.
        note: Optional note up to 1000 chars; blank notes are stored as None.
    """

    principal_name: str = Field(min_length=1, max_length=256)
    principal_type: PrincipalKind
    note: str | None = Field(default=None, max_length=1000)

    @field_validator("principal_name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        """Strip whitespace from the principal name and reject blank names.

        Args:
            value: Raw principal name.

        Returns:
            The stripped principal name.

        Raises:
            ValueError: If the name is empty after stripping.
        """
        text = value.strip()
        if not text:
            raise ValueError("Principal name cannot be empty")
        return text

    @field_validator("note")
    @classmethod
    def _strip_note(cls, value: str | None) -> str | None:
        """Strip whitespace from the note, normalizing blank notes to None.

        Args:
            value: Raw note, or None.

        Returns:
            The stripped note, or None when absent or blank.
        """
        if value is None:
            return None
        text = value.strip()
        return text or None


class PolicyExceptionList(ApiModel):
    """All policy exceptions, including built-in system entries.

    Attributes:
        exceptions: Configured and system exceptions.
        total: Number of entries in ``exceptions``.
        system_principals: Principal names of the built-in system exceptions.
    """

    exceptions: list[PolicyException]
    total: int
    system_principals: list[str] = []


class ApplyPolicyExceptionsResult(ApiModel):
    """Outcome of rebuilding the app-managed ABAC policy packs with the exception list.

    Attributes:
        except_principals: Ordered, de-duplicated ``EXCEPT`` principals (system first).
        mask_pack_synced: True when the mask-rule pack was rebuilt; False when that service
            is not wired.
        row_filter_pack_synced: True when the row-filter pack was rebuilt; False when that
            service is not wired.
    """

    except_principals: list[str]
    mask_pack_synced: bool = False
    row_filter_pack_synced: bool = False
