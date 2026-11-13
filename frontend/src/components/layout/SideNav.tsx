/**
 * @file Collapsible main side navigation driven by {@link navigation} and role visibility.
 */
import { NavLink } from "react-router-dom";
import { useApprovalsInbox } from "@/api/queries";
import { navigation, type NavItem } from "@/app/navigation";
import { usePreferences } from "@/app/preferences";
import { PanelCollapseIcon, PanelExpandIcon } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";
import { Tooltip } from "@/components/ui/Tooltip";
import styles from "./SideNav.module.css";

/**
 * Whether a nav item should be shown for the current user's roles.
 */
function isVisible(item: NavItem, isAdmin: boolean, canApprove: boolean): boolean {
  if (item.approverVisible) return canApprove;
  if (item.adminOnly) return isAdmin;
  return true;
}

/**
 * Primary app navigation, filtering by admin / approver visibility.
 * @param props.isAdmin - Whether the current user is an app admin.
 * @param props.canApprove - Whether the user may open the Approvals inbox.
 * @remarks When collapsed, labels hide and each link shows a right-side tooltip; section titles are omitted.
 * Approvals shows an unread-count badge from the inbox query when the user can approve.
 */
export function SideNav({ isAdmin, canApprove }: { isAdmin: boolean; canApprove: boolean }) {
  const { navCollapsed, toggleNavCollapsed } = usePreferences();
  const inbox = useApprovalsInbox(canApprove);
  const approvalCount = canApprove ? (inbox.data?.total ?? 0) : 0;

  return (
    <nav
      className={`${styles.nav}${navCollapsed ? ` ${styles.collapsed}` : ""}`}
      aria-label="Main"
      data-collapsed={navCollapsed || undefined}
    >
      <div className={styles.sections}>
        {navigation.map((section, index) => {
          const items = section.items.filter((item) => isVisible(item, isAdmin, canApprove));
          if (items.length === 0) return null;
          return (
            <div key={section.title ?? index} className={styles.section}>
              {section.title && !navCollapsed && <div className={styles.sectionTitle}>{section.title}</div>}
              {items.map((item) => {
                const { label, to, icon: Icon, badge } = item;
                const count = badge === "approvals" ? approvalCount : 0;
                const tipLabel = count > 0 ? `${label} (${count})` : label;
                return (
                  <Tooltip
                    key={to}
                    label={tipLabel}
                    side="right"
                    disabled={!navCollapsed}
                    className={styles.item}
                  >
                    <NavLink
                      to={to}
                      end={to === "/" || item.end === true}
                      aria-label={navCollapsed ? tipLabel : undefined}
                      className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ""}`}
                    >
                      <Icon />
                      {!navCollapsed && <span className={styles.label}>{label}</span>}
                      {count > 0 && (
                        <span className={styles.badge} aria-label={`${count} pending`}>
                          {count > 99 ? "99+" : count}
                        </span>
                      )}
                    </NavLink>
                  </Tooltip>
                );
              })}
            </div>
          );
        })}
      </div>

      <div className={styles.footer}>
        <IconButton
          label={navCollapsed ? "Expand navigation" : "Collapse navigation"}
          tooltipSide={navCollapsed ? "right" : "bottom"}
          onClick={toggleNavCollapsed}
          aria-expanded={!navCollapsed}
          aria-controls="main-navigation"
        >
          {navCollapsed ? <PanelExpandIcon /> : <PanelCollapseIcon />}
        </IconButton>
      </div>
    </nav>
  );
}
