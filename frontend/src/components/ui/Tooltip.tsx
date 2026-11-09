import type { ReactNode } from "react";
import styles from "./Tooltip.module.css";

type Side = "right" | "bottom";

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
