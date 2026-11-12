from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from databricks.sdk.errors import NotFound

from backend.app.core.errors import NotFoundError, UpstreamError
from backend.app.services import policies as policies_module
from backend.app.services.policies import (
    PolicyService,
    _normalize_policy_type,
    _normalize_securable_type,
    _parse_json_list,
    _parse_string_list,
    _parse_tags,
    _scope_kind,
)


class FakeSql:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.queries: list[str] = []

    def query(self, statement: str, parameters=None):  # noqa: ANN001
        self.queries.append(statement)
        if "abac_policy_definitions" in statement:
            return self.rows
        return []


class FakeCache:
    def get_or_load(self, key, loader):  # noqa: ANN001
        return loader()


class FakeMetastores:
    def current(self):
        return type("A", (), {"metastore_id": "m1"})()

    def get(self, _metastore_id: str):
        return type("M", (), {"name": "metastore_aws_us_east_1"})()


class FakePoliciesApi:
    def get_policy(self, on_securable_type: str, on_securable_fullname: str, name: str):
        return type(
            "P",
            (),
            {
                "comment": "demo",
                "created_at": 1,
                "updated_at": 2,
                "updated_by": "admin",
                "to_principals": ["account users"],
                "except_principals": [],
                "when_condition": "has_tag('ai.model_family')",
                "governed_tags": [type("T", (), {"tag_key": "ai.model_family", "tag_value": None})()],
                "match_columns": [],
                "column_mask": None,
                "row_filter": None,
                "grant": type("G", (), {"privileges": ["EXECUTE"]})(),
                "deny": None,
            },
        )()


class FakeFactory:
    def admin(self):
        return type("C", (), {"metastores": FakeMetastores(), "policies": FakePoliciesApi()})()


def test_parsers() -> None:
    assert _normalize_policy_type("POLICY_TYPE_GRANT") == "GRANT"
    assert _parse_string_list('["account users"]') == ["account users"]
    assert _parse_tags('[{"tag_key":"a","tag_value":null}]')[0].key == "a"


def test_list_splits_metastore_and_catalog() -> None:
    rows = [
        {
            "policy_id": "1",
            "policy_name": "meta_mask",
            "policy_type": "COLUMN_MASK",
            "catalog_name": None,
            "schema_name": None,
            "securable_name": None,
            "on_securable_type": "METASTORE",
            "to_principals": '["account users"]',
            "except_principals": "[]",
            "for_securable_type": "TABLE",
            "privileges": None,
            "when_condition": "has_tag('dga_domain')",
            "match_columns": None,
            "governed_tags": "[]",
            "created_by": "admin",
        },
        {
            "policy_id": "2",
            "policy_name": "approved_models",
            "policy_type": "GRANT",
            "catalog_name": "system",
            "schema_name": "ai",
            "securable_name": None,
            "on_securable_type": "SCHEMA",
            "to_principals": '["account users"]',
            "except_principals": "[]",
            "for_securable_type": "MODEL",
            "privileges": '["EXECUTE"]',
            "when_condition": "has_tag('ai.model_family')",
            "match_columns": None,
            "governed_tags": '[{"tag_key":"ai.model_family","tag_value":null}]',
            "created_by": "admin",
        },
    ]
    service = PolicyService(FakeFactory(), FakeSql(rows), FakeCache())
    result = service.list_policies()
    assert result.metastore_name == "metastore_aws_us_east_1"
    assert len(result.metastore) == 1
    assert result.metastore[0].scope_kind == "metastore"
    assert result.metastore[0].on_securable_fullname == "metastore_aws_us_east_1"
    assert len(result.catalog) == 1
    assert result.catalog[0].scope_kind == "catalog"
    assert result.catalog[0].on_securable_fullname == "system.ai"
    assert result.total == 2


