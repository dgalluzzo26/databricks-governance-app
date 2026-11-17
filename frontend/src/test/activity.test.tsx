import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { describeError } from "@/api/errors";
import { ApiError, HttpClient } from "@/api/httpClient";
import type { RequestRecord } from "@/api/types";
import { createQueryClient } from "@/app/App";
import { clearErrors, MAX_ENTRIES, queryLabel, recordError, sessionErrors } from "@/app/errorLog";
import { CLEARED_KEY, mergeErrors, SEEN_KEY } from "@/components/activity/ActivityPanel";
import { RouteErrorPage } from "@/pages/RouteErrorPage";
import { fakeServices, renderApp, testActivityFeed, testFailedTask, testRunningTask } from "./renderApp";

const failedRequest: RequestRecord = {
  recordedAt: "2026-11-12T11:50:00Z",
  method: "POST",
  route: "/api/row-filters/pack/ensure",
  activity: "Sync row-filter policy pack",
  statusCode: 504,
  ok: false,
  durationMs: 120_000,
  userName: "admin@example.com",
  requestId: "req-server-01",
  errorCode: "timeout",
  errorMessage: "Statement timed out after 120 s",
};

beforeEach(() => {
  clearErrors();
  localStorage.clear();
});

afterEach(() => vi.useRealTimers());

describe("describeError", () => {
  it("uses friendly copy for known codes and keeps the reference", () => {
    const described = describeError(
      new ApiError(503, "warehouse_unavailable", "Warehouse wh is STARTING", {}, "req-1"),
    );
    expect(described).toMatchObject({
      title: "SQL warehouse is not ready",
      message: "Warehouse wh is STARTING",
      requestId: "req-1",
      code: "warehouse_unavailable",
      status: 503,
    });
    expect(described.hint).toMatch(/Try again in a minute/);
  });

  it("explains proxy timeouts and falls back for unknown errors", () => {
    expect(describeError(new ApiError(504, "http_error", "Gateway Timeout")).title).toBe(
      "The app did not respond in time",
    );
    expect(describeError(new ApiError(418, "teapot", "short and stout"), "Could not brew").title).toBe(
      "Could not brew",
    );
    expect(describeError("boom")).toMatchObject({ title: "Something went wrong", message: "boom", code: null });
  });
});

