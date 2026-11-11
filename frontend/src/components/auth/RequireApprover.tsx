/**
 * @file Route guard that allows app admins and category approvers to render the Approvals page.
 */
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useCurrentUser } from "@/api/queries";
import { Alert, Spinner } from "@/components/ui";

/**
 * Blocks users who are neither admins nor category approvers.
 * @param props.children - Protected page content.
 * @remarks Shows a spinner while identity loads, an alert on error, and redirects
 * unauthorized users to `/access-explorer`.
 */
export function RequireApprover({ children }: { children: ReactNode }) {
  const { data: user, isPending, error } = useCurrentUser();
  if (isPending) return <Spinner label="Checking access" />;
  if (error) {
    return <Alert tone="danger" title="Could not verify access">{error.message}</Alert>;
  }
  if (!(user?.isAdmin || user?.isApprover)) {
    return <Navigate to="/access-explorer" replace />;
  }
  return <>{children}</>;
}
