/**
 * @file Icon button that cycles the persisted theme preference (light → dark → system).
 */
import { usePreferences, type ThemePreference } from "@/app/preferences";
import { MoonIcon, SunIcon, SystemThemeIcon } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";

/** Accessible labels describing the current theme and the next click action. */
const LABELS: Record<ThemePreference, string> = {
  light: "Light theme (click for dark)",
  dark: "Dark theme (click for system)",
  system: "System theme (click for light)",
};

/**
 * Cycles theme preference via {@link usePreferences}.
 * @remarks `aria-pressed` is true when a fixed light/dark theme is selected (not system).
 */
export function ThemeToggle() {
  const { theme, cycleTheme } = usePreferences();
  const Icon = theme === "light" ? SunIcon : theme === "dark" ? MoonIcon : SystemThemeIcon;

  return (
    <IconButton label={LABELS[theme]} onClick={cycleTheme} aria-pressed={theme !== "system"}>
      <Icon />
    </IconButton>
  );
}
