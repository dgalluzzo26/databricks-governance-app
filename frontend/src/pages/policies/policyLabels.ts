/**
 * @file Display helpers for ABAC policy type badges, governed tags, and principal lists.
 */
import type { BadgeTone } from "@/components/ui/Badge";
import type { TagRef } from "@/api/types";

/**
 * Maps a Unity Catalog policy type to a {@link BadgeTone}.
 *
 * @param policyType - Raw type such as `COLUMN_MASK`, `ROW_FILTER`, `GRANT`, or `DENY`.
 * Unrecognized values use `neutral`.
 */
export function policyTypeTone(policyType: string): BadgeTone {
  switch (policyType) {
    case "COLUMN_MASK":
      return "info";
    case "ROW_FILTER":
      return "warning";
    case "GRANT":
      return "success";
    case "DENY":
      return "danger";
    default:
      return "neutral";
  }
}

/**
 * Replaces underscores in a policy type token with spaces for display.
 *
 * @param policyType - Raw type such as `COLUMN_MASK`.
 */
export function formatPolicyType(policyType: string): string {
  return policyType.replaceAll("_", " ");
}

/**
 * Formats a governed tag as `key` or `key=value` when a value is present.
 *
 * @param tag - Tag reference from a policy summary or detail payload.
 */
export function formatTag(tag: TagRef): string {
  return tag.value == null ? tag.key : `${tag.key}=${tag.value}`;
}