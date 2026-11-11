from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.app.core.errors import InvalidRequestError
from backend.app.models.identity_attribute import GovernedTagSummary
from backend.app.models.mask_rule import (
    MaskHandler,
    MaskHandlerValidateRequest,
    MaskRuleKind,
    UpsertMaskRuleRequest,
)
from backend.app.services.mask_handler_sql import sanitize_expression
from backend.app.services.mask_rules import MaskRuleService
from backend.tests.conftest import headers_for
from backend.tests.test_identity_attributes import FakeGovernedTags, _admin


class MaskRuleTags(FakeGovernedTags):
    def __init__(self) -> None:
        super().__init__()
        self.tags["mask_rule"] = GovernedTagSummary(
            tag_key="mask_rule", values=["full", "email"], value_count=2
        )

    def ensure_values(self, tag_key: str, values: list[str]) -> GovernedTagSummary:
        tag = self.tags.get(tag_key) or GovernedTagSummary(tag_key=tag_key, values=[], value_count=0)
        merged = [*tag.values, *[v for v in values if v not in tag.values]]
        self.tags[tag_key] = tag.model_copy(update={"values": merged, "value_count": len(merged)})
        return self.tags[tag_key]


class FakeSql:
    configured = True

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.policy_rows = [
            {"match_columns": "has_tag('sensitivity_category') AND has_tag('sensitivity')"}
        ]
        self.apply_body = "apply_mask_rule full email"
        self.validate_rows: list[dict[str, Any]] = [
            {"input": "alice@gmail.com", "output": "***@gmail.com"},
            {"input": "555-123-4567", "output": "***"},
            {"input": "plaintext", "output": "***"},
            {"input": "ABCDEFGH", "output": "***"},
        ]

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if "abac_policy_definitions" in statement:
            return self.policy_rows
        if "routine_name = 'apply_mask_rule'" in statement or "routine_name = :name" in statement:
            return [{"routine_definition": self.apply_body}]
        if "routines" in statement:
            return [{"routine_definition": "apply_mask_rule(value, mask_rule)"}]
        if "FROM VALUES" in statement or "AS output" in statement:
            return self.validate_rows
        return []

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
        self.executed.append(statement)
        if "CREATE OR REPLACE FUNCTION" in statement and "apply_mask_rule" in statement:
            self.apply_body = statement


def _service(container) -> tuple[MaskRuleService, FakeSql]:  # noqa: ANN001
    sql = FakeSql()
    tags = MaskRuleTags()
    service = MaskRuleService(container.config, sql, container.storage, tags)
    return service, sql


def test_sanitize_rejects_dangerous_sql() -> None:
    with pytest.raises(InvalidRequestError, match="single statement"):
        sanitize_expression("v; DROP TABLE t")
    with pytest.raises(InvalidRequestError, match="not allowed"):
        sanitize_expression("secret_col")
    assert sanitize_expression("concat('ID-', sha2(lower(trim(v)), 256))")


def test_seed_defaults_and_unknown_falls_back_via_full(container) -> None:  # noqa: ANN001
    service, sql = _service(container)
    actor = _admin()
    seeded = service.seed_defaults(actor)  # type: ignore[arg-type]
    keys = {r.rule_key for r in seeded.rules}
    assert {"full", "email", "phone", "joinable", "keep_last_4"} <= keys
    keep = next(r for r in seeded.rules if r.rule_key == "keep_last_4")
    assert keep.handler == "keep_last_n"
    assert keep.kind == MaskRuleKind.BUILTIN
    assert keep.params == {"n": 4}
    assert any("CREATE OR REPLACE FUNCTION" in s and "apply_mask_rule" in s for s in sql.executed)
    status = service.pack_status()
    assert status.config.joinable_salt_configured is True
    assert status.config.in_sync is True


def test_upsert_builtin_rule_ensures_tag_and_sync(container) -> None:  # noqa: ANN001
    service, sql = _service(container)
    actor = _admin()
    service.seed_defaults(actor)  # type: ignore[arg-type]
    sql.executed.clear()
    saved = service.upsert_rule(
        UpsertMaskRuleRequest(
            rule_key="Employee_Email",
            handler=MaskHandler.EMAIL,
            description="Employee emails",
        ),
        actor,  # type: ignore[arg-type]
    )
    assert saved.rule_key == "employee_email"
    assert saved.kind == MaskRuleKind.BUILTIN
    assert "employee_email" in service._governed_tags.get_tag("mask_rule").values  # type: ignore[attr-defined]
    assert any("INSERT INTO" in s and "employee_email" in s for s in sql.executed)


def test_custom_requires_validation_token(container) -> None:  # noqa: ANN001
    service, _ = _service(container)
    with pytest.raises(ValueError, match="validation_token"):
        UpsertMaskRuleRequest(
            rule_key="id_hash",
            kind=MaskRuleKind.CUSTOM,
            expression="concat('ID-', sha2(lower(v), 256))",
        )


