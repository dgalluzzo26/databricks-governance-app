import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { cloneElement } from "react";
import { buildHourRows, formatMs, formatPercent, formatUsd } from "@/pages/performance/PerformancePage";
import {
  fakeServices,
  renderApp,
  testAppCost,
  testDataAccessGrantsPage,
  testDataAccessRunsPage,
  testPerformanceSummary,
} from "./renderApp";

// Recharts measures its container; jsdom reports zero size, so pin the chart to a fixed box.
vi.mock("recharts", async (importOriginal) => {
  const actual = await importOriginal<typeof import("recharts")>();
  return {
    ...actual,
    ResponsiveContainer: ({ children }: { children: ReactElement }) => (
      <div data-testid="chart-container">
        {cloneElement(children as ReactElement<{ width?: number; height?: number }>, {
          width: 800,
          height: 260,
        })}
      </div>
    ),
  };
});

describe("performance formatting helpers", () => {
  it("formats milliseconds and percentages, with dashes for unknown values", () => {
    expect(formatMs(850.4)).toBe("850 ms");
    expect(formatMs(12_400)).toBe("12.4 s");
    expect(formatMs(null)).toBe("—");
    expect(formatPercent(0.876)).toBe("88%");
    expect(formatPercent(null)).toBe("—");
  });

  it("converts hourly buckets into chart rows", () => {
    const [row] = buildHourRows(testPerformanceSummary.hourly);
    expect(row).toMatchObject({ avgWarehouseMs: 300, avgLakebaseMs: 20, avgAppMs: 60, slowCount: 6 });
    expect(row!.label).toMatch(/\d/);
  });
});

describe("Performance page", () => {
  it("renders KPIs, recommendations, activities, sizing, and slow requests", async () => {
    renderApp("/performance");
    expect(await screen.findByRole("heading", { name: "Performance" })).toBeInTheDocument();
    expect(await screen.findByText("Slower than expected")).toBeInTheDocument();
    expect(screen.getByText("Slow time on warehouse")).toBeInTheDocument();
    expect(screen.getByText("75%")).toBeInTheDocument();

    const recs = screen.getByRole("list", { name: "Scaling recommendations" });
    expect(within(recs).getByText("Approve permission request: warehouse time dominates")).toBeInTheDocument();

    const activities = screen.getByRole("table", { name: "Latency by activity" });
    expect(within(activities).getByText("Approve permission request")).toBeInTheDocument();
    expect(within(activities).getByText("9.0 s")).toBeInTheDocument();

    expect(screen.getByText("Current sizing")).toBeInTheDocument();
    expect(screen.getByText("Unavailable: Could not read endpoint")).toBeInTheDocument();
    expect(screen.getByText(/1–2 clusters/)).toBeInTheDocument();

    const slow = screen.getByRole("table", { name: "Slow requests" });
    expect(within(slow).getByText("9.0 s")).toBeInTheDocument();
    expect(within(slow).getByText("admin@example.com")).toBeInTheDocument();
  });

  it("refetches the summary when the window changes", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    const services = fakeServices();
    const summary = vi.spyOn(services.performance, "summary");
    renderApp("/performance", services);
    await screen.findByRole("heading", { name: "Performance" });
    expect(summary).toHaveBeenCalledWith(24, expect.anything());

    await user.selectOptions(screen.getByLabelText("Window"), "168");
    await waitFor(() => expect(summary).toHaveBeenLastCalledWith(168, expect.anything()));
  });

  it("shows an empty state when no requests are recorded", async () => {
    const empty = {
      ...testPerformanceSummary,
      kpis: { ...testPerformanceSummary.kpis, totalRequests: 0, slowRequests: 0, slowRate: null },
      activities: [],
      hourly: [],
      recommendations: [],
    };
    renderApp("/performance", fakeServices({ performanceSummary: empty }));
    expect(await screen.findByText("No requests recorded yet")).toBeInTheDocument();
    expect(screen.queryByText("Slower than expected")).not.toBeInTheDocument();
  });

  it("reports summary, capacity, and slow-request errors without breaking the page", async () => {
    renderApp(
      "/performance",
      fakeServices({
        performanceSummary: new Error("summary exploded"),
        performanceCapacity: new Error("sizing exploded"),
        performanceSlowRequests: new Error("slow exploded"),
      }),
    );
    expect(await screen.findByText("Could not load performance summary")).toBeInTheDocument();
    expect(await screen.findByText("Could not read sizing")).toBeInTheDocument();
    expect(await screen.findByText("Could not load slow requests")).toBeInTheDocument();
  });

  it("shows a message when nothing is slower than expected", async () => {
    renderApp(
      "/performance",
      fakeServices({
        performanceSlowRequests: { items: [], total: 0, page: 1, pageSize: 25 },
      }),
    );
    expect(await screen.findByText("Nothing slower than expected in this window.")).toBeInTheDocument();
  });
});

