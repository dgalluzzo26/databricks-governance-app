"""Kinds of Databricks principals that can hold attributes, approve, or be exempted."""

from __future__ import annotations

from enum import StrEnum


class PrincipalKind(StrEnum):
    """Type of a Databricks identity.

    Attributes:
        USER: An individual workspace user, identified by user name / email.
        GROUP: A workspace or account group, identified by group display name.
        SERVICE_PRINCIPAL: A service principal, identified by its application (client) ID.
    """

    USER = "USER"
    GROUP = "GROUP"
    SERVICE_PRINCIPAL = "SERVICE_PRINCIPAL"
