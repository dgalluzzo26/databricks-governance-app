/**
 * @file Route guard that allows only app admins to render admin-only pages.
 */
import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useCurrentUser } from "@/api/queries";
import { Alert, Spinner } from "@/components/ui";

/**
 * Blocks non-admin users from admin-only screens.
 * @param props.children - Protected page content rendered when the user is an admin.
 * @remarks Shows a spinner while identity loads, an alert on error, and redirects
 * non-admins to `/access-explorer`.
 */
export function RequireAdmin({ children }: { children: ReactNode }) {
  const { data: user, isPending, error } = useCurrentUser();
  if (isPending) return <Spinner label="Checking access" />;
  if (error) {
    return <Alert tone="danger" title="Could not verify access">{error.message}</Alert>;
  }
  if (!user?.isAdmin) {
    return <Navigate to="/access-explorer" replace />;
  }
  return <>{children}</>;
}
