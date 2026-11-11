/**
 * @file Shared 16×16 stroke icons and the Databricks logo used in nav and chrome.
 */
import type { SVGProps } from "react";

/** Shared SVG prop bag for stroke icons in this module. */
type IconProps = SVGProps<SVGSVGElement>;

/** Base SVG wrapper: 16×16, currentColor stroke, decorative (`aria-hidden`). */
function Icon({ children, ...props }: IconProps) {
  return (
    <svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5"
      strokeLinecap="round" strokeLinejoin="round" aria-hidden {...props}>
      {children}
    </svg>
  );
}

/** Home / dashboard nav icon. */
export const HomeIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 7 8 2.5 13.5 7v6.5h-4v-4h-3v4h-4V7Z" />
  </Icon>
);

/** App status / health nav icon. */
export const StatusIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="8" cy="8" r="5.5" />
    <path d="M8 5v3.2l2 1.3" />
  </Icon>
);

/** Policies list nav icon. */
export const PoliciesIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 3.5h10v9H3z" />
    <path d="M5.5 6.5h5M5.5 9h3.5" />
  </Icon>
);

/** Identity / people nav icon. */
export const IdentityIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="8" cy="5.5" r="2.5" />
    <path d="M3.5 13.5c0-2.5 2-4 4.5-4s4.5 1.5 4.5 4" />
  </Icon>
);

/** Column mask rules nav icon. */
export const MaskRuleIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M3 8h10M5.5 4.5 3 8l2.5 3.5M10.5 4.5 13 8l-2.5 3.5" />
  </Icon>
);

/** Row filters nav icon. */
export const RowFilterIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 3.5h11L9.5 8.5v4l-3 1.5V8.5z" />
  </Icon>
);

/** Access explorer / search nav icon. */
export const ExplorerIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="7" cy="7" r="4" />
    <path d="m10 10 3.5 3.5" />
  </Icon>
);

/** Help / documentation nav icon. */
export const HelpIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="8" cy="8" r="5.5" />
    <path d="M6.2 6.2a1.9 1.9 0 1 1 2.6 2.5c-.6.4-1 .8-1 1.5M8 11.8v.2" />
  </Icon>
);

/** Policy exceptions / warning nav icon. */
export const ExceptionIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M8 2.5 13.5 13.5h-11L8 2.5Z" />
    <path d="M8 6.5v3.2M8 11.5v.2" />
  </Icon>
);

/** External-link affordance (opens outside the app). */
export const ExternalIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M9 2.5h4.5V7M13.5 2.5 7.5 8.5M11.5 9.5v4h-9v-9h4" />
  </Icon>
);

/** Downward chevron for disclosure controls. */
export const ChevronDownIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="m4 6 4 4 4-4" />
  </Icon>
);

/** Collapse side panel / nav control. */
export const PanelCollapseIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 3h11v10h-11z" />
    <path d="M6 3v10" />
    <path d="m9.5 6.5-2 1.5 2 1.5" />
  </Icon>
);

/** Expand side panel / nav control. */
export const PanelExpandIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M2.5 3h11v10h-11z" />
    <path d="M6 3v10" />
    <path d="m7.5 6.5 2 1.5-2 1.5" />
  </Icon>
);

/** Light theme indicator. */
export const SunIcon = (p: IconProps) => (
  <Icon {...p}>
    <circle cx="8" cy="8" r="2.5" />
    <path d="M8 2.5v1.5M8 12v1.5M2.5 8H4M12 8h1.5M4.2 4.2l1.1 1.1M10.7 10.7l1.1 1.1M4.2 11.8l1.1-1.1M10.7 5.3l1.1-1.1" />
  </Icon>
);

/** Dark theme indicator. */
export const MoonIcon = (p: IconProps) => (
  <Icon {...p}>
    <path d="M12.5 9.2A4.8 4.8 0 0 1 6.8 3.5 5.2 5.2 0 1 0 12.5 9.2Z" />
  </Icon>
);

/** System / follow-OS theme indicator. */
export const SystemThemeIcon = (p: IconProps) => (
  <Icon {...p}>
    <rect x="2.5" y="3.5" width="11" height="8" rx="1.2" />
    <path d="M6 13.5h4M8 11.5v2" />
  </Icon>
);

/**
 * Databricks brand mark for the top bar.
 * @param props.size - Width and height in CSS pixels (default 22).
 */
export function DatabricksLogo({ size = 22 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" aria-hidden>
      <path fill="#ff3621" d="M16 3 3 10.2v3.6l13 7.2 13-7.2v-3.6L16 3Zm0 21.6L3 17.4V21l13 7.2L29 21v-3.6l-13 7.2Z" />
    </svg>
  );
}
