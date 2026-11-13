/**
 * @file Sticky banner shown while an admin is demoing as User or Approver.
 */
import { useViewAs, viewAsLabel } from "@/app/viewAs";
import styles from "./ViewAsBanner.module.css";

/**
 * Exit control for demo role preview.
 *
 * @remarks Renders nothing when not previewing. Always visible above the main chrome so
 * the admin can leave preview without hunting for admin-only UI.
 */
export function ViewAsBanner() {
  const { viewAs, isPreviewing, setViewAs } = useViewAs();
  if (!isPreviewing) return null;

  return (
    <div className={styles.banner} role="status">
      <span>
        Viewing as {viewAsLabel(viewAs)} <span className={styles.hint}>(demo)</span>
      </span>
      <button type="button" className={styles.exit} onClick={() => setViewAs("admin")}>
        Exit preview
      </button>
    </div>
  );
}
