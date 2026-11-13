/**
 * @file Top-bar user menu: identity summary, role badge, view-as demo, and workspace link.
 */
import { useEffect, useRef, useState } from "react";
import type { CurrentUser } from "@/api/types";
import { useViewAs, viewAsLabel, type ViewAsMode } from "@/app/viewAs";
import { ChevronDownIcon, ExternalIcon } from "@/components/icons";
import { Avatar, Badge } from "@/components/ui";
import styles from "./UserMenu.module.css";

const VIEW_AS_OPTIONS: { mode: ViewAsMode; label: string }[] = [
  { mode: "admin", label: "Admin (me)" },
  { mode: "user", label: "User" },
  { mode: "approver", label: "Approver" },
];

/**
 * Dropdown for the signed-in user in the top bar.
 * @param props.user - Effective user (role flags may be demo-overlaid).
 * @param props.realUser - Real identity from `GET /me`; used to show View as when admin.
 * @remarks Badge priority is Admin, then Approver, then User. Closes on outside click or Escape.
 */
export function UserMenu({ user, realUser }: { user: CurrentUser; realUser?: CurrentUser }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const { viewAs, setViewAs } = useViewAs();
  const canViewAs = Boolean(realUser?.isAdmin);

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

  const roleLabel = user.isAdmin ? "Admin" : user.isApprover ? "Approver" : "User";

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
        <Badge tone={user.isAdmin ? "info" : "neutral"}>{roleLabel}</Badge>
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
              {viewAs !== "admin" ? ` · viewing as ${viewAsLabel(viewAs)}` : ""}
            </Badge>
          </div>
          {canViewAs && (
            <div className={styles.viewAs} role="group" aria-label="View as">
              <div className={styles.viewAsTitle}>View as</div>
              {VIEW_AS_OPTIONS.map(({ mode, label }) => (
                <button
                  key={mode}
                  type="button"
                  role="menuitemradio"
                  aria-checked={viewAs === mode}
                  className={`${styles.viewAsOption}${viewAs === mode ? ` ${styles.viewAsActive}` : ""}`}
                  onClick={() => {
                    setViewAs(mode);
                    setOpen(false);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
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
