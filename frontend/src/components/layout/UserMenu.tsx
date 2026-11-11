import { useEffect, useRef, useState } from "react";
import type { CurrentUser } from "@/api/types";
import { ChevronDownIcon, ExternalIcon } from "@/components/icons";
import { Avatar, Badge } from "@/components/ui";
import styles from "./UserMenu.module.css";

export function UserMenu({ user }: { user: CurrentUser }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent ? event.key === "Escape" : !ref.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [open]);

  return (
    <div className={styles.wrapper} ref={ref}>
      <button
        type="button"
        className={styles.trigger}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Avatar name={user.displayName} />
        <span className={styles.name}>{user.displayName}</span>
        <Badge tone={user.isAdmin ? "info" : "neutral"}>
          {user.isAdmin ? "Admin" : user.isApprover ? "Approver" : "User"}
        </Badge>
        <ChevronDownIcon />
      </button>
      {open && (
        <div className={styles.menu} role="menu">
          <div className={styles.identity}>
            <Avatar name={user.displayName} size={36} />
            <div>
              <div className={styles.displayName}>{user.displayName}</div>
              <div className={styles.email}>{user.email}</div>
            </div>
          </div>
          <div className={styles.section}>
            <Badge tone={user.isAdmin ? "info" : "neutral"}>
              {user.isAdmin ? "App admin" : user.isApprover ? "Category approver" : "User"}
            </Badge>
          </div>
          {user.workspaceHost && (
            <a className={styles.item} href={user.workspaceHost} target="_blank" rel="noreferrer" role="menuitem">
              Open workspace <ExternalIcon />
            </a>
          )}
        </div>
      )}
    </div>
  );
}
