/**
 * @file Persisted UI preferences: color theme and side-nav collapsed state.
 *
 * Theme preference (`dga.theme`) may be light, dark, or system; the resolved light/dark value
 * is applied to `document.documentElement`. Nav collapse (`dga.navCollapsed`) survives reloads.
 * Both keys are written best-effort so private browsing / disabled storage still works.
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/** User-selected theme mode, including follow-system. */
export type ThemePreference = "light" | "dark" | "system";

/** Concrete theme applied to the document (system preference resolved). */
export type ResolvedTheme = "light" | "dark";

/** localStorage key for the theme preference. */
const THEME_KEY = "dga.theme";

/** localStorage key for whether the side nav is collapsed (`"true"` / `"false"`). */
const NAV_KEY = "dga.navCollapsed";

/** Context value exposed by {@link PreferencesProvider}. */
interface Preferences {
  /** Stored theme preference (may be `"system"`). */
  theme: ThemePreference;
  /** Effective light/dark theme after resolving system preference. */
  resolvedTheme: ResolvedTheme;
  /** Persist and apply a theme preference. */
  setTheme: (theme: ThemePreference) => void;
  /** Cycle light → dark → system → light. */
  cycleTheme: () => void;
  /** Whether the side navigation is collapsed to icons-only. */
  navCollapsed: boolean;
  /** Persist the side-nav collapsed flag. */
  setNavCollapsed: (collapsed: boolean) => void;
  /** Toggle and persist the side-nav collapsed flag. */
  toggleNavCollapsed: () => void;
}

/** React context holding theme and nav-collapse preferences; `null` outside the provider. */
const PreferencesContext = createContext<Preferences | null>(null);

/**
 * Reads a constrained string from localStorage, returning `fallback` on miss or failure.
 * @param key - Storage key.
 * @param fallback - Value when missing, invalid, or storage throws.
 * @param allowed - Whitelist of acceptable stored values.
 */
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

/**
 * Best-effort localStorage write; ignores quota / privacy errors.
 * @param key - Storage key.
 * @param value - Value to persist.
 */
function writeStored(key: string, value: string): void {
  try {
    localStorage.setItem(key, value);
  } catch {
    // Ignore persistence failures.
  }
}

/** Resolves the OS color-scheme preference to light or dark. */
function systemTheme(): ResolvedTheme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/**
 * Maps a theme preference to a concrete light/dark value.
 * @param preference - Stored preference including `"system"`.
 */
function resolveTheme(preference: ThemePreference): ResolvedTheme {
  return preference === "system" ? systemTheme() : preference;
}

/**
 * Applies the resolved theme to the document root for CSS and native form controls.
 * @param theme - Concrete light or dark theme.
 */
function applyTheme(theme: ResolvedTheme): void {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
}

/**
 * Provides theme and nav-collapse preferences to the subtree.
 * @param props.children - App tree that may call {@link usePreferences}.
 */
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

/**
 * Reads UI preferences from the nearest {@link PreferencesProvider}.
 * @throws If called outside {@link PreferencesProvider}.
 */
export function usePreferences(): Preferences {
  const value = useContext(PreferencesContext);
  if (!value) {
    throw new Error("usePreferences must be used inside <PreferencesProvider>");
  }
  return value;
}