def test_list_scope_filter() -> None:
    rows = [
        {
            "policy_id": "1",
            "policy_name": "meta_mask",
            "policy_type": "COLUMN_MASK",
            "catalog_name": None,
            "schema_name": None,
            "securable_name": None,
            "on_securable_type": "METASTORE",
            "to_principals": "[]",
            "except_principals": "[]",
            "for_securable_type": "TABLE",
            "privileges": None,
            "when_condition": None,
            "match_columns": None,
            "governed_tags": "[]",
            "created_by": None,
        },
        {
            "policy_id": "2",
            "policy_name": "cat_mask",
            "policy_type": "COLUMN_MASK",
            "catalog_name": "finance",
            "schema_name": None,
            "securable_name": None,
            "on_securable_type": "CATALOG",
            "to_principals": "[]",
            "except_principals": "[]",
            "for_securable_type": "TABLE",
            "privileges": None,
            "when_condition": None,
            "match_columns": None,
            "governed_tags": "[]",
            "created_by": None,
        },
    ]
    service = PolicyService(FakeFactory(), FakeSql(rows), FakeCache())
    only_meta = service.list_policies(scope="metastore")
    assert [p.name for p in only_meta.metastore] == ["meta_mask"]
    assert only_meta.catalog == []
    only_cat = service.list_policies(scope="catalog", catalog="finance")
    assert only_cat.metastore == []
    assert [p.name for p in only_cat.catalog] == ["cat_mask"]


def test_get_policy_detail() -> None:
    rows = [
        {
            "policy_id": "abc",
            "policy_name": "approved_models",
            "policy_type": "GRANT",
            "catalog_name": "system",
            "schema_name": "ai",
            "securable_name": None,
            "on_securable_type": "SCHEMA",
            "to_principals": '["account users"]',
            "except_principals": "[]",
            "for_securable_type": "MODEL",
            "privileges": '["EXECUTE"]',
            "when_condition": "has_tag('ai.model_family')",
            "match_columns": None,
            "governed_tags": '[{"tag_key":"ai.model_family","tag_value":null}]',
            "created_by": "admin",
        }
    ]
    service = PolicyService(FakeFactory(), FakeSql(rows), FakeCache())
    detail = service.get_policy("abc")
    assert detail.scope_kind == "catalog"
    assert detail.comment == "demo"
    assert detail.grant is not None
    assert detail.grant.privileges == ["EXECUTE"]
    assert detail.name == "approved_models"


