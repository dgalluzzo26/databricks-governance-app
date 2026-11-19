import type { AttributeDefinition } from "@/api/types";

/**
 * Purpose rules for Identity definitions. Mirrors `is_data_access_definition` on the backend:
 * the sensitivity definition composes `access_path.sensitivity` values, so it also binds the
 * access tag, but it is never a data-access definition.
 */
const SENSITIVITY_TAG = "sensitivity";

type DefinitionLike = Pick<AttributeDefinition, "key" | "governedTagKeys">;

export function isSensitivityDefinition(item: DefinitionLike): boolean {
  return (
    item.key.toLowerCase() === SENSITIVITY_TAG ||
    item.governedTagKeys.some((tag) => tag.toLowerCase() === SENSITIVITY_TAG)
  );
}

export function isDataAccessDefinition(item: DefinitionLike, accessTagKey: string): boolean {
  const access = accessTagKey.trim().toLowerCase();
  if (!access || isSensitivityDefinition(item)) return false;
  return (
    item.key.toLowerCase() === access ||
    item.governedTagKeys.some((tag) => tag.toLowerCase() === access)
  );
}
