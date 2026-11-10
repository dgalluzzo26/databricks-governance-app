from __future__ import annotations

from typing import Any

import pytest

from backend.app.core.errors import InvalidRequestError
from backend.app.models.identity_attribute import (
    AssignAttributeRequest,
    GovernedTagSummary,
    UpsertAttributeDefinitionRequest,
    ValueSource,
)
from backend.app.models.principal import PrincipalKind
from backend.app.models.row_filter import AttachRowFilterRequest, UpsertRowFilterDimensionRequest
from backend.app.services.identity_attributes import IdentityAttributeService
from backend.app.services.row_filters import RowFilterService, build_policy_sql
from backend.app.services.value_seed import validate_seed_sql
from backend.tests.test_identity_attributes import FakeGovernedTags, _admin


class FakeSql:
    configured = True

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.column_tags: list[dict[str, Any]] = []
        self.seed_rows: list[dict[str, Any]] = [{"abbreviation": "NY"}, {"abbreviation": "CA"}]

    def query(self, statement: str, parameters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if "column_tags" in statement:
            rows = self.column_tags
            if "table_name = " in statement:
                rows = [r for r in rows if f"table_name = '{r['table_name']}'" in statement]
            return rows
        if "abac_policy_definitions" in statement:
            return []
        if "AS seed" in statement:
            return self.seed_rows
        return []

    def execute(self, statement: str, parameters: dict[str, Any] | None = None) -> None:
        self.executed.append(statement)


class RowFilterTags(FakeGovernedTags):
    def __init__(self) -> None:
        super().__init__()
        self.tags["row_filter"] = GovernedTagSummary(
            tag_key="row_filter", values=["geo_region"], value_count=1
        )

    def ensure_values(self, tag_key: str, values: list[str]) -> GovernedTagSummary:
        tag = self.tags[tag_key]
        merged = [*tag.values, *[v for v in values if v not in tag.values]]
        self.tags[tag_key] = tag.model_copy(update={"values": merged, "value_count": len(merged)})
        return self.tags[tag_key]


class FakeWorkspace:
    app_client_id = "app-sp-id"
    admin_client_id = "admin-sp-id"


def _services(container) -> tuple[IdentityAttributeService, RowFilterService, FakeSql]:  # noqa: ANN001
    sql = FakeSql()
    tags = RowFilterTags()
    identity: IdentityAttributeService = container.identity_attributes
    identity._governed_tags = tags  # type: ignore[assignment]
    identity._sql = sql  # type: ignore[assignment]
    service = RowFilterService(
        container.config,
        FakeWorkspace(),
        sql,
        container.storage,
        tags,
        identity,  # type: ignore[arg-type]
    )
    return identity, service, sql


def test_seed_sql_must_be_single_select() -> None:
    assert validate_seed_sql("SELECT DISTINCT a FROM t;") == "SELECT DISTINCT a FROM t"
    assert validate_seed_sql("-- note\nWITH x AS (SELECT 1) SELECT * FROM x")
    with pytest.raises(InvalidRequestError, match="single statement"):
        validate_seed_sql("SELECT 1; DROP TABLE t")
    with pytest.raises(InvalidRequestError, match="SELECT"):
        validate_seed_sql("DELETE FROM t")


def test_free_form_definition_always_allows_star(container) -> None:  # noqa: ANN001
    identity, _, _ = _services(container)
    actor = _admin()
    definition = identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="region", value_source=ValueSource.FREE_FORM, allowed_values=["East", "West"]
        ),
        actor,  # type: ignore[arg-type]
    )
    assert definition.allowed_values == ["*", "East", "West"]
    for value in ["*", "east"]:
        saved = identity.assign(
            AssignAttributeRequest(
                principal_name="analyst@example.com",
                principal_type=PrincipalKind.USER,
                attribute_key="region",
                attribute_value=value,
            ),
            actor,  # type: ignore[arg-type]
        )
        assert saved.attribute_value in {"*", "East"}
    with pytest.raises(InvalidRequestError, match="not allowed"):
        identity.assign(
            AssignAttributeRequest(
                principal_name="analyst@example.com",
                principal_type=PrincipalKind.USER,
                attribute_key="region",
                attribute_value="North",
            ),
            actor,  # type: ignore[arg-type]
        )


