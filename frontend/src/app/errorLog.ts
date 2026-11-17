/**
 * @file In-browser log of errors seen this session, for the Activity panel.
 *
 * The server keeps failed requests it handled, but some failures never reach it (network
 * drops, proxy timeouts) and render errors happen only in the browser. React Query's global
 * error callbacks and the route error boundary record them here.
 */
import { useSyncExternalStore } from "react";
import { describeError } from "@/api/errors";

/** Most entries kept; older ones are dropped. */
export const MAX_ENTRIES = 50;

/** Where an error was caught. */
export type ErrorSource = "query" | "mutation" | "render";

/** One recorded error. */
export interface SessionError {
  id: number;
  at: string;
  source: ErrorSource;
  /** What was being done, for example a mutation's busy message or a query key. */
  label: string;
  title: string;
  message: string;
  code: string | null;
  status: number | null;
  requestId: string | null;
}

let entries: SessionError[] = [];
let nextId = 1;
const listeners = new Set<() => void>();

function emit(): void {
  for (const listener of listeners) listener();
}

/**
 * Records an error (newest first). Aborted requests and 403s from role probes are skipped.
 *
 * @param error - The thrown value.
 * @param source - Where it was caught.
 * @param label - What was being done.
 * @returns The stored entry, or null when skipped.
 */
export function recordError(error: unknown, source: ErrorSource, label: string): SessionError | null {
  if (error instanceof DOMException && error.name === "AbortError") return null;
  const described = describeError(error);
  if (source === "query" && described.status === 403) return null;
  const entry: SessionError = {
    id: nextId++,
    at: new Date().toISOString(),
    source,
    label,
    title: described.title,
    message: described.message,
    code: described.code,
    status: described.status,
    requestId: described.requestId,
  };
  entries = [entry, ...entries].slice(0, MAX_ENTRIES);
  emit();
  return entry;
}

/** Removes every recorded error. */
export function clearErrors(): void {
  entries = [];
  emit();
}

/** Current entries, newest first. */
export function sessionErrors(): SessionError[] {
  return entries;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Subscribes a component to the session error log. */
export function useSessionErrors(): SessionError[] {
  return useSyncExternalStore(subscribe, sessionErrors, sessionErrors);
}

/** Human-readable label for a query key, for example `performance · summary · 24`. */
export function queryLabel(queryKey: readonly unknown[]): string {
  return queryKey
    .filter((part) => part !== null && part !== undefined && part !== "")
    .map((part) => (typeof part === "object" ? JSON.stringify(part) : String(part)))
    .join(" · ")
    .slice(0, 120);
}