describe("Data access analytics paged tables", () => {
  it("renders run history rows and the pager total", async () => {
    renderApp("/data-access/analytics");
    const runs = await screen.findByRole("table", { name: "Sync run history" });
    expect(within(runs).getByText("Live")).toBeInTheDocument();
    expect(within(runs).getByText("+4 / −1")).toBeInTheDocument();
    expect(within(runs).getByText("abcdef123456")).toBeInTheDocument();
    expect(screen.getByText("1–25 of 26")).toBeInTheDocument();
  });

  it("renders planned grant changes", async () => {
    renderApp("/data-access/analytics");
    const grants = await screen.findByRole("table", { name: "Planned grant changes" });
    expect(within(grants).getByText("main.sales.orders")).toBeInTheDocument();
    expect(within(grants).getByText("dac_finance_readers")).toBeInTheDocument();
  });

  it("requests the next page of runs from the server", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    const services = fakeServices();
    const runs = vi.spyOn(services.dataAccess, "analyticsRuns");
    renderApp("/data-access/analytics", services);
    await screen.findByRole("table", { name: "Sync run history" });
    await user.click(screen.getAllByRole("button", { name: "Next" })[0]!);
    await waitFor(() => expect(runs).toHaveBeenLastCalledWith(expect.objectContaining({ page: 2 }), expect.anything()));
  });

  it("filters grant changes by change type", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    const services = fakeServices();
    const grants = vi.spyOn(services.dataAccess, "analyticsGrants");
    renderApp("/data-access/analytics", services);
    await screen.findByRole("table", { name: "Planned grant changes" });
    await user.selectOptions(screen.getByLabelText("Change"), "revoke");
    await waitFor(() =>
      expect(grants).toHaveBeenLastCalledWith(expect.objectContaining({ change: "revoke", page: 1 }), expect.anything()),
    );
  });

  it("explains when the SQL views are not available on this storage backend", async () => {
    renderApp(
      "/data-access/analytics",
      fakeServices({
        dataAccessRuns: {
          ...testDataAccessRunsPage,
          rows: [],
          total: 0,
          available: false,
          detail: "The SQL views exist only on the Lakebase backend.",
        },
        dataAccessGrants: {
          ...testDataAccessGrantsPage,
          rows: [],
          total: 0,
          available: false,
          detail: "The SQL views exist only on the Lakebase backend.",
        },
      }),
    );
    expect(await screen.findByText("data_access_runs_v is not available")).toBeInTheDocument();
    expect(screen.getByText("data_access_run_grants_v is not available")).toBeInTheDocument();
  });

  it("shows empty and error states for the paged tables", async () => {
    renderApp(
      "/data-access/analytics",
      fakeServices({
        dataAccessRuns: { ...testDataAccessRunsPage, rows: [], total: 0 },
        dataAccessGrants: new Error("grants exploded"),
      }),
    );
    expect(await screen.findByText("No runs match these filters.")).toBeInTheDocument();
    expect(await screen.findByText("Could not load grant changes")).toBeInTheDocument();
  });
});

describe("App cost card", () => {
  it("shows per-sync and per-day cost from billing", async () => {
    renderApp("/performance");
    expect(await screen.findByText("App cost")).toBeInTheDocument();
    expect(await screen.findByText("Avg cost per sync")).toBeInTheDocument();
    expect(screen.getByText("$5.50")).toBeInTheDocument();
    expect(screen.getByText("$1.50")).toBeInTheDocument();
    expect(screen.getByText("$7.50")).toBeInTheDocument();
    expect(screen.getByText("$2.50")).toBeInTheDocument();
    expect(screen.getByText(testAppCost.lagNote)).toBeInTheDocument();
  });

  it("explains when billing cannot be read", async () => {
    renderApp(
      "/performance",
      fakeServices({
        performanceAppCost: {
          ...testAppCost,
          available: false,
          detail: "system.billing.usage is not accessible",
        },
      }),
    );
    expect(await screen.findByText("App cost unavailable")).toBeInTheDocument();
    expect(screen.getByText("system.billing.usage is not accessible")).toBeInTheDocument();
    expect(screen.queryByText("Avg cost per sync")).not.toBeInTheDocument();
  });

  it("formats USD for display and shows a dash when there is no value", () => {
    expect(formatUsd(5.5)).toBe("$5.50");
    expect(formatUsd(0)).toBe("$0.00");
    expect(formatUsd(null)).toBe("—");
  });
});
