/**
 * @file CSS hover/focus tooltip wrapper used by icon-only chrome controls.
 */
import type { ReactNode } from "react";
import styles from "./Tooltip.module.css";

/** Preferred tooltip placement relative to the trigger. */
type Side = "right" | "bottom";

/**
 * Wraps children with a decorative tooltip label.
 * @param props.label - Text shown in the tooltip.
 * @param props.side - Placement of the tooltip (default `"right"`).
 * @param props.disabled - When true, renders children only with no tooltip chrome.
 * @param props.className - Optional class on the wrapper span.
 * @param props.children - Trigger element that receives hover/focus.
 * @remarks Uses `role="tooltip"`; not a focus-managed popup (CSS-driven visibility).
 */
export function Tooltip({
  label,
  side = "right",
  disabled = false,
  className,
  children,
}: {
  label: string;
  side?: Side;
  disabled?: boolean;
  className?: string;
  children: ReactNode;
}) {
  if (disabled) {
    return <>{children}</>;
  }

  const classes = [styles.wrapper, styles[side], className].filter(Boolean).join(" ");
  return (
    <span className={classes}>
      {children}
      <span className={styles.tooltip} role="tooltip">
        {label}
      </span>
    </span>
  );
}
