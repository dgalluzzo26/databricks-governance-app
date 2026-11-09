import { usePreferences, type ThemePreference } from "@/app/preferences";
import { MoonIcon, SunIcon, SystemThemeIcon } from "@/components/icons";
import { IconButton } from "@/components/ui/IconButton";

const LABELS: Record<ThemePreference, string> = {
  light: "Light theme (click for dark)",
  dark: "Dark theme (click for system)",
  system: "System theme (click for light)",
};

export function ThemeToggle() {
  const { theme, cycleTheme } = usePreferences();
  const Icon = theme === "light" ? SunIcon : theme === "dark" ? MoonIcon : SystemThemeIcon;

  return (
    <IconButton label={LABELS[theme]} onClick={cycleTheme} aria-pressed={theme !== "system"}>
      <Icon />
    </IconButton>
  );
}
