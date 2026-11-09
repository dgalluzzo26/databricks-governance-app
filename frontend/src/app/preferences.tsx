import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const THEME_KEY = "dga.theme";
const NAV_KEY = "dga.navCollapsed";

interface Preferences {
  theme: ThemePreference;
  resolvedTheme: ResolvedTheme;
  setTheme: (theme: ThemePreference) => void;
  cycleTheme: () => void;
  navCollapsed: boolean;
  setNavCollapsed: (collapsed: boolean) => void;
  toggleNavCollapsed: () => void;
}

const PreferencesContext = createContext<Preferences | null>(null);

function readStored<T extends string>(key: string, fallback: T, allowed: readonly T[]): T {
  try {
    const value = localStorage.getItem(key);
    if (value && (allowed as readonly string[]).includes(value)) {
      return value as T;
    }
  } catch {
    // Private browsing or disabled storage; fall back.
  }
  return fallback;
}

function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Ignore persistence failures.
  }
}

function systemTheme(): ResolvedTheme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === "system" ? systemTheme() : preference;
}

function applyTheme(theme: ResolvedTheme): void {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

export function PreferencesProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemePreference>(() =>
    readStored(THEME_KEY, "system", ["light", "dark", "system"] as const),
  );
  const [navCollapsed, setNavCollapsedState] = useState(() => readStored(NAV_KEY, "false", ["true", "false"] as const) === "true");
  const [resolvedTheme, setResolvedTheme] = useState<ResolvedTheme>(() => resolveTheme(theme));

  useEffect(() => {
    const next = resolveTheme(theme);
    setResolvedTheme(next);
    applyTheme(next);
    writeStored(THEME_KEY, theme);

    if (theme !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      const system = media.matches ? "dark" : "light";
      setResolvedTheme(system);
      applyTheme(system);
    };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [theme]);

  const setTheme = useCallback((next: ThemePreference) => setThemeState(next), []);
  const cycleTheme = useCallback(() => {
    setThemeState((current) => {
      if (current === "light") return "dark";
      if (current === "dark") return "system";
      return "light";
    });
  }, []);

  const setNavCollapsed = useCallback((collapsed: boolean) => {
    setNavCollapsedState(collapsed);
    writeStored(NAV_KEY, String(collapsed));
  }, []);
  const toggleNavCollapsed = useCallback(() => {
    setNavCollapsedState((current) => {
      const next = !current;
      writeStored(NAV_KEY, String(next));
      return next;
    });
  }, []);

  const value = useMemo(
    () => ({
      theme,
      resolvedTheme,
      setTheme,
      cycleTheme,
      navCollapsed,
      setNavCollapsed,
      toggleNavCollapsed,
    }),
    [theme, resolvedTheme, setTheme, cycleTheme, navCollapsed, setNavCollapsed, toggleNavCollapsed],
  );

  return <PreferencesContext.Provider value={value}>{children}</PreferencesContext.Provider>;
}

export function usePreferences(): Preferences {
  const value = useContext(PreferencesContext);
  if (!value) {
    throw new Error("usePreferences must be used inside <PreferencesProvider>");
  }
  return value;
}
