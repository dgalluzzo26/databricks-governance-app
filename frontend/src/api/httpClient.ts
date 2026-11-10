export interface ApiErrorBody {
  error?: { code?: string; message?: string; details?: Record<string, unknown> };
}

export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
    this.name = "ApiError";
  }

  get isForbidden(): boolean {
    return this.status === 403;
  }
}

type FetchFn = typeof fetch;

/** Thin JSON client for the app's own /api routes. Auth is handled by the Databricks Apps proxy. */
export class HttpClient {
  constructor(
    private readonly baseUrl = "/api",
    private readonly fetchFn: FetchFn = (...args) => fetch(...args),
  ) {}

  get<T>(path: string, signal?: AbortSignal): Promise<T> {
    return this.request<T>("GET", path, undefined, signal);
  }

  getText(path: string, signal?: AbortSignal): Promise<string> {
    return this.requestText("GET", path, signal);
  }

  post<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  put<T>(path: string, body?: unknown): Promise<T> {
    return this.request<T>("PUT", path, body);
  }

  delete<T>(path: string): Promise<T> {
    return this.request<T>("DELETE", path);
  }

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
