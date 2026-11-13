/**
 * @file Application top bar with brand link, theme toggle, and optional user menu.
 */
import { Link } from "react-router-dom";
import type { CurrentUser } from "@/api/types";
import { DatabricksLogo } from "@/components/icons";
import { BusyStatus } from "./BusyBar";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";
import styles from "./TopBar.module.css";

/**
 * Sticky header chrome for the app shell.
 * @param props.user - Effective user shown in the menu (role flags may be demo-overlaid).
 * @param props.realUser - Real identity; when admin, unlocks the View as control.
 */
export function TopBar({ user, realUser }: { user?: CurrentUser; realUser?: CurrentUser }) {
  return (
    <header className={styles.topbar}>
      <Link to="/" className={styles.brand}>
        <DatabricksLogo />
        <span className={styles.product}>Data Governance</span>
      </Link>
      <div className={styles.spacer} />
      <BusyStatus />
      <ThemeToggle />
      {user && <UserMenu user={user} realUser={realUser} />}
    </header>
  );
}
