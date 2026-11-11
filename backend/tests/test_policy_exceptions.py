from __future__ import annotations

from backend.app.models.policy_exception import UpsertPolicyExceptionRequest
from backend.app.models.principal import PrincipalKind
from backend.app.services.policy_exceptions import PolicyExceptionService
from backend.tests.test_identity_attributes import _admin


class _FakeWorkspace:
    app_client_id = "app-sp"
    admin_client_id = "admin-sp"


class _FakePack:
    def __init__(self) -> None:
        self.calls = 0

    def ensure_pack(self, actor) -> dict:  # noqa: ANN001
        self.calls += 1
        return {}


def test_except_principals_include_system_and_configured(container) -> None:  # noqa: ANN001
    mask = _FakePack()
    row = _FakePack()
    service = PolicyExceptionService(
        container.storage, _FakeWorkspace(), mask_rules=mask, row_filters=row
    )
    actor = _admin()
    service.upsert(
        UpsertPolicyExceptionRequest(
            principal_name="data-services",
            principal_type=PrincipalKind.GROUP,
            note="DSP break-glass",
        ),
        actor,  # type: ignore[arg-type]
    )
    principals = service.except_principals()
    assert principals == ["app-sp", "admin-sp", "data-services"]
    listed = service.list_exceptions()
    assert listed.total == 3
    assert any(item.system and item.principal_name == "app-sp" for item in listed.exceptions)
    assert mask.calls >= 1
    assert row.calls >= 1


def test_build_policy_sql_includes_exceptions() -> None:
    from backend.app.services.row_filters import build_policy_sql

    sql = build_policy_sql(
        "rf_by_dimension",
        "cat.sch.has_row_access",
        "row_filter",
        ["state"],
        ["app-sp", "data-services"],
    )
    assert "EXCEPT `app-sp`, `data-services`" in sql
