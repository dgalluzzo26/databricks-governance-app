import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReactElement } from "react";
import { cloneElement } from "react";
import { buildChartRows } from "@/pages/dataAccess/DataAccessAnalyticsPage";
import type { DataAccessAnalyticsDay } from "@/api/types";
import { fakeServices, renderApp, testDataAccessAnalytics, testUser } from "./renderApp";

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

const DAILY_ROWS: DataAccessAnalyticsDay[] = [
  {
    day: "2026-11-12",
    mode: "dry_run",
    runs: 2,
    success: 1,
    failed: 1,
    grantsAdded: 2,
    grantsRevoked: 1,
    membersAdded: 0,
    membersRemoved: 0,
    avgDurationSeconds: 10,
  },
  {
    day: "2026-11-12",
    mode: "apply",
    runs: 1,
    success: 1,
    failed: 0,
    grantsAdded: 3,
    grantsRevoked: 0,
    membersAdded: 2,
    membersRemoved: 1,
    avgDurationSeconds: 30,
  },
];
const [DRY_RUN_DAY] = DAILY_ROWS as [DataAccessAnalyticsDay];

describe("buildChartRows", () => {
  it("combines modes per day and weights average duration by finished runs", () => {
    const row = buildChartRows(DAILY_ROWS, "all")[0]!;
    expect(row).toMatchObject({
      day: "2026-11-12",
      label: "11-12",
      runs: 3,
      success: 2,
      failed: 1,
      grantsAdded: 5,
      grantsRevoked: 1,
      membersAdded: 2,
      membersRemoved: 1,
    });
    // (10 * 2 finished dry-run + 30 * 1 finished apply) / 3 finished runs
    expect(row.avgDurationSeconds).toBe(16.7);
  });

  it("filters to one mode", () => {
    const row = buildChartRows(DAILY_ROWS, "apply")[0]!;
    expect(row.runs).toBe(1);
    expect(row.avgDurationSeconds).toBe(30);
  });

  it("returns no rows when the mode has no activity", () => {
    expect(buildChartRows([DRY_RUN_DAY], "apply")).toEqual([]);
  });

  it("leaves duration null when no run finished that day", () => {
    const running: DataAccessAnalyticsDay = {
      ...DRY_RUN_DAY,
      success: 0,
      failed: 0,
      avgDurationSeconds: null,
    };
    expect(buildChartRows([running], "all")[0]!.avgDurationSeconds).toBeNull();
  });

  it("sorts days ascending", () => {
    const rows = buildChartRows(
      [
        { ...DRY_RUN_DAY, day: "2026-11-13" },
        { ...DRY_RUN_DAY, day: "2026-11-11" },
      ],
      "all",
    );
    expect(rows.map((r) => r.day)).toEqual(["2026-11-11", "2026-11-13"]);
  });
});

describe("Data access analytics page", () => {
  it("renders KPIs, charts, fingerprints, and recent failures", async () => {
    renderApp("/data-access/analytics");
    expect(
      await screen.findByRole("heading", { name: "Data access analytics" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("Sync runs")).toBeInTheDocument();
    expect(screen.getByText("80%")).toBeInTheDocument();
    expect(screen.getByText("12.5 s")).toBeInTheDocument();
    expect(screen.getByText("Desired-state changes")).toBeInTheDocument();
    expect(screen.getByText("Run outcomes per day")).toBeInTheDocument();
    expect(screen.getByText("Planned changes per day")).toBeInTheDocument();
    expect(screen.getByText("Average run duration")).toBeInTheDocument();
    expect(screen.getAllByTestId("chart-container")).toHaveLength(3);

    const failures = screen.getByRole("table", { name: "Recent failed sync runs" });
    expect(within(failures).getByText("Warehouse unavailable")).toBeInTheDocument();

    const fingerprints = screen.getByRole("table", { name: "Desired-state fingerprints" });
    expect(within(fingerprints).getByText("035e259276c5fb22")).toBeInTheDocument();
  });

  it("re-queries when the window changes", async () => {
    const services = fakeServices();
    const analytics = vi.spyOn(services.dataAccess, "analytics");
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    renderApp("/data-access/analytics", services);
    await screen.findByRole("heading", { name: "Data access analytics" });
    await user.selectOptions(screen.getByLabelText("Window"), "7");
    expect(analytics).toHaveBeenLastCalledWith(7, expect.anything());
  });

  it("filters charts by mode", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    const user = userEvent.setup();
    renderApp("/data-access/analytics");
    await screen.findByRole("heading", { name: "Data access analytics" });
    await user.selectOptions(screen.getByLabelText("Mode"), "apply");
    expect(screen.getByText("Run outcomes per day")).toBeInTheDocument();
  });

  it("shows an empty state when there are no runs", async () => {
    renderApp(
      "/data-access/analytics",
      fakeServices({
        dataAccessAnalytics: {
          ...testDataAccessAnalytics,
          kpis: {
            ...testDataAccessAnalytics.kpis,
            totalRuns: 0,
            successRuns: 0,
            failedRuns: 0,
            runningRuns: 0,
          },
          daily: [],
          failures: [],
          fingerprints: [],
        },
      }),
    );
    expect(await screen.findByText("No sync runs yet")).toBeInTheDocument();
    expect(screen.queryByText("Sync runs")).not.toBeInTheDocument();
  });

  it("shows empty tables when there are failures or fingerprints to list", async () => {
    renderApp(
      "/data-access/analytics",
      fakeServices({
        dataAccessAnalytics: { ...testDataAccessAnalytics, failures: [], fingerprints: [] },
      }),
    );
    expect(await screen.findByText("No failed runs in this window.")).toBeInTheDocument();
    expect(screen.getByText("No successful runs in this window.")).toBeInTheDocument();
  });

  it("shows an error when analytics fail to load", async () => {
    renderApp(
      "/data-access/analytics",
      fakeServices({ dataAccessAnalytics: new Error("analytics down") }),
    );
    expect(await screen.findByText("Could not load analytics")).toBeInTheDocument();
  });

  it("redirects non-admins away from analytics", async () => {
    renderApp(
      "/data-access/analytics",
      fakeServices({ user: { ...testUser, isAdmin: false, isApprover: false, groups: ["users"] } }),
    );
    expect(await screen.findByRole("heading", { name: "Access explorer" })).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Data access analytics" }),
    ).not.toBeInTheDocument();
  });
});
