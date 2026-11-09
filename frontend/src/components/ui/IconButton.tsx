import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Tooltip } from "./Tooltip";
import styles from "./IconButton.module.css";

interface IconButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  label: string;
  children: ReactNode;
  tooltipSide?: "right" | "bottom";
}

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
