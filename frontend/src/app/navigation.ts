import type { ComponentType, SVGProps } from "react";
import { HomeIcon, IdentityIcon, PoliciesIcon, RowFilterIcon, StatusIcon } from "@/components/icons";

export interface NavItem {
  label: string;
  to: string;
  icon: ComponentType<SVGProps<SVGSVGElement>>;
  adminOnly?: boolean;
}

export interface NavSection {
  title?: string;
  items: NavItem[];
}

/** Side navigation. Each new feature adds its entry here and its route in routes.tsx. */
export const navigation: NavSection[] = [
  { items: [{ label: "Home", to: "/", icon: HomeIcon }] },
  {
    title: "Governance",
    items: [
      { label: "Policies", to: "/policies", icon: PoliciesIcon },
      { label: "Identity", to: "/identity", icon: IdentityIcon },
      { label: "Row filters", to: "/row-filters", icon: RowFilterIcon },
    ],
  },
  { title: "App", items: [{ label: "App status", to: "/status", icon: StatusIcon }] },
];
