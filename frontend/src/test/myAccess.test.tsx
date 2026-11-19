import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { MyAccessResources } from "@/api/types";
import { fakeServices, renderApp, testMyAccess, testMyAccessSubscription } from "./renderApp";

describe("Identity My access", () => {
  it("shows groups, inherited attributes, and expands resources", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const resources = vi.spyOn(services.identityAttributes, "myAccessResources");
    renderApp("/identity?tab=my-access", services);

    expect(await screen.findByRole("tab", { name: "My access" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    expect(await screen.findByText("2 groups")).toBeInTheDocument();
    expect(screen.getByText("4 attributes")).toBeInTheDocument();
    expect(screen.getByText("2 via groups")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Via admins" })).toBeInTheDocument();

    const groups = await screen.findByRole("table", { name: "My groups" });
    expect(within(groups).getByText("admins")).toBeInTheDocument();
    expect(within(groups).getByText("users")).toBeInTheDocument();

    expect(screen.getByRole("heading", { name: "Via users" })).toBeInTheDocument();
    expect(screen.getByText("CA")).toBeInTheDocument();
    expect(screen.getAllByText("Has access").length).toBeGreaterThan(0);

    await user.click(screen.getAllByRole("button", { name: "Show resources" })[0]!);
    expect(resources).toHaveBeenCalled();
    expect(
      await screen.findByRole("table", { name: /Columns affected|Tables filtered|Tables unlocked/ }),
    ).toBeInTheDocument();
  });

  it("pages a long list of unlocked tables instead of rendering every row", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const tables = Array.from({ length: 25 }, (_, i) => ({
      fullName: `data_governance_demo.finance.t${String(i).padStart(3, "0")}`,
      columnName: null,
      tagValue: "finance",
      match: "descendant" as const,
      groupName: "dac_finance",
    }));
    const payload: MyAccessResources = {
      attributeKey: "data_access",
      attributeValue: "finance",
      kind: "data_access",
      tables,
    };
    vi.spyOn(services.identityAttributes, "myAccessResources").mockResolvedValue(payload);
    renderApp("/identity?tab=my-access", services);

    await user.click((await screen.findAllByRole("button", { name: "Show resources" }))[0]!);
    const table = await screen.findByRole("table", { name: "Tables unlocked" });
    expect(within(table).getByText("data_governance_demo.finance.t000")).toBeInTheDocument();
    expect(within(table).queryByText("data_governance_demo.finance.t010")).not.toBeInTheDocument();
    expect(screen.getByText("1–10 of 25")).toBeInTheDocument();

    const pagerNext = screen.getAllByRole("button", { name: "Next" });
    await user.click(pagerNext[pagerNext.length - 1]!);
    expect(screen.getByText("11–20 of 25")).toBeInTheDocument();
    expect(within(table).getByText("data_governance_demo.finance.t010")).toBeInTheDocument();
    expect(within(table).queryByText("data_governance_demo.finance.t000")).not.toBeInTheDocument();
  });

  it("lists subscriptions that include the caller and shows what they hold", async () => {
    const user = userEvent.setup();
    renderApp("/identity?tab=my-access");

    const table = await screen.findByRole("table", { name: "My subscriptions" });
    expect(within(table).getByText("IA engineers")).toBeInTheDocument();
    expect(within(table).getByText("dac_subscription_ia_engineers")).toBeInTheDocument();
    expect(within(table).getByText("Via users")).toBeInTheDocument();
    expect(within(table).getByText("Active")).toBeInTheDocument();
    expect(screen.getByText("1 subscriptions")).toBeInTheDocument();

    await user.click(within(table).getByRole("button", { name: "Show access" }));
    expect(screen.getByText(/Reaches: Tables where access_path is under people\.ia/)).toBeInTheDocument();
    const grants = screen.getByRole("table", { name: "Matched objects" });
    expect(within(grants).getByText("dga_people.ia.claims")).toBeInTheDocument();
    expect(within(grants).getByText("SELECT")).toBeInTheDocument();
  });

  it("explains when a subscription holds nothing yet", async () => {
    const user = userEvent.setup();
    const services = fakeServices({
      myAccess: {
        ...testMyAccess,
        subscriptions: [{ ...testMyAccessSubscription, phase: "PENDING_APPROVAL", grants: [] }],
        subscriptionCount: 1,
      },
    });
    renderApp("/identity?tab=my-access", services);
    const table = await screen.findByRole("table", { name: "My subscriptions" });
    expect(within(table).getByText("Pending approval")).toBeInTheDocument();
    await user.click(within(table).getByRole("button", { name: "Show access" }));
    expect(screen.getByText(/not in effect, so it holds no access/)).toBeInTheDocument();
  });
});
