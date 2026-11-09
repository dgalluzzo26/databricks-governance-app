import { NavLink } from "react-router-dom";
import { navigation } from "@/app/navigation";
import styles from "./SideNav.module.css";

export function SideNav({ isAdmin }: { isAdmin: boolean }) {
  return (
    <nav className={styles.nav} aria-label="Main">
      {navigation.map((section, index) => {
        const items = section.items.filter((item) => !item.adminOnly || isAdmin);
        if (items.length === 0) return null;
        return (
          <div key={section.title ?? index} className={styles.section}>
            {section.title && <div className={styles.sectionTitle}>{section.title}</div>}
            {items.map(({ label, to, icon: Icon }) => (
              <NavLink
                key={to}
                to={to}
                end={to === "/"}
                className={({ isActive }) => `${styles.link} ${isActive ? styles.active : ""}`}
              >
                <Icon />
                {label}
              </NavLink>
            ))}
          </div>
        );
      })}
    </nav>
  );
}
