import type { ReactNode } from "react";
import { Navigate } from "react-router-dom";
import { useCurrentUser } from "@/api/queries";
import { Alert, Spinner } from "@/components/ui";

/** Blocks non-admin users from admin-only screens. */
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
