from __future__ import annotations

from enum import StrEnum


class PrincipalKind(StrEnum):
    USER = "USER"
    GROUP = "GROUP"
    SERVICE_PRINCIPAL = "SERVICE_PRINCIPAL"
