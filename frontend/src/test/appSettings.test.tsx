import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeServices, renderApp } from "./renderApp";

describe("App settings", () => {
  it("shows the metadata cache switch under App settings", async () => {
    renderApp("/app-settings");
    expect(await screen.findByRole("heading", { name: "App settings" })).toBeInTheDocument();
    expect(await screen.findByLabelText("Metadata cache")).toHaveValue("on");
  });

  it("saves disabling the metadata cache", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const save = vi.spyOn(services.appSettings, "save");
    renderApp("/app-settings", services);

    const select = await screen.findByLabelText("Metadata cache");
    await user.selectOptions(select, "off");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(save).toHaveBeenCalledWith(expect.objectContaining({ disableMetadataCache: true }));
  });

  it("saves the subscription approval switch and maximum length", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const save = vi.spyOn(services.appSettings, "save");
    renderApp("/app-settings", services);

    await user.selectOptions(await screen.findByLabelText("Approver-created subscriptions"), "direct");
    const days = screen.getByLabelText("Longest subscription (days)");
    await user.clear(days);
    await user.type(days, "90");
    await user.click(screen.getByRole("button", { name: "Save" }));

    expect(save).toHaveBeenCalledWith({
      disableMetadataCache: false,
      subscriptionsRequireAdminApproval: false,
      subscriptionMaxDays: 90,
    });
  });

  it("is not on the Data access page anymore", async () => {
    renderApp("/data-access");
    await screen.findByRole("heading", { name: "Data access" });
    expect(screen.queryByLabelText("Metadata cache")).not.toBeInTheDocument();
  });
});