def test_sql_definition_seeds_values(container) -> None:  # noqa: ANN001
    identity, _, _ = _services(container)
    definition = identity.upsert_definition(
        UpsertAttributeDefinitionRequest(
            key="state", value_source=ValueSource.SQL, value_sql="SELECT DISTINCT abbreviation FROM t"
        ),
        _admin(),  # type: ignore[arg-type]
    )
    assert definition.value_source == ValueSource.SQL
    assert definition.allowed_values == ["*", "CA", "NY"]


def test_empty_seed_result_does_not_wipe_values(container) -> None:  # noqa: ANN001
    identity, _, sql = _services(container)
    request = UpsertAttributeDefinitionRequest(
        key="state", value_source=ValueSource.SQL, value_sql="SELECT DISTINCT abbreviation FROM t"
    )
    identity.upsert_definition(request, _admin())  # type: ignore[arg-type]
    sql.seed_rows = []
    with pytest.raises(InvalidRequestError, match="no values"):
        identity.refresh_definition("state", _admin())  # type: ignore[arg-type]
    assert identity.get_definition("state").allowed_values == ["*", "CA", "NY"]


def test_governed_tag_definition_still_requires_tags() -> None:
    with pytest.raises(ValueError, match="governed tag key"):
        UpsertAttributeDefinitionRequest(key="x")
    with pytest.raises(ValueError, match="value_sql"):
        UpsertAttributeDefinitionRequest(key="x", value_source=ValueSource.SQL)


def test_upsert_dimension_regenerates_policy(container) -> None:  # noqa: ANN001
    _, service, sql = _services(container)
    dim = service.upsert_dimension(
        UpsertRowFilterDimensionRequest(
            key="State", value_source=ValueSource.SQL, value_sql="SELECT abbreviation FROM t"
        ),
        _admin(),  # type: ignore[arg-type]
    )
    assert dim.key == "state"
    assert dim.allowed_value_count == 2
    policy = next(s for s in sql.executed if "CREATE OR REPLACE POLICY" in s)
    assert "has_tag_value('row_filter', 'state')" in policy
    assert "EXCEPT `app-sp-id`, `admin-sp-id`" in policy
    assert "geo_region" not in policy
    assert any("CREATE OR REPLACE FUNCTION" in s for s in sql.executed)


def test_policy_sql_matches_every_dimension() -> None:
    text = build_policy_sql(
        "rf_by_dimension", "`c`.`s`.`has_row_access`", "row_filter", ["state", "region"], []
    )
    assert "has_tag_value('row_filter', 'state')\n  OR has_tag_value('row_filter', 'region')" in text
    assert "EXCEPT" not in text
    assert "USING COLUMNS (c, get_column_tag_value(c, 'row_filter'))" in text


def test_attach_enforces_one_dimension_per_table(container) -> None:  # noqa: ANN001
    _, service, sql = _services(container)
    actor = _admin()
    for key in ["state", "region"]:
        service.upsert_dimension(
            UpsertRowFilterDimensionRequest(
                key=key, value_source=ValueSource.FREE_FORM, allowed_values=["A"]
            ),
            actor,  # type: ignore[arg-type]
        )
    service.attach(
        AttachRowFilterRequest(
            table_full_name="cat.sch.us_states", column_name="abbreviation", dimension="state"
        ),
        actor,  # type: ignore[arg-type]
    )
    assert "SET TAGS ('row_filter' = 'state')" in sql.executed[-1]

    sql.column_tags = [
        {
            "catalog_name": "cat",
            "schema_name": "sch",
            "table_name": "us_states",
            "column_name": "abbreviation",
            "tag_value": "state",
        }
    ]
    with pytest.raises(InvalidRequestError, match="one dimension per table"):
        service.attach(
            AttachRowFilterRequest(
                table_full_name="cat.sch.us_states", column_name="region_code", dimension="region"
            ),
            actor,  # type: ignore[arg-type]
        )
    with pytest.raises(InvalidRequestError, match="three-part"):
        service.attach(
            AttachRowFilterRequest(table_full_name="sch.us_states", column_name="x", dimension="state"),
            actor,  # type: ignore[arg-type]
        )
    with pytest.raises(InvalidRequestError, match="Detach"):
        service.delete_dimension("state", actor)  # type: ignore[arg-type]
