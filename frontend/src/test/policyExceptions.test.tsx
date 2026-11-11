import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeServices, renderApp } from "./renderApp";

describe("Policy exceptions", () => {
  it("lists system exceptions and adds a group", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const upsert = vi.spyOn(services.policyExceptions, "upsert");
    renderApp("/exceptions", services);

    expect(await screen.findByRole("heading", { name: "Policy exceptions" })).toBeInTheDocument();
    const table = await screen.findByRole("table", { name: "Policy exceptions" });
    expect(within(table).getByText("app-sp")).toBeInTheDocument();
    expect(within(table).getByText("system")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Principal type"), "GROUP");
    await user.type(screen.getByLabelText("Principal"), "data-services");
    await user.type(screen.getByLabelText("Note"), "DSP full access");
    await user.click(screen.getByRole("button", { name: "Add exception" }));
    expect(upsert).toHaveBeenCalledWith({
      principalName: "data-services",
      principalType: "GROUP",
      note: "DSP full access",
    });
  });
});
