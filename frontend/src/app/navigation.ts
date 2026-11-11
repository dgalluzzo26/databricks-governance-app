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
