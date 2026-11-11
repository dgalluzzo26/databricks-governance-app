/**
 * @file Side-nav model for the app shell.
 *
 * Items with `adminOnly: true` are hidden from non-admins in {@link SideNav}; route-level
 * enforcement for those destinations lives in {@link routes} via {@link RequireAdmin}.
 * Keep this list in sync with `routes.tsx` when adding features.
 */
import type { ComponentType, SVGProps } from "react";
import {
  ExceptionIcon,
  ExplorerIcon,
  HelpIcon,
  HomeIcon,
  IdentityIcon,
  MaskRuleIcon,
  PoliciesIcon,
  RowFilterIcon,
  StatusIcon,
} from "@/components/icons";

/** A single navigable destination in the side nav. */
export interface NavItem {
  /** Visible label (also used as tooltip when the nav is collapsed). */
  label: string;
  /** Absolute path for the NavLink. */
  to: string;
  /** Icon component rendered beside the label. */
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  /** When true, the item is shown only to app admins. */
  adminOnly?: boolean;
}

/** Grouped nav items, optionally under a section title. */
export interface NavSection {
  /** Optional section heading omitted when the nav is collapsed. */
  title?: string;
  /** Links in this section. */
  items: NavItem[];
}

/** Side navigation. Each new feature adds its entry here and its route in routes.tsx. */
export const navigation: NavSection[] = [
  { items: [{ label: "Home", to: "/", icon: HomeIcon }] },
  {
    title: "Governance",
    items: [
      { label: "Access explorer", to: "/access-explorer", icon: ExplorerIcon },
      { label: "Identity", to: "/identity", icon: IdentityIcon },
      { label: "Policies", to: "/policies", icon: PoliciesIcon, adminOnly: true },
      { label: "Exceptions", to: "/exceptions", icon: ExceptionIcon, adminOnly: true },
      { label: "Row filters", to: "/row-filters", icon: RowFilterIcon, adminOnly: true },
      { label: "Mask rules", to: "/mask-rules", icon: MaskRuleIcon, adminOnly: true },
    ],
  },
  {
    title: "App",
    items: [
      { label: "Help", to: "/help", icon: HelpIcon },
      { label: "App status", to: "/status", icon: StatusIcon, adminOnly: true },
    ],
  },
];
