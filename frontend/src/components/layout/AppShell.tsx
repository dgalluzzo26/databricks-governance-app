/**
 * @file Layout shell: top bar, side nav, and main outlet gated on current-user load.
 */
import { Outlet } from "react-router-dom";
import { useCurrentUser } from "@/api/queries";
import { ErrorAlert, Spinner } from "@/components/ui";
import { BusyBar } from "./BusyBar";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";
import styles from "./AppShell.module.css";

/**
 * Authenticated app chrome around routed page content.
 * @remarks Child routes render only after the current user resolves successfully;
 * pending and error states are shown in the main pane instead of the outlet.
 * {@link BusyBar} shows a top progress indicator while mutations or refreshes run.
 */
export function AppShell() {
  const { data: user, isPending, error } = useCurrentUser();

  return (
    <div className={styles.shell}>
      <BusyBar />
      <TopBar user={user} />
      <div className={styles.body}>
        <SideNav isAdmin={user?.isAdmin ?? false} />
        <main className={styles.main}>
          {isPending && <Spinner label="Signing you in" />}
          {error && <ErrorAlert error={error} title="Could not load your identity" />}
          {user && <Outlet />}
        </main>
      </div>
    </div>
  );
}
