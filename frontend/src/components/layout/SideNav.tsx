/**
 * @file Collapsible main side navigation driven by {@link navigation} and admin visibility.
 */
import { NavLink } from "react-router-dom";
import { navigation } from "@/app/navigation";
import { usePreferences } from "@/app/preferences";
import { PanelCollapseIcon, PanelExpandIcon } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";
import { Tooltip } from "@/components/ui/Tooltip";
import styles from "./SideNav.module.css";

/**
 * Primary app navigation, filtering out `adminOnly` items when the user is not an admin.
 * @param props.isAdmin - Whether the current user is an app admin.
 * @remarks When collapsed, labels hide and each link shows a right-side tooltip; section titles are omitted.
 */
export function SideNav({ isAdmin }: { isAdmin: boolean }) {
  const { navCollapsed, toggleNavCollapsed } = usePreferences();

  return (
    <nav
      className={`${styles.nav}${navCollapsed ? ` ${styles.collapsed}` : ""}`}
      aria-label="Main"
      data-collapsed={navCollapsed || undefined}
    >
      <div className={styles.sections}>
        {navigation.map((section, index) => {
          const items = section.items.filter((item) => !item.adminOnly || isAdmin);
          if (items.length === 0) return null;
          return (
            <div key={section.title ?? index} className={styles.section}>
              {section.title && !navCollapsed && <div className={styles.sectionTitle}>{section.title}</div>}
              {items.map(({ label, to, icon: Icon }) => (
                <Tooltip
                  key={to}
                  label={label}
                  side="right"
                  disabled={!navCollapsed}
                  className={styles.item}
                >
                  <NavLink
                    to={to}
                    end={to === "/"}
                    aria-label={navCollapsed ? label : undefined}
                    className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ""}`}
                  >
                    <Icon />
                    {!navCollapsed && <span className={styles.label}>{label}</span>}
                  </NavLink>
                </Tooltip>
              ))}
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
