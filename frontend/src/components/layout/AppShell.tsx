import { Outlet } from "react-router-dom";
import { useCurrentUser } from "@/api/queries";
import { ErrorAlert, Spinner } from "@/components/ui";
import { SideNav } from "./SideNav";
import { TopBar } from "./TopBar";
import styles from "./AppShell.module.css";

export function AppShell() {
  const { data: user, isPending, error } = useCurrentUser();

  return (
    <div className={styles.shell}>
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
