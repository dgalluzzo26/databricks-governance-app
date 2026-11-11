/**
 * @file Application top bar with brand link, theme toggle, and optional user menu.
 */
import { Link } from "react-router-dom";
import type { CurrentUser } from "@/api/types";
import { DatabricksLogo } from "@/components/icons";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";
import styles from "./TopBar.module.css";

/**
 * Sticky header chrome for the app shell.
 * @param props.user - Signed-in user; when absent, the user menu is omitted (e.g. while loading).
 */
export function TopBar({ user }: { user?: CurrentUser }) {
  return (
    <header className={styles.topbar}>
      <Link to="/" className={styles.brand}>
        <DatabricksLogo />
        <span className={styles.product}>Data Governance</span>
      </Link>
      <div className={styles.spacer} />
      <ThemeToggle />
      {user && <UserMenu user={user} />}
    </header>
  );
}
