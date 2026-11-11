/**
 * @file Inline loading indicator with visible status text for async UI states.
 */
import styles from "./Spinner.module.css";

/**
 * Animated spinner with a human-readable status label.
 * @param props.label - Status text (default `"Loading"`); an ellipsis is appended in the UI.
 * @remarks Exposed with `role="status"`; the graphic itself is `aria-hidden`.
 */
export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className={styles.wrapper} role="status">
      <span className={styles.spinner} aria-hidden />
      <span>{label}…</span>
    </div>
  );
}