def test_validate_and_save_custom_regenerates_udf(container) -> None:  # noqa: ANN001
    service, sql = _service(container)
    actor = _admin()
    service.seed_defaults(actor)  # type: ignore[arg-type]
    sql.executed.clear()
    expr = "concat('ID-', sha2(lower(trim(v)), 256))"
    result = service.validate_handler(
        MaskHandlerValidateRequest(expression=expr), actor  # type: ignore[arg-type]
    )
    assert result.ok is True
    assert result.validation_token
    saved = service.upsert_rule(
        UpsertMaskRuleRequest(
            rule_key="id_hash",
            kind=MaskRuleKind.CUSTOM,
            expression=expr,
            validation_token=result.validation_token,
            description="Prefixed joinable hash",
        ),
        actor,  # type: ignore[arg-type]
    )
    assert saved.kind == MaskRuleKind.CUSTOM
    assert saved.handler == "id_hash"
    assert any("id_hash" in s and "CREATE OR REPLACE FUNCTION" in s for s in sql.executed)



def test_draft_handler_mocked_llm(container) -> None:  # noqa: ANN001
    from types import SimpleNamespace

    from backend.app.models.mask_rule import MaskHandlerDraftRequest

    class FakeServing:
        def query(self, **kwargs):  # noqa: ANN003
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content='{"expression":"concat(\'ID-\', sha2(lower(trim(v)), 256))",'
                            '"explanation":"Prefixed hash","sample_inputs":["alice@x.com","CA"]}'
                        )
                    )
                ]
            )

    class FakeWorkspace:
        def admin(self):
            return SimpleNamespace(serving_endpoints=FakeServing())

    service, _ = _service(container)
    service._workspace = FakeWorkspace()
    draft = service.draft_handler(
        MaskHandlerDraftRequest(prompt="joinable-style hash with ID- prefix"),
        _admin(),  # type: ignore[arg-type]
    )
    assert draft.expression.startswith("concat(")
    assert "ID-" in draft.expression
    assert draft.sample_inputs


def test_cannot_delete_full(container) -> None:  # noqa: ANN001
    service, _ = _service(container)
    actor = _admin()
    service.seed_defaults(actor)  # type: ignore[arg-type]
    with pytest.raises(InvalidRequestError, match="protected"):
        service.delete_rule("full", actor)  # type: ignore[arg-type]


def test_keep_last_n_params_validated() -> None:
    with pytest.raises(ValueError, match="params.n"):
        UpsertMaskRuleRequest(rule_key="x", handler=MaskHandler.KEEP_LAST_N, params={"n": 0})


def test_api_list_and_ensure(client: TestClient, container) -> None:  # noqa: ANN001
    service, _ = _service(container)
    container.mask_rules = service
    headers = headers_for("admin@example.com")
    empty = client.get("/api/mask-rules", headers=headers)
    assert empty.status_code == 200
    assert empty.json()["total"] == 0

    seeded = client.post("/api/mask-rules/seed", headers=headers)
    assert seeded.status_code == 200
    assert seeded.json()["total"] >= 5

    pack = client.post("/api/mask-rules/pack/ensure", headers=headers)
    assert pack.status_code == 200
    body = pack.json()
    assert body["config"]["joinableSaltConfigured"] is True
    assert body["config"]["inSync"] is True

    bad = client.post(
        "/api/mask-rules/handlers/validate",
        headers=headers,
        json={"expression": "v; DROP TABLE t"},
    )
    assert bad.status_code in {400, 422}
    assert "single statement" in bad.text or "invalid" in bad.text.lower()


def test_ensure_pack_owns_geo_policies_with_enrollment_scope(container) -> None:  # noqa: ANN001
    service, sql = _service(container)
    service.ensure_pack(_admin())  # type: ignore[arg-type]

    policies = [s for s in sql.executed if "CREATE OR REPLACE POLICY" in s]
    names = {
        "mask_sensitivity_variant",
        "mask_sensitivity_geometry",
        "mask_sensitivity_geography",
    }
    assert {n for n in names if any(f"POLICY {n} " in s for s in policies)} == names
    for statement in policies:
        assert "has_tag_value('data_access_controlled', 'true')" in statement
        assert "ON METASTORE" in statement

    geometry = next(s for s in policies if "POLICY mask_sensitivity_geometry " in s)
    assert "mask_geometry_4326" in geometry
    assert "has_tag_value('data_type', 'geometry')" in geometry
    geography = next(s for s in policies if "POLICY mask_sensitivity_geography " in s)
    assert "mask_geography_4326" in geography
    assert "has_tag_value('data_type', 'geography')" in geography

    functions = [s for s in sql.executed if "mask_geo" in s and "CREATE OR REPLACE FUNCTION" in s]
    assert len(functions) == 2
    assert any("RETURNS GEOMETRY(4326)" in s for s in functions)
    assert any("RETURNS GEOGRAPHY(4326)" in s for s in functions)
    assert not any("g GEOMETRY," in s or "g GEOGRAPHY," in s for s in functions)




def test_ensure_pack_creates_auth_functions_before_policies(container) -> None:  # noqa: ANN001
    service, sql = _service(container)
    service.ensure_pack(_admin())  # type: ignore[arg-type]

    def first(predicate: str) -> int:
        return next(i for i, s in enumerate(sql.executed) if predicate in s)

    has_sensitivity = next(
        i for i, s in enumerate(sql.executed) if "`has_sensitivity`(required_path" in s
    )
    mask_variant = next(i for i, s in enumerate(sql.executed) if "`mask_variant`(" in s)
    variant_policy = first("POLICY mask_sensitivity_variant ")
    geo_policy = first("POLICY mask_sensitivity_geometry ")
    assert has_sensitivity < mask_variant < variant_policy
    assert mask_variant < geo_policy
