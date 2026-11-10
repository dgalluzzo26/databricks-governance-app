import type { BadgeTone } from "@/components/ui/Badge";
import type { PolicySummary, TagRef } from "@/api/types";

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

export function formatPolicyType(policyType: string): string {
  return policyType.replaceAll("_", " ");
}

export function formatTag(tag: TagRef): string {
  return tag.value == null ? tag.key : `${tag.key}=${tag.value}`;
}

export function principalsLabel(policy: PolicySummary): string {
  const included = policy.toPrincipals.length
    ? policy.toPrincipals.join(", ")
    : "No principals";
  if (!policy.exceptPrincipals.length) return included;
  return `${included} (except ${policy.exceptPrincipals.join(", ")})`;
}
