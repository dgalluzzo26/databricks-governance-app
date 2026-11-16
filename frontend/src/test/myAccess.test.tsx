import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeServices, renderApp } from "./renderApp";

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
});

