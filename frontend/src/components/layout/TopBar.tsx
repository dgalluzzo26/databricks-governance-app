import { Link } from "react-router-dom";
import type { CurrentUser } from "@/api/types";
import { DatabricksLogo } from "@/components/icons";
import { UserMenu } from "./UserMenu";
import styles from "./TopBar.module.css";

export function TopBar({ user }: { user?: CurrentUser }) {
  return (
    <header className={styles.topbar}>
      <Link to="/" className={styles.brand}>
        <DatabricksLogo />
        <span className={styles.product}>Data Governance</span>
      </Link>
      <div className={styles.spacer} />
      {user && <UserMenu user={user} />}
    </header>
  );
}
