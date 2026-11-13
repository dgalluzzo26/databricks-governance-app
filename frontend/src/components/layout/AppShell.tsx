/**
 * @file Layout shell: top bar, side nav, and main outlet gated on current-user load.
 */
import { useEffect } from "react";
import { Outlet, useLocation, useNavigate } from "react-router-dom";
import { useCurrentUser } from "@/api/queries";
import { isAdminOnlyPath, useEffectiveUser, useViewAs } from "@/app/viewAs";
import { ErrorAlert, Spinner } from "@/components/ui";
import { BusyBar } from "./BusyBar";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";
import { ViewAsBanner } from "./ViewAsBanner";
import styles from "./AppShell.module.css";

/**
 * Authenticated app chrome around routed page content.
 * @remarks Child routes render only after the current user resolves successfully;
 * pending and error states are shown in the main pane instead of the outlet.
 * {@link BusyBar} shows a top progress indicator while mutations or refreshes run.
 * Side nav and outlet gates use {@link useEffectiveUser} so admin demo preview applies.
 */
export function AppShell() {
  const { data: realUser, isPending, error } = useCurrentUser();
  const { data: user } = useEffectiveUser();
  const { viewAs } = useViewAs();
  const location = useLocation();
  const navigate = useNavigate();

  useEffect(() => {
    if (viewAs !== "admin" && isAdminOnlyPath(location.pathname)) {
      navigate("/access-explorer", { replace: true });
    }
  }, [viewAs, location.pathname, navigate]);

  const effective = user ?? realUser;
  const isAdmin = effective?.isAdmin ?? false;
  const canApprove = Boolean(effective?.isAdmin || effective?.isApprover);

  return (
    <div className={styles.shell}>
      <BusyBar />
      <TopBar user={effective} realUser={realUser} />
      <ViewAsBanner />
      <div className={styles.body}>
        <SideNav isAdmin={isAdmin} canApprove={canApprove} />
        <main className={styles.main}>
          {isPending && <Spinner label="Signing you in" />}
          {error && <ErrorAlert error={error} title="Could not load your identity" />}
          {realUser && <Outlet />}
        </main>
      </div>
    </div>
  );
}
