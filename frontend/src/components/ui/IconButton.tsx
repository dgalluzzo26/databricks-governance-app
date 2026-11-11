/**
 * @file Accessible icon-only button with an always-on tooltip for its label.
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Tooltip } from "./Tooltip";
import styles from "./IconButton.module.css";

/** Props for {@link IconButton}; extends native button attributes. */
interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Accessible name and tooltip text. */
  label: string;
  /** Icon (or other content) rendered inside the button. */
  children: ReactNode;
  /** Tooltip placement (default `"bottom"`). */
  tooltipSide?: "right" | "bottom";
}

/**
 * Square icon button that exposes `label` via `aria-label` and a {@link Tooltip}.
 * @param props - See {@link IconButtonProps}; remaining props pass through to the button.
 */
export function IconButton({ label, children, tooltipSide = "bottom", className, ...props }: IconButtonProps) {
  return (
    <Tooltip label={label} side={tooltipSide}>
      <button
        type="button"
        aria-label={label}
        className={`${styles.button}${className ? ` ${className}` : ""}`}
        {...props}
      >
        {children}
      </button>
    </Tooltip>
  );
}
