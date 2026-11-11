import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeServices, renderApp, testUser } from "./renderApp";

describe("Identity attributes", () => {
  it("shows my attributes and admin tabs", async () => {
    const user = userEvent.setup();
    renderApp("/identity");

    expect(await screen.findByRole("heading", { name: "Identity attributes" })).toBeInTheDocument();
    expect(await screen.findByText("finance.budget")).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Assignments" }));
    expect(await screen.findByRole("columnheader", { name: "Expires" })).toBeInTheDocument();
    expect(screen.getByText(new Date("2026-12-31T23:59:00Z").toLocaleString())).toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: "Definitions" }));
    const table = await screen.findByRole("table", { name: "Attribute definitions" });
    expect(within(table).getByText("dga_domain", { selector: "code" })).toBeInTheDocument();
    expect(within(table).getByText("Business domain")).toBeInTheDocument();
  });

  it("creates a definition by selecting governed tags", async () => {
    const user = userEvent.setup();
    renderApp("/identity?tab=definitions");

    expect(await screen.findByRole("tab", { name: "Existing definitions" })).toHaveAttribute("aria-selected", "true");
    await user.click(screen.getByRole("tab", { name: "Create new definition" }));
    expect(await screen.findByText("Create definition from governed tags")).toBeInTheDocument();
    const createButton = screen.getByRole("button", { name: "Create definition" });
    expect(createButton).toBeDisabled();

    await user.click(await screen.findByRole("checkbox", { name: "Select dga_domain" }));
    expect(screen.getByRole("button", { name: "Create definition" })).toBeEnabled();
    expect(screen.getByText("Allowed values resolved from selected tags (3)")).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/Leave empty for free-form/)).not.toBeInTheDocument();
  });

  it("keeps a custom attribute name independent of the selected tag", async () => {
    const user = userEvent.setup();
    renderApp("/identity?tab=definitions");

    await user.click(await screen.findByRole("tab", { name: "Create new definition" }));
    const keyInput = await screen.findByPlaceholderText("e.g. data_access");
    await user.type(keyInput, "data_access");
    await user.click(screen.getByRole("checkbox", { name: "Select sub_domain" }));
    await user.click(screen.getByRole("checkbox", { name: "Select sub_domain" }));
    await user.click(screen.getByRole("checkbox", { name: "Select sub_domain" }));

    expect(screen.getByDisplayValue("data_access")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create definition" })).toBeEnabled();
  });

  it("hides admin tabs for non-admins and approvals unless they are approvers", async () => {
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

  it("shows Approvals for category approvers who are not admins", async () => {
    renderApp(
      "/identity",
      fakeServices({
        user: { ...testUser, isAdmin: false, isApprover: true, groups: ["finance-owners"] },
      }),
    );
    expect(await screen.findByRole("tab", { name: "Approvals" })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: "Definitions" })).not.toBeInTheDocument();
  });

  it("lets a user submit a sensitivity access request", async () => {
    const user = userEvent.setup();
    renderApp("/identity?tab=request");

    expect(await screen.findByRole("heading", { name: "Request access" })).toBeInTheDocument();
    await screen.findByRole("option", { name: "finance.budget.PII.Name" });
    await user.selectOptions(screen.getByLabelText("Sensitivity path"), "finance.budget.PII.Name");
    await user.type(screen.getByLabelText("Justification"), "Quarterly close");
    await user.type(screen.getByLabelText("Requested expiry"), "2026-12-31T12:00");
    expect(screen.getByRole("button", { name: "Submit request" })).toBeEnabled();
    await user.click(screen.getByRole("button", { name: "Submit request" }));
    expect(await screen.findByLabelText("Sensitivity path")).toHaveValue("");
  });

  it("shows approvals inbox items for approvers", async () => {
    renderApp("/identity?tab=approvals");
    expect(await screen.findByText("Approvals inbox")).toBeInTheDocument();
    expect((await screen.findAllByText(/finance\.budget\.PII\.Name/)).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Deny" })).toBeInTheDocument();
  });

  it("lists category approvers for admins", async () => {
    renderApp("/identity?tab=approvers");
    expect(await screen.findByText("Configured approvers")).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Approvers" });
    expect(within(table).getByText("finance")).toBeInTheDocument();
    expect(within(table).getByText("finance-owners")).toBeInTheDocument();
  });
});
