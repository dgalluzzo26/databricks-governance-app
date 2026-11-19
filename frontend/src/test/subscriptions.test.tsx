import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeServices, renderApp, testSubscription, testSubscriptionOptions } from "./renderApp";

describe("Subscriptions", () => {
  it("lists subscriptions with their group, status and sync", async () => {
    renderApp("/subscriptions");
    expect(await screen.findByRole("heading", { name: "Subscriptions" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Subscriptions" });
    expect(within(table).getByText("IA engineers")).toBeInTheDocument();
    expect(within(table).getByText("dac_subscription_ia_engineers")).toBeInTheDocument();
    expect(within(table).getByText("Active")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "New subscription" })).toBeInTheDocument();
  });

  it("filters by phase", async () => {
    const user = userEvent.setup();
    renderApp("/subscriptions");
    await screen.findByRole("table", { name: "Subscriptions" });
    await user.click(screen.getByRole("tab", { name: /Pending approval/ }));
    expect(screen.getByText("No subscriptions match this filter.")).toBeInTheDocument();
  });

  it("shows an empty state", async () => {
    renderApp("/subscriptions", fakeServices({ subscriptions: [] }));
    expect(await screen.findByText(/No subscriptions yet/)).toBeInTheDocument();
  });

  it("describes access in plain language on the detail page", async () => {
    renderApp("/subscriptions/sub-1");
    expect(await screen.findByRole("heading", { name: "IA engineers" })).toBeInTheDocument();
    expect(screen.getByText(/tables where access_path is under people\.ia/)).toBeInTheDocument();
    expect(screen.getByText("dga_people.ia.claims")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Revoke" })).toBeInTheDocument();
  });

  it("lets an admin approve a pending subscription", async () => {
    const user = userEvent.setup();
    const pending = { ...testSubscription, status: "PENDING_APPROVAL" as const, phase: "PENDING_APPROVAL" as const };
    const services = fakeServices({ subscriptions: [pending] });
    const decide = vi.spyOn(services.subscriptions, "decide");
    renderApp("/subscriptions/sub-1", services);
    await user.click(await screen.findByRole("button", { name: "Approve" }));
    expect(decide).toHaveBeenCalledWith("sub-1", true, undefined);
  });

  it("creates a subscription through the wizard", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const create = vi.spyOn(services.subscriptions, "create");
    renderApp("/subscriptions/new", services);

    await user.type(await screen.findByLabelText("Name"), "Flood Team Q4");
    expect(screen.getByText("dac_subscription_flood_team_q4")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next: People" }));

    await user.type(screen.getByRole("combobox", { name: "Principal" }), "ana.lopez@example.com");
    await user.click(screen.getByRole("button", { name: "Add" }));
    expect(screen.getByText("ana.lopez@example.com")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next: Access" }));

    await user.click(screen.getByRole("button", { name: /Match by tags/ }));
    await user.click(screen.getByRole("combobox", { name: "Add value" }));
    await user.click(await screen.findByRole("option", { name: "people.ia" }));
    await user.click(screen.getByRole("button", { name: "Next: Attributes" }));
    await user.click(screen.getByRole("button", { name: "Next: Review" }));

    expect(await screen.findByText(/Nothing matches yet/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create subscription" }));
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        name: "flood_team_q4",
        displayName: "Flood Team Q4",
        subjects: [{ principalName: "ana.lopez@example.com", principalType: "USER" }],
        resources: [
          expect.objectContaining({
            selector: "TAG_MATCH",
            conditions: [{ tagKey: "access_path", values: ["people.ia"], match: "PREFIX" }],
          }),
        ],
      }),
    );
  });

  async function openAccessStep(user: ReturnType<typeof userEvent.setup>) {
    await user.type(await screen.findByLabelText("Name"), "Ops");
    await user.click(screen.getByRole("button", { name: "Next: People" }));
    await user.type(screen.getByRole("combobox", { name: "Principal" }), "ana.lopez@example.com");
    await user.click(screen.getByRole("button", { name: "Add" }));
    await user.click(screen.getByRole("button", { name: "Next: Access" }));
  }

  it("offers values for every governed tag, not just attribute tags", async () => {
    const user = userEvent.setup();
    renderApp("/subscriptions/new");
    await openAccessStep(user);
    await user.click(screen.getByRole("button", { name: /Match by tags/ }));
    await user.selectOptions(screen.getByLabelText("Where tag"), "region");
    await user.click(screen.getByRole("combobox", { name: "Add value" }));
    expect(await screen.findByRole("option", { name: "west" })).toBeInTheDocument();
  });

  it("lets admins pick a dashboard by name and grant the admin level", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    vi.spyOn(services.subscriptions, "browse").mockImplementation(async (kind) =>
      kind === "DASHBOARD" ? { items: [{ name: "d1", label: "Claims overview", comment: "" }] } : { items: [] },
    );
    const create = vi.spyOn(services.subscriptions, "create");
    renderApp("/subscriptions/new", services);
    await openAccessStep(user);
    await user.click(screen.getByRole("button", { name: /Pick an object/ }));
    await user.selectOptions(screen.getByLabelText("Type"), "DASHBOARD");
    await user.click(screen.getByRole("combobox", { name: "dashboard" }));
    await user.click(await screen.findByRole("option", { name: "Claims overview (d1)" }));
    await user.click(screen.getByRole("button", { name: /^Admin/ }));
    expect(screen.getByText(/Grants CAN_MANAGE\./)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Next: Attributes" }));
    await user.click(screen.getByRole("button", { name: "Next: Review" }));
    await user.click(await screen.findByRole("button", { name: "Create subscription" }));
    expect(create).toHaveBeenCalledWith(
      expect.objectContaining({
        resources: [
          expect.objectContaining({ securableKind: "DASHBOARD", fullName: "d1", label: "Claims overview", preset: "ADMIN" }),
        ],
      }),
    );
  });

  it("hides the admin level and workspace objects from approvers", async () => {
    const user = userEvent.setup();
    const { ADMIN: _admin, ...presets } = testSubscriptionOptions.presets;
    renderApp(
      "/subscriptions/new",
      fakeServices({ subscriptionOptions: { ...testSubscriptionOptions, isAdmin: false, presets } }),
    );
    await openAccessStep(user);
    await user.click(screen.getByRole("button", { name: /Pick an object/ }));
    expect(screen.queryByRole("button", { name: /^Admin/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("option", { name: "Dashboard" })).not.toBeInTheDocument();
  });

  it("tells approvers their subscriptions need admin approval", async () => {
    renderApp(
      "/subscriptions/new",
      fakeServices({ subscriptionOptions: { ...testSubscriptionOptions, isAdmin: false, requiresApproval: true } }),
    );
    await screen.findByLabelText("Name");
    expect(screen.queryByText(/Waive the review gate/)).not.toBeInTheDocument();
  });
});
