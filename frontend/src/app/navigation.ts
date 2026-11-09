import type { ComponentType, SVGProps } from "react";
import { HomeIcon, StatusIcon } from "@/components/icons";

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
  { title: "App", items: [{ label: "App status", to: "/status", icon: StatusIcon }] },
];
