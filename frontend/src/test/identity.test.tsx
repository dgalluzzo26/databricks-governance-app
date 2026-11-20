import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeServices, renderApp, testUser } from "./renderApp";

describe("Identity attributes", () => {
  it("shows my attributes and admin tabs", async () => {
    const user = userEvent.setup();
    renderApp("/identity");

    expect(await screen.findByRole("heading", { name: "Identity attributes" })).toBeInTheDocument();
    expect(await screen.findByText("finance.budget")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Assignments" }));
    expect(await screen.findByRole("heading", { name: "Assign attributes" })).toBeInTheDocument();
    expect(await screen.findByRole("columnheader", { name: "Expires" })).toBeInTheDocument();
    expect(screen.getByText(new Date("2026-12-31T23:59:00Z").toLocaleString())).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Definitions" }));
    const table = await screen.findByRole("table", { name: "Attribute definitions" });
    expect(within(table).getByText("dga_domain", { selector: "code" })).toBeInTheDocument();
    expect(within(table).getByText("Business domain")).toBeInTheDocument();
  });

  it("shows Assignments to path approvers without admin tabs", async () => {
    renderApp(
      "/identity",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: true, groups: ["finance-owners"] },
      }),
    );
    expect(await screen.findByRole("heading", { name: "Identity attributes" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Assignments" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Definitions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Approvers" })).not.toBeInTheDocument();
  });

  it("submits a bulk assign and shows the result summary", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const bulkAssign = vi.spyOn(services.identityAttributes, "bulkAssign");
    renderApp("/identity?tab=assignments", services);

    const form = await screen.findByRole("heading", { name: "Assign attributes" });
    const card = form.closest("section") ?? form.parentElement!;
    const picker = within(card as HTMLElement).getByRole("combobox", { name: "Add principal" });
    await user.type(picker, "an");
    const matches = await screen.findByRole("listbox", { name: "Matching principals" });
    await user.click(within(matches).getByRole("button", { name: /Ana Lopez/ }));
    expect(within(card as HTMLElement).getByText("ana.lopez@example.com")).toBeInTheDocument();

    await user.selectOptions(within(card as HTMLElement).getByLabelText("Grant"), "sensitivity");
    const valueBox = within(card as HTMLElement).getByRole("combobox", { name: "Sensitivity path" });
    await user.click(valueBox);
    await user.click(await screen.findByRole("option", { name: "finance.budget.PII.Name" }));
    await user.type(within(card as HTMLElement).getByLabelText("Justification"), "Close support");
    expect(screen.getByText("1 principal × 1 value = 1 assignment")).toBeInTheDocument();
    await user.click(within(card as HTMLElement).getByRole("button", { name: "Assign" }));

    expect(bulkAssign).toHaveBeenCalledWith({
      principals: [{ principalName: "ana.lopez@example.com", principalType: "USER" }],
      attributes: [{ attributeKey: "sensitivity", attributeValue: "finance.budget.PII.Name" }],
      justification: "Close support",
      expiresAt: null,
    });
    expect(await screen.findByText(/1 created or renewed, 0 already held/)).toBeInTheDocument();
  });

  it("blocks a second sensitivity or data-access definition", async () => {
    const user = userEvent.setup();
    renderApp("/identity?tab=definitions");

    expect(await screen.findByRole("tab", { name: "Existing definitions" })).toHaveAttribute(
      "aria-selected",
      "true",
    );
    await user.click(screen.getByRole("tab", { name: "Create new definition" }));
    expect(await screen.findByText("Create definition from governed tags")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Definition purpose" })).toHaveValue("sensitivity");
    expect(await screen.findByText("Already configured")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create definition" })).toBeDisabled();

    await user.selectOptions(screen.getByRole("combobox", { name: "Definition purpose" }), "data_access");
    expect(screen.getByRole("textbox", { name: "Attribute key" })).toHaveValue("data_access");
    expect(screen.getByText("Already configured")).toBeInTheDocument();
    expect(await screen.findByRole("checkbox", { name: "Select access_path" })).toBeChecked();
    expect(screen.getByRole("button", { name: "Create definition" })).toBeDisabled();
  });

  it("hides admin tabs for non-admins", async () => {
    renderApp(
      "/identity",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: false, groups: ["users"] },
      }),
    );
    expect(await screen.findByRole("heading", { name: "Identity attributes" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Definitions" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Assignments" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Approvers" })).not.toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Approvals" })).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Request access" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "My attributes" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "My requests" })).toBeInTheDocument();
  });

  it("lets a user submit a sensitivity access request", async () => {
    const user = userEvent.setup();
    renderApp("/identity?tab=request");

    expect(await screen.findByRole("heading", { name: "Request access" })).toBeInTheDocument();
    const path = await screen.findByRole("combobox", { name: "Sensitivity path" });
    await user.click(path);
    await user.click(await screen.findByRole("option", { name: "finance.budget.PII.Name" }));
    await user.type(screen.getByLabelText("Justification"), "Quarterly close");
    await user.type(screen.getByLabelText("Requested expiry"), "2026-12-31T12:00");
    expect(screen.getByRole("button", { name: "Submit request" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByLabelText("Sensitivity path")).toHaveValue("");
  });

  it("redirects legacy approvals tab to /approvals", async () => {
    renderApp("/identity?tab=approvals");
    expect(await screen.findByRole("heading", { name: "Approvals" })).toBeInTheDocument();
    expect(await screen.findByText("Inbox")).toBeInTheDocument();
  });

  it("adds a user and a group as approvers for an access path", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const upsert = vi.spyOn(services.identityAttributes, "upsertCategoryApprover");
    renderApp("/identity?tab=approvers", services);

    expect(await screen.findByText("Configured approvers")).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Approvers" });
    expect(within(table).getByText("finance")).toBeInTheDocument();
    expect(within(table).getByText("finance-owners")).toBeInTheDocument();

    await user.click(screen.getByRole("combobox", { name: "Access path" }));
    await user.click(await screen.findByRole("option", { name: "finance.budget" }));

    const picker = screen.getByRole("combobox", { name: "Add approver principal" });
    await user.type(picker, "an");
    const userHits = await screen.findByRole("listbox", { name: "Matching principals" });
    await user.click(within(userHits).getByRole("button", { name: /Ana Lopez/ }));
    expect(screen.getByLabelText("Selected approvers")).toBeInTheDocument();

    await user.type(picker, "data");
    const groupHits = await screen.findByRole("listbox", { name: "Matching principals" });
    await user.click(within(groupHits).getByRole("button", { name: /data-stewards/ }));

    await user.click(screen.getByRole("button", { name: "Add approvers" }));
    expect(upsert).toHaveBeenCalledWith({
      categoryPath: "finance.budget",
      principals: [
        { principalName: "ana.lopez@example.com", principalType: "USER" },
        { principalName: "data-stewards", principalType: "GROUP" },
      ],
    });
  });
});
