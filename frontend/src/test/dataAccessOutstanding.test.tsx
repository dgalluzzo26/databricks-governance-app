import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { accessStatusLabel } from "@/components/AccessStatusBadge";
import type { AccessStatus, GapItem } from "@/api/types";
import { describeGapItem } from "@/pages/dataAccess/DataAccessOutstanding";
import {
  fakeServices,
  renderApp,
  testAccessStatus,
  testPendingItems,
  testPendingSummary,
} from "./renderApp";

describe("Data access outstanding work", () => {
  it("shows counts, outstanding items, and tag changes", async () => {
    const user = userEvent.setup();
    renderApp("/data-access");
    await user.click(await screen.findByRole("tab", { name: "Outstanding work" }));
    const counts = await screen.findByLabelText("Outstanding counts");
    expect(within(counts).getByRole("button", { name: /Grants to add\s*17/ })).toBeInTheDocument();
    expect(within(counts).getByRole("button", { name: /Expired since sync\s*2/ })).toBeInTheDocument();
    expect(within(counts).getByText("Failed on last apply")).toBeInTheDocument();
    expect(screen.getByText(/5123 tagged tables tracked/)).toBeInTheDocument();
    expect(screen.getByText(/A dry run lists everything/)).toBeInTheDocument();

    const table = await screen.findByRole("table", { name: "Outstanding items" });
    expect(within(table).getByText(/GRANT SELECT on table main.sales.orders to dac_finance/)).toBeInTheDocument();
    expect(within(table).getByText(/permission denied/)).toBeInTheDocument();

    const tags = await screen.findByRole("table", { name: "Tag changes" });
    expect(within(tags).getByText("main.sales.forecast")).toBeInTheDocument();
    expect(within(tags).getByText("Value changed")).toBeInTheDocument();
    expect(screen.getByText(/6 newly eligible/)).toBeInTheDocument();
  });

  it("filters by clicking a count and by failed only", async () => {
    const user = userEvent.setup();
    renderApp("/data-access?tab=outstanding");
    const counts = await screen.findByLabelText("Outstanding counts");
    const nesting = within(counts).getByRole("button", { name: /Nesting to add/ });
    await user.click(nesting);
    expect(nesting).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByLabelText("Change")).toHaveValue("group_nest");
    const table = await screen.findByRole("table", { name: "Outstanding items" });
    expect(await within(table).findByText(/Nest dac_finance into dac_finance_budget/)).toBeInTheDocument();
    expect(within(table).queryByText(/GRANT SELECT/)).not.toBeInTheDocument();

    await user.click(nesting);
    expect(screen.getByLabelText("Change")).toHaveValue("all");
    await user.click(screen.getByLabelText("Failed only"));
    expect(await screen.findByText(/Nest dac_finance into dac_finance_budget/)).toBeInTheDocument();
    expect(screen.queryByText(/GRANT SELECT/)).not.toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Change"), "grant_revoke");
    expect(await screen.findByText("Nothing outstanding matches these filters.")).toBeInTheDocument();
  });

  it("explains when no sync has run and when tags are a baseline", async () => {
    renderApp("/data-access?tab=outstanding", fakeServices({ pendingSummary: { ...testPendingSummary, meta: null } }));
    expect(await screen.findByText("No sync has run yet")).toBeInTheDocument();
  });

  it("shows the baseline note and the apply wording", async () => {
    const meta = { ...testPendingSummary.meta!, mode: "apply" as const, tagBaseline: true };
    renderApp(
      "/data-access?tab=outstanding",
      fakeServices({ pendingSummary: { ...testPendingSummary, meta }, tagChanges: { items: [], total: 0, page: 1, pageSize: 25 } }),
    );
    expect(await screen.findByText(/first tag snapshot/)).toBeInTheDocument();
    expect(screen.getByText(/Only changes that failed to apply remain/)).toBeInTheDocument();
    expect(await screen.findByText("No tag changes match these filters.")).toBeInTheDocument();
  });

  it("surfaces load errors", async () => {
    renderApp("/data-access?tab=outstanding", fakeServices({ pendingSummary: new Error("boom") }));
    expect(await screen.findByText("Could not load outstanding work")).toBeInTheDocument();
  });

  it("describes every item kind", () => {
    const base = testPendingItems.items[0]!;
    const make = (patch: Partial<GapItem>): GapItem => ({ ...base, ...patch });
    expect(describeGapItem(make({ kind: "group_create", groupName: "dac_x" }))).toBe("Create dac_x");
    expect(describeGapItem(make({ kind: "member_add", principalName: "ana", groupName: "dac_x" }))).toBe(
      "Add ana to dac_x",
    );
    expect(describeGapItem(make({ kind: "member_remove", principalName: "bob", groupName: "dac_x" }))).toBe(
      "Remove bob from dac_x",
    );
    expect(describeGapItem(make({ kind: "grant_revoke" }))).toBe(
      "REVOKE SELECT on table main.sales.orders from dac_finance",
    );
  });
});

describe("Identity data-access status", () => {
  it("shows access status on My attributes", async () => {
    renderApp("/identity");
    expect(await screen.findByText("Member · grants pending")).toBeInTheDocument();
  });

  it("shows access status on admin Assignments", async () => {
    renderApp("/identity?tab=assignments");
    const table = await screen.findByRole("table", { name: "Assignments" });
    expect(await within(table).findByText("Member · grants pending")).toBeInTheDocument();
  });

  it("labels every membership and structure state", () => {
    const base: AccessStatus = testAccessStatus.statuses[0]!;
    const label = (patch: Partial<AccessStatus>) => accessStatusLabel({ ...base, ...patch }).label;
    expect(label({ membership: "member", structure: "ready" })).toBe("Has access");
    expect(label({ membership: "pending_sync" })).toBe("Waiting on sync");
    expect(label({ membership: "failed" })).toBe("Failed · sync retries");
    expect(label({ membership: "removal_pending" })).toBe("Removal at next sync");
    expect(label({ membership: "removed" })).toBe("Removed");
    expect(label({ membership: "not_applicable" })).toBe("No group");
    expect(label({ membership: "unknown" })).toBe("No sync yet");
  });
});