describe("HttpClient request IDs and network failures", () => {
  it("reads the request ID from the error body, or the response header", async () => {
    const withBody = (() =>
      Promise.resolve(
        new Response(JSON.stringify({ error: { code: "timeout", message: "slow", requestId: "req-body" } }), {
          status: 504,
          headers: { "Content-Type": "application/json" },
        }),
      )) as typeof fetch;
    await expect(new HttpClient("/api", withBody).get("/x")).rejects.toMatchObject({ requestId: "req-body" });

    const withHeader = (() =>
      Promise.resolve(new Response("<html>", { status: 502, headers: { "x-request-id": "req-header" } }))) as typeof fetch;
    await expect(new HttpClient("/api", withHeader).get("/x")).rejects.toMatchObject({
      code: "http_error",
      requestId: "req-header",
    });
  });

  it("turns a failed connection into a network_error", async () => {
    const offline = (() => Promise.reject(new TypeError("Failed to fetch"))) as typeof fetch;
    await expect(new HttpClient("/api", offline).get("/x")).rejects.toMatchObject({
      status: 0,
      code: "network_error",
    });
  });

  it("passes aborts through untouched", async () => {
    const aborted = (() => Promise.reject(new DOMException("aborted", "AbortError"))) as typeof fetch;
    const error = await new HttpClient("/api", aborted).get("/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(DOMException);
  });
});

describe("session error log", () => {
  it("records newest first, skips aborts and query 403s, and caps the list", () => {
    expect(recordError(new DOMException("aborted", "AbortError"), "query", "x")).toBeNull();
    expect(recordError(new ApiError(403, "forbidden", "Admins only"), "query", "probe")).toBeNull();
    expect(recordError(new ApiError(403, "forbidden", "Admins only"), "mutation", "Approve")).not.toBeNull();
    recordError(new Error("second"), "render", "/page");
    expect(sessionErrors().map((entry) => entry.message)).toEqual(["second", "Admins only"]);

    for (let i = 0; i < MAX_ENTRIES + 5; i++) recordError(new Error(`e${i}`), "query", "q");
    expect(sessionErrors()).toHaveLength(MAX_ENTRIES);
    clearErrors();
    expect(sessionErrors()).toEqual([]);
  });

  it("labels query keys readably", () => {
    expect(queryLabel(["performance", "summary", 24, null, { page: 1 }])).toBe('performance · summary · 24 · {"page":1}');
  });

  it("captures failed queries and mutations through the app's query client", async () => {
    const client = createQueryClient();
    await client
      .fetchQuery({ queryKey: ["broken", "query"], queryFn: () => Promise.reject(new Error("query failed")), retry: false })
      .catch(() => undefined);
    await client
      .fetchQuery({
        queryKey: ["quiet"],
        queryFn: () => Promise.reject(new Error("silent failure")),
        retry: false,
        meta: { silent: true },
      })
      .catch(() => undefined);
    const mutation = client.getMutationCache().build(client, {
      mutationFn: () => Promise.reject(new Error("mutation failed")),
      meta: { busyMessage: "Saving mask rule" },
    });
    await mutation.execute(undefined).catch(() => undefined);

    const errors = sessionErrors();
    expect(errors.map((entry) => entry.message)).toEqual(["mutation failed", "query failed"]);
    expect(errors[0]).toMatchObject({ source: "mutation", label: "Saving mask rule" });
    expect(errors[1]).toMatchObject({ source: "query", label: "broken · query" });
  });
});

describe("mergeErrors", () => {
  it("dedupes by request ID, hides errors behind failed tasks, and respects Clear", () => {
    const session = [
      recordError(new ApiError(504, "timeout", "dupe", {}, "req-server-01"), "mutation", "Sync")!,
      recordError(new ApiError(503, "warehouse_unavailable", "task dupe", {}, testFailedTask.requestId), "mutation", "Apply")!,
      recordError(new Error("browser only"), "render", "/page")!,
    ];
    const taskRequest = { ...failedRequest, requestId: testFailedTask.requestId, recordedAt: "2026-11-12T11:40:30Z" };

    const merged = mergeErrors([failedRequest, taskRequest], session, [testFailedTask], 0);
    expect(merged.map((entry) => entry.message)).toEqual(["browser only", "Statement timed out after 120 s"]);
    expect(merged[0]?.title).toBe("Something went wrong · /page");

    expect(mergeErrors([failedRequest], [], [], new Date("2026-11-12T12:00:00Z").getTime())).toEqual([]);
  });
});

describe("Activity panel", () => {
  it("shows the running count, task steps, failures, and closes on Escape", async () => {
    const user = userEvent.setup();
    renderApp(
      "/",
      fakeServices({
        activity: {
          tasks: { items: [testRunningTask, testFailedTask], total: 2, page: 1, pageSize: 30, running: 1, failed: 1 },
          errors: { items: [failedRequest], total: 1, page: 1, pageSize: 30, succeeded: 0, failed: 1 },
        },
      }),
    );
    const trigger = await screen.findByRole("button", { name: "Activity: 1 running" });
    await user.click(trigger);

    const panel = screen.getByRole("complementary", { name: "Activity" });
    expect(within(panel).getByRole("heading", { name: "Activity" })).toHaveFocus();
    expect(within(panel).getByText("Sync mask policy pack")).toBeInTheDocument();
    const steps = within(panel).getByRole("list", { name: "Steps for Sync mask policy pack" });
    expect(within(steps).getByText("Recreate column mask policy")).toBeInTheDocument();
    expect(within(panel).getAllByText("SQL warehouse governance-wh is starting").length).toBeGreaterThan(0);
    expect(within(panel).getByText("Ref req-failed-01")).toBeInTheDocument();
    expect(within(panel).getByText("Sync row-filter policy pack")).toBeInTheDocument();
    expect(within(panel).getAllByRole("link", { name: "Open page" })[0]).toHaveAttribute("href", "/mask-rules");

    await user.keyboard("{Escape}");
    expect(screen.queryByRole("complementary", { name: "Activity" })).not.toBeInTheDocument();
    expect(Number(localStorage.getItem(SEEN_KEY))).toBeGreaterThan(0);
  });

  it("badges unseen problems and clears the error list", async () => {
    const user = userEvent.setup();
    renderApp(
      "/",
      fakeServices({
        activity: {
          ...testActivityFeed,
          errors: { items: [failedRequest], total: 1, page: 1, pageSize: 30, succeeded: 0, failed: 1 },
        },
      }),
    );
    await user.click(await screen.findByRole("button", { name: "Activity: 1 new problem" }));
    const panel = screen.getByRole("complementary", { name: "Activity" });
    expect(within(panel).getByText(/No tasks in the last 7 days/)).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "Clear" }));
    expect(within(panel).getByText("No errors in the last 24 hours.")).toBeInTheDocument();
    expect(Number(localStorage.getItem(CLEARED_KEY))).toBeGreaterThan(0);

    await user.click(within(panel).getByRole("button", { name: "Close activity" }));
    expect(screen.getByRole("button", { name: "Activity" })).toBeInTheDocument();
  });

  it("toggles a task's steps", async () => {
    const user = userEvent.setup();
    const succeeded = { ...testRunningTask, status: "succeeded" as const, summary: "4 rules synced" };
    renderApp(
      "/",
      fakeServices({
        activity: { ...testActivityFeed, tasks: { ...testActivityFeed.tasks, items: [succeeded], total: 1 } },
      }),
    );
    await user.click(await screen.findByRole("button", { name: "Activity" }));
    const panel = screen.getByRole("complementary", { name: "Activity" });
    expect(within(panel).getByText("4 rules synced")).toBeInTheDocument();
    expect(within(panel).queryByRole("list", { name: /Steps for/ })).not.toBeInTheDocument();
    await user.click(within(panel).getByRole("button", { name: "Show steps" }));
    expect(within(panel).getByRole("list", { name: "Steps for Sync mask policy pack" })).toBeInTheDocument();
  });
});

describe("RouteErrorPage", () => {
  it("shows the crash and records it for the Activity panel", async () => {
    const Boom = () => {
      throw new ApiError(500, "internal_error", "Kaboom", {}, "req-crash");
    };
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const router = createMemoryRouter([{ path: "/", element: <Boom />, errorElement: <RouteErrorPage /> }]);
    render(<RouterProvider router={router} />);

    expect(await screen.findByRole("heading", { name: "This page hit an error" })).toBeInTheDocument();
    expect(screen.getByText("Kaboom")).toBeInTheDocument();
    expect(screen.getByText(/req-crash/)).toBeInTheDocument();
    await waitFor(() => expect(sessionErrors()[0]).toMatchObject({ source: "render", requestId: "req-crash" }));
  });
});