class RoutedSql:
    """SQL stand-in returning rows (or raising) for the first route whose key appears in the SQL."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.queries: list[str] = []

    def query(self, statement: str, parameters=None):  # noqa: ANN001, ANN201
        del parameters
        self.queries.append(statement)
        for needle, value in self.routes.items():
            if needle in statement:
                if isinstance(value, Exception):
                    raise value
                return list(value)
        return []


class _PoliciesApi:
    def __init__(self, info: Any = None, error: Exception | None = None) -> None:
        self.info = info
        self.error = error
        self.calls: list[tuple[str, str, str]] = []

    def get_policy(self, on_securable_type: str, on_securable_fullname: str, name: str):  # noqa: ANN201
        self.calls.append((on_securable_type, on_securable_fullname, name))
        if self.error is not None:
            raise self.error
        return self.info


class _BrokenMetastores:
    def current(self):  # noqa: ANN201
        raise RuntimeError("no metastore")

    def get(self, _metastore_id: str):  # noqa: ANN201
        raise RuntimeError("no metastore")


def _row(**overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "policy_id": "p1",
        "policy_name": "mask_pii",
        "policy_type": "POLICY_TYPE_COLUMN_MASK",
        "catalog_name": "finance",
        "schema_name": None,
        "securable_name": None,
        "on_securable_type": "CATALOG",
        "to_principals": '["analysts"]',
        "except_principals": "[]",
        "for_securable_type": "TABLE",
        "privileges": None,
        "when_condition": "has_tag('sensitivity')",
        "match_columns": None,
        "governed_tags": '[{"tag_key":"sensitivity","tag_value":"PII"}]',
        "created_by": "owner@example.com",
    }
    row.update(overrides)
    return row


def _service_for(
    rows: list[dict[str, Any]] | None = None,
    *,
    routes: dict[str, Any] | None = None,
    metastore: object | None = None,
    api: _PoliciesApi | None = None,
) -> PolicyService:
    sql_routes = dict(routes or {})
    if rows is not None:
        sql_routes.setdefault("abac_policy_definitions", rows)
    admin = SimpleNamespace(
        metastores=metastore or FakeMetastores(),
        policies=api or _PoliciesApi(),
    )
    factory = SimpleNamespace(admin=lambda: admin)
    return PolicyService(factory, RoutedSql(sql_routes), FakeCache())  # type: ignore[arg-type]


def test_parsers_handle_loose_inputs() -> None:
    assert _parse_json_list(["a"]) == ["a"]
    assert _parse_json_list("not json") == []
    assert _parse_json_list('{"a": 1}') == []
    assert _parse_json_list("null") == []
    assert _parse_json_list("   ") == []
    assert _parse_json_list(None) == []
    assert _parse_string_list('["a", null, "  ", 3]') == ["a", "3"]
    tags = _parse_tags(
        '[{"key":"k1","value":"v"}, {"tag_key":"", "tag_value":"x"}, "bare", "  ", 5, {"tag_key":"k2"}]'
    )
    assert [(t.key, t.value) for t in tags] == [("k1", "v"), ("bare", None), ("k2", None)]
    assert _normalize_policy_type("") == "UNKNOWN"
    assert _normalize_securable_type(None) == "UNKNOWN"
    assert _scope_kind("SCHEMA") == "catalog"


def test_list_filters_by_type_catalog_and_text() -> None:
    rows = [
        _row(
            policy_id="1",
            policy_name="meta_mask",
            on_securable_type="METASTORE",
            catalog_name=None,
            governed_tags="[]",
        ),
        _row(
            policy_id="2",
            policy_name="fin_mask",
            catalog_name="Finance",
            policy_type="POLICY_TYPE_ROW_FILTER",
            governed_tags='[{"tag_key":"region","tag_value":"EU"}]',
        ),
        _row(
            policy_id="3",
            policy_name="hr_grant",
            catalog_name="hr",
            policy_type="POLICY_TYPE_GRANT",
            to_principals='["hr-team"]',
            governed_tags="[]",
            privileges='["SELECT"]',
        ),
    ]
    service = _service_for(rows)
    assert [p.name for p in service.list_policies(policy_type="row_filter").catalog] == ["fin_mask"]
    by_catalog = service.list_policies(catalog="finance")
    assert [p.name for p in by_catalog.catalog] == ["fin_mask"]
    assert by_catalog.metastore == []
    assert [p.name for p in service.list_policies(q="region:eu").catalog] == ["fin_mask"]
    assert [p.name for p in service.list_policies(q="hr-team").catalog] == ["hr_grant"]
    assert service.list_policies(scope="unknown-scope").total == 3
    assert service.list_policies(q="zzz-no-match").total == 0


def test_list_survives_metastore_lookup_failure() -> None:
    service = _service_for(
        [_row(on_securable_type="METASTORE", catalog_name=None, governed_tags="[]")],
        metastore=_BrokenMetastores(),
    )
    result = service.list_policies()
    assert result.metastore_name is None
    assert result.metastore[0].on_securable_fullname == "metastore"


def test_list_reports_query_failures_as_upstream_errors() -> None:
    service = _service_for(routes={"abac_policy_definitions": RuntimeError("warehouse down")})
    with pytest.raises(UpstreamError, match="Could not list ABAC policies"):
        service.list_policies()


def test_securable_fullnames_follow_securable_type() -> None:
    rows = [
        _row(policy_id="t", on_securable_type="TABLE", schema_name="sales", securable_name="orders"),
        _row(policy_id="s", on_securable_type="SCHEMA", schema_name="sales"),
        _row(policy_id="v", on_securable_type="VIEW", securable_name="v1"),
        _row(
            policy_id="n",
            on_securable_type=None,
            catalog_name=None,
            for_securable_type=None,
            governed_tags="[]",
        ),
    ]
    result = _service_for(rows).list_policies()
    assert {p.id: p.on_securable_fullname for p in result.catalog} == {
        "t": "finance.sales.orders",
        "s": "finance.sales",
        "v": "finance.v1",
        "n": "",
    }


def test_get_policy_unknown_id_and_api_not_found() -> None:
    with pytest.raises(NotFoundError):
        _service_for([_row()]).get_policy("nope")

    service = _service_for([_row()], api=_PoliciesApi(error=NotFound("gone")))
    with pytest.raises(NotFoundError, match="was not found on finance"):
        service.get_policy("p1")


def test_get_policy_falls_back_to_information_schema_when_api_fails() -> None:
    service = _service_for([_row()], api=_PoliciesApi(error=RuntimeError("api down")))
    detail = service.get_policy("p1")
    assert detail.comment is None
    assert detail.to_principals == ["analysts"]
    assert detail.grant is None
    assert detail.affected_assets == []


def test_get_policy_overlays_api_details_and_finds_tagged_assets() -> None:
    info = SimpleNamespace(
        comment="PII mask",
        created_at=10,
        updated_at=20,
        updated_by="owner@example.com",
        to_principals=["analysts"],
        except_principals=["admins"],
        when_condition="has_tag('sensitivity')",
        governed_tags=[
            SimpleNamespace(tag_key="sensitivity", tag_value="PII"),
            SimpleNamespace(tag_key="region", tag_value=None),
            SimpleNamespace(tag_key=None, tag_value=None),
        ],
        match_columns=[SimpleNamespace(alias="c", condition="c.x > 1"), SimpleNamespace(alias=None)],
        column_mask=SimpleNamespace(function_name="mask_fn", on_column="email"),
        row_filter=SimpleNamespace(
            function_name="rf_fn",
            using=[SimpleNamespace(alias="region"), SimpleNamespace(alias=None)],
        ),
        grant=None,
        deny=SimpleNamespace(privileges=["SELECT"]),
    )
    column_hit = {
        "asset_type": "COLUMN",
        "catalog_name": "finance",
        "schema_name": "sales",
        "table_name": "orders",
        "column_name": "email",
        "tag_name": "sensitivity",
        "tag_value": "PII",
    }
    table_hit = {
        "asset_type": "TABLE",
        "catalog_name": "finance",
        "schema_name": "sales",
        "table_name": "orders",
        "column_name": None,
        "tag_name": "region",
        "tag_value": None,
    }
    api = _PoliciesApi(info=info)
    sql_routes = {
        "abac_policy_definitions": [_row()],
        "column_tags": [column_hit, column_hit],  # duplicate is de-duplicated
        "table_tags": [table_hit],
    }
    service = _service_for(routes=sql_routes, api=api)
    detail = service.get_policy("p1")

    assert api.calls == [("catalog", "finance", "mask_pii")]
    assert detail.comment == "PII mask"
    assert (detail.created_at, detail.updated_at, detail.updated_by) == (10, 20, "owner@example.com")
    assert detail.except_principals == ["admins"]
    assert [(t.key, t.value) for t in detail.governed_tags] == [("sensitivity", "PII"), ("region", None)]
    assert [m.alias for m in detail.match_columns] == ["c"]
    assert detail.column_mask is not None
    assert (detail.column_mask.function_name, detail.column_mask.on_column) == ("mask_fn", "email")
    assert detail.row_filter is not None
    assert detail.row_filter.using == ["region"]
    assert detail.grant is None
    assert detail.deny is not None
    assert detail.privileges == ["SELECT"]

    assert [(a.full_name, a.asset_type) for a in detail.affected_assets] == [
        ("finance.sales.orders.email", "COLUMN"),
        ("finance.sales.orders", "TABLE"),
    ]
    assert detail.affected_assets_truncated is False
    assert "approximate" in (detail.affected_assets_note or "")
    column_query = next(q for q in service._sql.queries if "column_tags" in q)  # noqa: SLF001
    assert "(tag_name = 'sensitivity' AND tag_value = 'PII')" in column_query
    assert "tag_name = 'region'" in column_query


def test_get_policy_with_sparse_api_payload_keeps_information_schema_values() -> None:
    service = _service_for([_row()], api=_PoliciesApi(info=SimpleNamespace()))
    detail = service.get_policy("p1")
    assert detail.to_principals == ["analysts"]
    assert detail.when_condition == "has_tag('sensitivity')"
    assert detail.column_mask is None
    assert detail.row_filter is None


def test_get_policy_grant_privileges_are_recorded() -> None:
    info = SimpleNamespace(grant=SimpleNamespace(privileges=["EXECUTE"]), deny=None)
    detail = _service_for([_row()], api=_PoliciesApi(info=info)).get_policy("p1")
    assert detail.grant is not None
    assert detail.grant.privileges == ["EXECUTE"]
    assert detail.privileges == ["EXECUTE"]


def test_affected_assets_explain_unsupported_and_untagged_policies() -> None:
    rows = [
        _row(for_securable_type="MODEL", governed_tags='[{"tag_key":"x","tag_value":null}]'),
        _row(policy_id="p2", governed_tags="[]"),
    ]
    service = _service_for(rows)
    model_detail = service.get_policy("p1")
    assert model_detail.affected_assets == []
    assert "not available yet" in (model_detail.affected_assets_note or "")
    untagged_detail = service.get_policy("p2")
    assert "does not reference governed tags" in (untagged_detail.affected_assets_note or "")


def test_affected_assets_report_lookup_failures() -> None:
    service = _service_for(routes={"abac_policy_definitions": [_row()], "column_tags": RuntimeError("no access")})
    detail = service.get_policy("p1")
    assert detail.affected_assets == []
    assert detail.affected_assets_note == "Could not resolve tagged assets: no access"


def test_affected_assets_truncate_at_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policies_module, "_ASSET_LIMIT", 2)
    columns = [
        {
            "asset_type": "COLUMN",
            "catalog_name": "finance",
            "schema_name": "sales",
            "table_name": f"t{i}",
            "column_name": "email",
            "tag_name": "sensitivity",
            "tag_value": "PII",
        }
        for i in range(3)
    ]
    service = _service_for(routes={"abac_policy_definitions": [_row()], "column_tags": columns})
    detail = service.get_policy("p1")
    assert len(detail.affected_assets) == 2
    assert detail.affected_assets_truncated is True
    assert "Showing the first 2 matches" in (detail.affected_assets_note or "")


def test_scope_filter_sql_restricts_to_the_policy_securable() -> None:
    def scope(**fields: Any) -> str:
        return PolicyService._scope_filter_sql(SimpleNamespace(**fields))  # noqa: SLF001

    assert scope(on_securable_type="METASTORE", catalog_name=None, on_securable_fullname="m") == ""
    assert "catalog_name = 'finance'" in scope(
        on_securable_type="CATALOG", catalog_name="finance", on_securable_fullname="finance"
    )
    schema_sql = scope(on_securable_type="SCHEMA", catalog_name=None, on_securable_fullname="finance.sales")
    assert "catalog_name = 'finance'" in schema_sql
    assert "schema_name = 'sales'" in schema_sql
    table_sql = scope(on_securable_type="TABLE", catalog_name=None, on_securable_fullname="finance.sales.orders")
    assert "table_name = 'orders'" in table_sql
    assert scope(on_securable_type="SCHEMA", catalog_name="finance", on_securable_fullname="bad") == (
        " AND catalog_name = 'finance'"
    )
    assert scope(on_securable_type="VIEW", catalog_name=None, on_securable_fullname="x") == ""
