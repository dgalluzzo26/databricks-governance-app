/**
 * @file Minimal fetch-based JSON/text client for the app's `/api` backend and its typed error.
 *
 * Every non-2xx response is converted to an {@link ApiError} carrying the backend's
 * `{ error: { code, message, details } }` envelope when present.
 */

/** Error envelope returned by the backend for non-2xx responses. All fields are optional because proxies may return other shapes. */
export interface ApiErrorBody {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
}

/**
 * Error thrown by {@link HttpClient} for any non-2xx HTTP response.
 *
 * @remarks
 * `code` is the backend's machine-readable error code, or `"http_error"` when the body was
 * not a JSON error envelope (e.g. an HTML page from the Databricks Apps proxy).
 */
export class ApiError extends Error {
  /**
   * Creates an API error.
   *
   * @param status - HTTP status code of the failed response.
   * @param code - Backend error code, or `"http_error"` when unavailable.
   * @param message - Human-readable message (backend message, status text, or a generic fallback).
   * @param details - Optional structured details from the backend error envelope.
   */
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  /** `true` when the server rejected the request with HTTP 403 (caller lacks the required role). */
  get isForbidden(): boolean {
    return this.status === 403;
  }
}

/** Signature of the injected fetch implementation (overridable in tests). */
type FetchFn = typeof fetch;

/** Thin JSON client for the app's own /api routes. Auth is handled by the Databricks Apps proxy. */
export class HttpClient {
  /**
   * Creates a client.
   *
   * @param baseUrl - Prefix prepended to every request path. Defaults to `"/api"`.
   * @param fetchFn - Fetch implementation; defaults to the global `fetch`.
   */
  constructor(
    private readonly baseUrl = "/api",
    private readonly fetchFn: FetchFn = (...args) => fetch(...args),
  ) {}

  /**
   * Sends a `GET` and parses the JSON response.
   *
   * @param path - Path relative to `baseUrl`, including any query string.
   * @param signal - Optional abort signal (TanStack Query passes one per query).
   * @returns The parsed JSON body, or `undefined` for HTTP 204.
   * @throws {@link ApiError} When the response status is not 2xx.
   */
  get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return this.request<T>("GET", path, undefined, signal);
  }

  /**
   * Sends a `GET` and returns the raw response body as text (e.g. Markdown help docs).
   *
   * @param path - Path relative to `baseUrl`.
   * @param signal - Optional abort signal.
   * @returns The response body as a string.
   * @throws {@link ApiError} When the response status is not 2xx.
   */
  getText(path: string, signal?: AbortSignal): Promise<string> {
    return this.requestText("GET", path, signal);
  }

  /**
   * Sends a `POST` with an optional JSON body.
   *
   * @param path - Path relative to `baseUrl`.
   * @param body - Value to JSON-serialize; omit to send no body.
   * @returns The parsed JSON body, or `undefined` for HTTP 204.
   * @throws {@link ApiError} When the response status is not 2xx.
   */
  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  /**
   * Sends a `PUT` with an optional JSON body (used for upserts).
   *
   * @param path - Path relative to `baseUrl`.
   * @param body - Value to JSON-serialize; omit to send no body.
   * @returns The parsed JSON body, or `undefined` for HTTP 204.
   * @throws {@link ApiError} When the response status is not 2xx.
   */
  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("PUT", path, body);
  }

  /**
   * Sends a `DELETE`.
   *
   * @param path - Path relative to `baseUrl`, including any query string.
   * @returns The parsed JSON body, or `undefined` for HTTP 204.
   * @throws {@link ApiError} When the response status is not 2xx.
   */
  delete<T>(path: string): Promise<T> {
    return this.request<T>("DELETE", path);
  }

  /**
   * Performs a same-origin JSON request.
   *
   * @remarks
   * `Content-Type: application/json` is only sent when a body is provided. A 204 response
   * resolves to `undefined` cast to `T`.
   *
   * @param method - HTTP method.
   * @param path - Path relative to `baseUrl`.
   * @param body - Optional value to JSON-serialize.
   * @param signal - Optional abort signal.
   * @returns The parsed JSON body, or `undefined` for HTTP 204.
   * @throws {@link ApiError} When the response status is not 2xx.
   */
  private async request<T>(method: string, path: string, body?: unknown, signal?: AbortSignal): Promise<T> {
    const response = await this.fetchFn(`${this.baseUrl}${path}`, {
      method,
      signal,
      credentials: "same-origin",
      headers: body === undefined ? { Accept: "application/json" } : { "Content-Type": "application/json", Accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!response.ok) {
      throw await HttpClient.toError(response);
    }
    if (response.status === 204) {
      return undefined as T;
    }
    return (await response.json()) as T;
  }

  /**
   * Performs a same-origin request that accepts plain text or Markdown.
   *
   * @param method - HTTP method.
   * @param path - Path relative to `baseUrl`.
   * @param signal - Optional abort signal.
   * @returns The response body as a string.
   * @throws {@link ApiError} When the response status is not 2xx.
   */
  private async requestText(method: string, path: string, signal?: AbortSignal): Promise<string> {
    const response = await this.fetchFn(`${this.baseUrl}${path}`, {
      method,
      signal,
      credentials: "same-origin",
      headers: { Accept: "text/plain, text/markdown, */*" },
    });
    if (!response.ok) {
      throw await HttpClient.toError(response);
    }
    return response.text();
  }

  /**
   * Builds an {@link ApiError} from a failed response, reading the backend error envelope if the body is JSON.
   *
   * @param response - The non-2xx response.
   * @returns An error whose message falls back to the status text, then to a generic `Request failed (<status>)`.
   */
  private static async toError(response: Response): Promise<ApiError> {
    let body: ApiErrorBody = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // Non-JSON error (e.g. proxy page); fall back to the status text.
    }
    return new ApiError(
      response.status,
      body.error?.code ?? "http_error",
      body.error?.message ?? (response.statusText || `Request failed (${response.status})`),
      body.error?.details,
    );
  }
}
