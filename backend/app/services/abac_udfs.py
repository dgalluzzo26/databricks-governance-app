"""Pure SQL builders for the app-owned ABAC authorization and mask UDFs.

Shared by two entry points so they can never drift:

* ``MaskRuleService.ensure_pack`` (app, Account Admin SP) on **Sync registry / Ensure pack**.
* ``provisioning.steps.masking_functions`` (setup job) on deploy.

This module must stay dependency-free (standard library only): the serverless setup job
imports it without the FastAPI/app runtime installed.
"""

from __future__ import annotations

import re

_SEGMENT = re.compile(r"^[A-Za-z0-9_\-]+$")

# Column-mask types must be SRID-qualified; bare GEOMETRY / GEOGRAPHY fail with
# UNSUPPORTED_DATATYPE on SQL warehouses.
GEO_KINDS: dict[str, str] = {"geometry": "GEOMETRY(4326)", "geography": "GEOGRAPHY(4326)"}


def fqn(*parts: str) -> str:
    """Backtick-quote a dotted Unity Catalog name from validated segments.

    Raises:
        ValueError: If any segment contains characters outside ``[A-Za-z0-9_-]``.
    """
    for part in parts:
        if not _SEGMENT.match(part):
            raise ValueError(f"Invalid Unity Catalog name segment: {part!r}")
    return ".".join(f"`{part}`" for part in parts)


def build_has_sensitivity_sql(function_fqn: str, identity_table_fqn: str) -> str:
    """Return DDL for ``has_sensitivity(required_path)``.

    True when the session user (directly, or via an account group) holds an active,
    unrevoked, unexpired ``sensitivity`` attribute equal to ``required_path`` or to one of
    its dotted prefixes.
    """
    return f"""CREATE OR REPLACE FUNCTION {function_fqn}(required_path STRING)
RETURNS BOOLEAN
LANGUAGE SQL
READS SQL DATA
RETURN EXISTS (
  SELECT 1
  FROM {identity_table_fqn} a
  WHERE a.attribute_key = 'sensitivity'
    AND a.revoked_at IS NULL
    AND (a.expires_at IS NULL OR a.expires_at > current_timestamp())
    AND (
      (a.principal_type = 'USER' AND lower(a.principal_name) = lower(session_user()))
      OR (a.principal_type = 'GROUP' AND is_account_group_member(a.principal_name))
    )
    AND (
      lower(required_path) = lower(a.attribute_value)
      OR lower(required_path) LIKE concat(lower(a.attribute_value), '.%')
    )
)"""


def build_mask_variant_sql(function_fqn: str, has_sensitivity_fqn: str, apply_fqn: str) -> str:
    """Return DDL for ``mask_variant(value, sensitivity_category, sensitivity, mask_rule)``.

    Returns the value when the caller holds ``category.sensitivity``; otherwise applies the
    registry mask rule via ``apply_mask_rule``. NULL values stay NULL.
    """
    return f"""CREATE OR REPLACE FUNCTION {function_fqn}(
  value VARIANT,
  sensitivity_category STRING,
  sensitivity STRING,
  mask_rule STRING
)
RETURNS VARIANT
LANGUAGE SQL
READS SQL DATA
RETURN CASE
  WHEN value IS NULL THEN NULL::VARIANT
  WHEN {has_sensitivity_fqn}(concat_ws('.', sensitivity_category, sensitivity)) THEN value
  ELSE {apply_fqn}(value, mask_rule)
END"""


def build_geo_mask_sql(kind: str, function_fqn: str, has_sensitivity_fqn: str) -> str:
    """Return DDL for ``mask_<kind>_4326(g, sensitivity_category, sensitivity)``.

    Returns the geometry/geography value when the caller holds the sensitivity path,
    otherwise ``NULL``.

    Raises:
        ValueError: If ``kind`` is not ``geometry`` or ``geography``.
    """
    if kind not in GEO_KINDS:
        raise ValueError(f"Unsupported geo kind: {kind!r}")
    typed = GEO_KINDS[kind]
    return f"""CREATE OR REPLACE FUNCTION {function_fqn}(
  g {typed},
  sensitivity_category STRING,
  sensitivity STRING
)
RETURNS {typed}
LANGUAGE SQL
READS SQL DATA
RETURN CASE
  WHEN {has_sensitivity_fqn}(concat_ws('.', sensitivity_category, sensitivity)) THEN g
  ELSE NULL
END"""
