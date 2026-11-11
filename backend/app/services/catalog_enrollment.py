"""Shared helpers for the catalog enrollment tag governance perimeter."""

from __future__ import annotations

from typing import Any, Mapping

from backend.app.clients.sql import string_literal

DEFAULT_ENROLLMENT_TAG_KEY = "data_access_controlled"
DEFAULT_ENROLLMENT_TAG_VALUE = "true"
_SETTINGS_KEY = "data_access"


def enrollment_tag_from_raw(raw: Mapping[str, Any] | None) -> tuple[str, str]:
    """Return ``(tag_key, tag_value)`` from a stored data-access settings dict.

    Args:
        raw: Persisted ``data_access`` settings blob, or None.

    Returns:
        Non-empty enrollment tag key and value (defaults when missing/blank).
    """
    data = raw if isinstance(raw, Mapping) else {}
    key = str(data.get("enrollment_catalog_tag_key") or DEFAULT_ENROLLMENT_TAG_KEY).strip()
    value = str(data.get("enrollment_catalog_tag_value") or DEFAULT_ENROLLMENT_TAG_VALUE).strip()
    return (
        key or DEFAULT_ENROLLMENT_TAG_KEY,
        value or DEFAULT_ENROLLMENT_TAG_VALUE,
    )


def enrollment_tag_from_storage(settings_get_all: Mapping[str, Any]) -> tuple[str, str]:
    """Read enrollment tag key/value from storage ``settings.get_all()`` output."""
    return enrollment_tag_from_raw(settings_get_all.get(_SETTINGS_KEY))


def enrollment_when_clause(tag_key: str, tag_value: str) -> str:
    """Return an ABAC ``WHEN`` clause requiring the enrollment catalog tag."""
    return (
        f"WHEN has_tag_value({string_literal(tag_key)}, {string_literal(tag_value)})"
    )


def enrolled_catalogs_sql(tag_key: str, tag_value: str) -> str:
    """Return a SELECT that lists catalogs carrying the enrollment tag."""
    return f"""
        SELECT catalog_name
        FROM system.information_schema.catalog_tags
        WHERE lower(tag_name) = lower({string_literal(tag_key)})
          AND lower(tag_value) = lower({string_literal(tag_value)})
        ORDER BY catalog_name
    """
