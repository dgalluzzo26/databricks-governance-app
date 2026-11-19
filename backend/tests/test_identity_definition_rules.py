"""Purpose rules for Identity definitions that share the access_path governed tag."""

from __future__ import annotations

from backend.app.models.identity_attribute import is_data_access_definition, is_sensitivity_definition


def test_sensitivity_definition_is_not_data_access_even_though_it_binds_access_path() -> None:
    # The sensitivity definition composes access_path.sensitivity, so it links the access tag too.
    assert is_sensitivity_definition("sensitivity", ["access_path", "sensitivity"])
    assert not is_data_access_definition("sensitivity", ["access_path", "sensitivity"], "access_path")


def test_data_access_definition_is_matched_by_access_tag_or_key() -> None:
    assert is_data_access_definition("data_access", ["access_path"], "access_path")
    assert is_data_access_definition("access_path", ["access_path"], "access_path")
    assert not is_data_access_definition("data_access", [], "access_path")


def test_sensitivity_tag_alone_marks_sensitivity_not_data_access() -> None:
    assert is_sensitivity_definition("tag_scope", ["sensitivity"])
    assert not is_data_access_definition("tag_scope", ["sensitivity", "access_path"], "access_path")


def test_blank_access_tag_matches_nothing() -> None:
    assert not is_data_access_definition("data_access", ["access_path"], "  ")
