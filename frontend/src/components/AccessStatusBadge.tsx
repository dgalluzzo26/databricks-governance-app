/**
 * @file Badge showing whether a data-access assignment gives access yet.
 *
 * Access needs both membership in the path's `dac_*` group (added at approval) and the path's
 * structure (group nesting and table grants, built by the sync). The badge collapses the two
 * into one label; the full explanation is in the tooltip.
 */
import type { AccessStatus } from "@/api/types";
import { Badge } from "@/components/ui";
import type { BadgeTone } from "@/components/ui/Badge";

/** Label and tone for one access status. */
export function accessStatusLabel(status: AccessStatus): { label: string; tone: BadgeTone } {
  switch (status.membership) {
    case "failed":
      return { label: "Failed · sync retries", tone: "danger" };
    case "removal_pending":
      return { label: "Removal at next sync", tone: "warning" };
    case "removed":
      return { label: "Removed", tone: "neutral" };
    case "not_applicable":
      return { label: "No group", tone: "neutral" };
    case "unknown":
      return { label: "No sync yet", tone: "neutral" };
    case "pending_sync":
      return { label: "Waiting on sync", tone: "warning" };
    case "member":
      return status.structure === "ready"
        ? { label: "Has access", tone: "success" }
        : { label: "Member · grants pending", tone: "warning" };
  }
}

/**
 * Access badge for one assignment, or an em dash when it is not a data-access assignment.
 *
 * @param props.status - Status from the access-status API, or undefined when none applies.
 */
export function AccessStatusBadge({ status }: { status: AccessStatus | undefined }) {
  if (!status) return <span aria-hidden="true">—</span>;
  const { label, tone } = accessStatusLabel(status);
  return (
    <span title={status.detail} aria-label={`${label}. ${status.detail}`}>
      <Badge tone={tone}>{label}</Badge>
    </span>
  );
}
