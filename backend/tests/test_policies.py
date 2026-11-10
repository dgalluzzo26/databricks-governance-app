from __future__ import annotations

from backend.app.services.policies import (
    PolicyService,
    _normalize_policy_type,
    _parse_string_list,
    _parse_tags,
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
