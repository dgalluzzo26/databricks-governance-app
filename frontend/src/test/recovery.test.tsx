import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { fakeServices, renderApp, testRecoverySettings } from "./renderApp";

describe("Recovery export preview", () => {
  it("shows Git target settings and previews the export without writing", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    const push = vi.spyOn(services.recovery, "pushExport");
    renderApp("/recovery", services);
    expect(await screen.findByRole("heading", { name: "Recovery" })).toBeInTheDocument();
    expect(await screen.findByLabelText(/Repository URL/)).toBeInTheDocument();
    expect(await screen.findByDisplayValue("governance-export")).toBeInTheDocument();

    const pushButton = screen.getByRole("button", { name: "Push to Git" });
    expect(pushButton).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Preview export" }));
    expect(await screen.findByText("manifest.yaml")).toBeInTheDocument();
    expect(screen.getByText("masking/config.yaml")).toBeInTheDocument();
    expect(screen.getByText(/joinable salt is not exported/i)).toBeInTheDocument();
    expect(screen.getByLabelText("YAML for masking/config.yaml")).toHaveTextContent("<redacted>");
    expect(push).not.toHaveBeenCalled();
  });

  it("pushes the export after a preview when a repository is saved", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    vi.spyOn(services.recovery, "getSettings").mockResolvedValue({
      ...testRecoverySettings,
      repoUrl: "https://github.com/acme/gov-backup.git",
    });
    const push = vi.spyOn(services.recovery, "pushExport");
    renderApp("/recovery", services);

    const pushButton = await screen.findByRole("button", {
      name: "Push to Git",
    });
    expect(pushButton).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Preview export" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Push to Git" })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "Push to Git" }));
    expect(await screen.findByText(/Committed c0ffee123456/)).toBeInTheDocument();
    expect(push).toHaveBeenCalledTimes(1);
  });
});
