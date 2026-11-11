"""Tests for the shared app-owned ABAC UDF DDL builders."""

from __future__ import annotations

import pytest

from backend.app.services import abac_udfs


def test_fqn_quotes_segments_and_rejects_bad_names() -> None:
    assert abac_udfs.fqn("cat", "sch", "fn") == "`cat`.`sch`.`fn`"
    with pytest.raises(ValueError):
        abac_udfs.fqn("cat", "bad name")
    with pytest.raises(ValueError):
        abac_udfs.fqn("cat;DROP", "x")


def test_has_sensitivity_reads_identity_mirror_with_prefix_match() -> None:
    sql = abac_udfs.build_has_sensitivity_sql("`c`.`s`.`has_sensitivity`", "`c`.`app`.`identity_attributes`")
    assert sql.startswith("CREATE OR REPLACE FUNCTION `c`.`s`.`has_sensitivity`(required_path STRING)")
    assert "RETURNS BOOLEAN" in sql
    assert "FROM `c`.`app`.`identity_attributes` a" in sql
    assert "'.%'" in sql
    assert "a.revoked_at IS NULL" in sql


def test_mask_variant_passes_value_or_applies_rule() -> None:
    sql = abac_udfs.build_mask_variant_sql(
        "`c`.`s`.`mask_variant`", "`c`.`s`.`has_sensitivity`", "`c`.`s`.`apply_mask_rule`"
    )
    assert "RETURNS VARIANT" in sql
    assert "THEN value" in sql
    assert "ELSE `c`.`s`.`apply_mask_rule`(value, mask_rule)" in sql
    assert "WHEN value IS NULL THEN NULL::VARIANT" in sql


@pytest.mark.parametrize(
    ("kind", "typed"), [("geometry", "GEOMETRY(4326)"), ("geography", "GEOGRAPHY(4326)")]
)
def test_geo_mask_uses_srid_types(kind: str, typed: str) -> None:
    sql = abac_udfs.build_geo_mask_sql(kind, "`c`.`s`.`mask_x`", "`c`.`s`.`has_sensitivity`")
    assert f"g {typed}" in sql
    assert f"RETURNS {typed}" in sql
    assert "ELSE NULL" in sql


def test_geo_mask_rejects_unknown_kind() -> None:
    with pytest.raises(ValueError):
        abac_udfs.build_geo_mask_sql("point", "`c`.`s`.`f`", "`c`.`s`.`h`")
