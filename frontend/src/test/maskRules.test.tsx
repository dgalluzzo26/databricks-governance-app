import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeServices, renderApp, testUser } from "./renderApp";

describe("Mask rules", () => {
  it("shows pack status and named rules", async () => {
    renderApp("/mask-rules");
    expect(await screen.findByRole("heading", { name: "Mask rules" })).toBeInTheDocument();
    expect(await screen.findByText("In sync")).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Mask rules" });
    expect(within(table).getByText("email", { selector: "code" })).toBeInTheDocument();
    expect(within(table).getByText("joinable", { selector: "code" })).toBeInTheDocument();
  });

  it("creates a builtin rule", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const upsert = vi.spyOn(services.maskRules, "upsert");
    renderApp("/mask-rules", services);
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    await user.type(screen.getByLabelText("Rule key"), "employee_email");
    await user.selectOptions(screen.getByLabelText("Handler"), "email");
    await user.click(screen.getByRole("button", { name: "Create rule" }));
    expect(upsert).toHaveBeenCalledWith(
      expect.objectContaining({ ruleKey: "employee_email", handler: "email", kind: "builtin" }),
    );
  });

  it("requires validate before saving a custom handler", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const upsert = vi.spyOn(services.maskRules, "upsert");
    const validate = vi.spyOn(services.maskRules, "validateHandler");
    renderApp("/mask-rules", services);
    await user.click(await screen.findByRole("button", { name: "New rule" }));
    await user.type(screen.getByLabelText("Rule key"), "id_hash");
    await user.selectOptions(screen.getByLabelText("Kind"), "custom");
    expect(screen.getByRole("button", { name: "Create rule" })).toBeDisabled();
    await user.type(
      screen.getByLabelText("SQL expression over v"),
      "concat('ID-', sha2(lower(trim(v)), 256))",
    );
    expect(screen.getByRole("button", { name: "Create rule" })).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Validate" }));
    expect(validate).toHaveBeenCalled();
    expect(await screen.findByText("Validated")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create rule" }));
    expect(upsert).toHaveBeenCalledWith(
      expect.objectContaining({
        ruleKey: "id_hash",
        kind: "custom",
        validationToken: "token",
      }),
    );
  });

  it("is read-only for non-admins", async () => {
    renderApp(
      "/mask-rules",
      fakeServices({ user: { ...testUser, isAdmin: false, groups: ["users"] } }),
    );
    expect(await screen.findByRole("table", { name: "Mask rules" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "New rule" })).not.toBeInTheDocument();
    expect(screen.queryByText("Policy pack")).not.toBeInTheDocument();
  });
});
