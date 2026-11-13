/**
 * @file Admin-only demo role preview: view the app as a plain user or dummy approver.
 *
 * Overlays `isAdmin` / `isApprover` for UI gating (nav, route guards, page feature gates).
 * API calls still run as the signed-in admin; this is not server-side impersonation.
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useCurrentUser } from "@/api/queries";
import type { CurrentUser } from "@/api/types";

/** Demo preview mode. `"admin"` means no overlay (real roles). */
export type ViewAsMode = "admin" | "user" | "approver";

const VIEW_AS_KEY = "dga.viewAs";
const VIEW_AS_MODES = ["admin", "user", "approver"] as const;

/** Context value for {@link ViewAsProvider}. */
interface ViewAsContextValue {
  /** Current preview mode. */
  viewAs: ViewAsMode;
  /** Set preview mode; no-op when the real user is not an admin. */
  setViewAs: (mode: ViewAsMode) => void;
  /** True when a non-admin preview is active. */
  isPreviewing: boolean;
}

const ViewAsContext = createContext<ViewAsContextValue | null>(null);

/**
 * Apply a view-as overlay to a real {@link CurrentUser}.
 *
 * @param user - Identity from `GET /me`.
 * @param mode - Preview mode; `"admin"` returns `user` unchanged.
 */
export function applyViewAs(user: CurrentUser, mode: ViewAsMode): CurrentUser {
  if (mode === "admin") return user;
  if (mode === "user") {
    return { ...user, isAdmin: false, isApprover: false };
  }
  return { ...user, isAdmin: false, isApprover: true };
}

function readStoredViewAs(): ViewAsMode {
  try {
    const value = sessionStorage.getItem(VIEW_AS_KEY);
    if (value && (VIEW_AS_MODES as readonly string[]).includes(value)) {
      return value as ViewAsMode;
    }
  } catch {
    // Private browsing / disabled storage.
  }
  return "admin";
}

function writeStoredViewAs(mode: ViewAsMode): void {
  try {
    if (mode === "admin") {
      sessionStorage.removeItem(VIEW_AS_KEY);
    } else {
      sessionStorage.setItem(VIEW_AS_KEY, mode);
    }
  } catch {
    // Ignore persistence failures.
  }
}

/**
 * Provides demo view-as mode to the subtree.
 *
 * @param props.children - App tree that may call {@link useViewAs} / {@link useEffectiveUser}.
 */
export function ViewAsProvider({ children }: { children: ReactNode }) {
  const [viewAs, setViewAsState] = useState<ViewAsMode>(readStoredViewAs);
  const { data: realUser } = useCurrentUser();

  const setViewAs = useCallback(
    (mode: ViewAsMode) => {
      if (!realUser?.isAdmin) return;
      setViewAsState(mode);
      writeStoredViewAs(mode);
    },
    [realUser?.isAdmin],
  );

  // Non-admins cannot stay in a leftover preview from a previous session as admin.
  const effectiveMode: ViewAsMode = realUser && !realUser.isAdmin ? "admin" : viewAs;

  const value = useMemo(
    () => ({
      viewAs: effectiveMode,
      setViewAs,
      isPreviewing: effectiveMode !== "admin",
    }),
    [effectiveMode, setViewAs],
  );

  return <ViewAsContext.Provider value={value}>{children}</ViewAsContext.Provider>;
}

/**
 * Reads view-as mode from the nearest {@link ViewAsProvider}.
 *
 * @throws If called outside {@link ViewAsProvider}.
 */
export function useViewAs(): ViewAsContextValue {
  const value = useContext(ViewAsContext);
  if (!value) {
    throw new Error("useViewAs must be used inside <ViewAsProvider>");
  }
  return value;
}

/**
 * Current user with demo role flags overlaid for UI gating.
 *
 * Same query shape as {@link useCurrentUser}; use this anywhere nav, guards, or pages
 * decide features from `isAdmin` / `isApprover`. Keep {@link useCurrentUser} for the
 * real-admin check (e.g. showing the View as control).
 */
export function useEffectiveUser() {
  const query = useCurrentUser();
  const { viewAs } = useViewAs();
  const data = query.data ? applyViewAs(query.data, viewAs) : query.data;
  return { ...query, data };
}

/** Human label for a view-as mode (banner / menu). */
export function viewAsLabel(mode: ViewAsMode): string {
  if (mode === "user") return "User";
  if (mode === "approver") return "Approver";
  return "Admin";
}

/**
 * Whether a path is admin-only (should redirect away under user/approver preview).
 *
 * @param pathname - Router pathname (no search).
 */
export function isAdminOnlyPath(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, "") || "/";
  const adminPrefixes = [
    "/policies",
    "/exceptions",
    "/row-filters",
    "/mask-rules",
    "/data-access",
    "/performance",
    "/recovery",
    "/status",
  ];
  return adminPrefixes.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}
