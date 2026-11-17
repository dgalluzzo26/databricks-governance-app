/**
 * @file Turns thrown values into a friendly title, message, hint, and reference ID for the UI.
 */
import { ApiError } from "./httpClient";

/** What the UI shows for one error. */
export interface ErrorDescription {
  /** Short heading, for example "SQL warehouse is not ready". */
  title: string;
  /** The server's message (or the thrown error's). */
  message: string;
  /** Optional next step for the user. */
  hint: string | null;
  /** Server request ID to quote when reporting the problem. */
  requestId: string | null;
  /** Backend error code, `http_error`, or `network_error`; null for non-API errors. */
  code: string | null;
  /** HTTP status, 0 for network failures; null for non-API errors. */
  status: number | null;
}

/** Friendly copy per backend error code. */
const BY_CODE: Record<string, { title: string; hint: string | null }> = {
  warehouse_unavailable: {
    title: "SQL warehouse is not ready",
    hint: "The warehouse is starting or recovering. Try again in a minute.",
  },
  timeout: {
    title: "The request took too long",
    hint: "The query ran past the time limit. Try again, or narrow what you asked for.",
  },
  upstream_error: { title: "Databricks returned an error", hint: null },
  forbidden: { title: "You do not have access", hint: "Ask an app admin if you need this page or action." },
  not_found: { title: "Not found", hint: null },
  invalid_request: { title: "Check your input", hint: null },
  not_configured: { title: "Not configured", hint: "An app admin needs to finish setup for this feature." },
  unauthorized: { title: "Signed out", hint: "Reload the page to sign in again." },
  network_error: {
    title: "Could not reach the app",
    hint: "Check your connection or VPN, then try again.",
  },
  internal_error: { title: "Unexpected server error", hint: "Try again. If it keeps failing, report the reference ID." },
};

/**
 * Describes an error for display.
 *
 * @param error - Anything thrown (usually an {@link ApiError}).
 * @param fallbackTitle - Title for non-API errors and unknown codes.
 * @returns Title, message, optional hint, and reference ID.
 */
export function describeError(error: unknown, fallbackTitle = "Something went wrong"): ErrorDescription {
  if (error instanceof ApiError) {
    const known = BY_CODE[error.code];
    if (known) {
      return { ...known, message: error.message, requestId: error.requestId, code: error.code, status: error.status };
    }
    if (error.code === "http_error" && (error.status === 502 || error.status === 504)) {
      return {
        title: "The app did not respond in time",
        message: error.message,
        hint: "Long operations may still finish in the background; check the Activity panel.",
        requestId: error.requestId,
        code: error.code,
        status: error.status,
      };
    }
    return {
      title: fallbackTitle,
      message: error.message,
      hint: null,
      requestId: error.requestId,
      code: error.code,
      status: error.status,
    };
  }
  const message = error instanceof Error ? error.message : String(error);
  return { title: fallbackTitle, message, hint: null, requestId: null, code: null, status: null };
}
