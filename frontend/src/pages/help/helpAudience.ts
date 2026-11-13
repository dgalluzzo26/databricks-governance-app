/**
 * @file Pure helpers: which Help guides and chrome an effective role should see.
 */
import type { CurrentUser, HelpDoc } from "@/api/types";

/** Roles used to filter Help (mirrors effective `isAdmin` / `isApprover`). */
export type HelpRole = "admin" | "approver" | "user";

/**
 * Effective Help role from the (possibly View as–overlaid) user.
 *
 * Admins see only admin guides. Approvers see user + approver. Everyone else sees user.
 */
export function helpRoleFor(user: Pick<CurrentUser, "isAdmin" | "isApprover"> | null | undefined): HelpRole {
  if (user?.isAdmin) return "admin";
  if (user?.isApprover) return "approver";
  return "user";
}

/** Page title for the Help header. */
export function helpPageTitle(role: HelpRole): string {
  if (role === "admin") return "Admin guide";
  if (role === "approver") return "Approver guide";
  return "Help";
}

/** Short description under the Help title. */
export function helpPageDescription(role: HelpRole): string {
  if (role === "admin") {
    return "Technical guides for masking, row filters, grants sync, and the live API reference.";
  }
  if (role === "approver") {
    return "How to review access requests and inspect attribute holders. Includes the user guide.";
  }
  return "How to explore tables, request access, and track your attributes.";
}

/**
 * Guides visible for an effective Help role.
 *
 * @param docs - Full catalog from `GET /help/docs`.
 * @param role - From {@link helpRoleFor}.
 */
export function visibleHelpDocs(docs: readonly HelpDoc[], role: HelpRole): HelpDoc[] {
  if (role === "admin") {
    return docs.filter((doc) => doc.audiences.includes("admin"));
  }
  if (role === "approver") {
    return docs.filter(
      (doc) => doc.audiences.includes("user") || doc.audiences.includes("approver"),
    );
  }
  return docs.filter((doc) => doc.audiences.includes("user"));
}

/** Whether the API reference tab is shown (admins only). */
export function showHelpApiReference(role: HelpRole): boolean {
  return role === "admin";
}
