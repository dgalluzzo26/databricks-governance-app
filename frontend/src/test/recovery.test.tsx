import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import {
  fakeServices,
  renderApp,
  testRecoveryImportPreview,
  testRecoverySettings,
} from "./renderApp";

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
    expect(screen.getByRole("button", { name: "Recover" })).toBeDisabled();

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

    const pushButton = await screen.findByRole("button", { name: "Push to Git" });
    expect(pushButton).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Preview export" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Push to Git" })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "Push to Git" }));
    expect(await screen.findByText(/Committed c0ffee123456/)).toBeInTheDocument();
    expect(push).toHaveBeenCalledTimes(1);
  });
});

describe("Recovery import and recover", () => {
  it("imports from Git then recovers after confirmation", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    vi.spyOn(services.recovery, "getSettings").mockResolvedValue({
      ...testRecoverySettings,
      repoUrl: "https://github.com/acme/gov-backup.git",
    });
    const importFromGit = vi.spyOn(services.recovery, "importFromGit");
    const recover = vi.spyOn(services.recovery, "recover");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderApp("/recovery", services);

    const recoverButton = await screen.findByRole("button", { name: "Recover" });
    expect(recoverButton).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Import from Git" }));
    expect(await screen.findByText("identity/assignments.yaml")).toBeInTheDocument();
    expect(importFromGit).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.getByRole("button", { name: "Recover" })).toBeEnabled());

    await user.click(screen.getByRole("button", { name: "Recover" }));
    expect(confirm).toHaveBeenCalled();
    expect(await screen.findByText(/Recovered from Git/)).toBeInTheDocument();
    expect(recover).toHaveBeenCalledWith({ confirm: true });
    confirm.mockRestore();
  });

  it("warns when the app already has state and cancels when confirm is declined", async () => {
    const user = userEvent.setup();
    const services = fakeServices();
    vi.spyOn(services.recovery, "getSettings").mockResolvedValue({
      ...testRecoverySettings,
      repoUrl: "https://github.com/acme/gov-backup.git",
    });
    vi.spyOn(services.recovery, "importFromGit").mockResolvedValue({
      ...testRecoveryImportPreview,
      occupancy: {
        ...testRecoveryImportPreview.occupancy,
        appHasState: true,
        assignments: 12,
        definitions: 3,
        summary: "3 definitions, 12 assignments",
      },
    });
    const recover = vi.spyOn(services.recovery, "recover");
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(false);
    renderApp("/recovery", services);

    await user.click(await screen.findByRole("button", { name: "Import from Git" }));
    expect(await screen.findByText(/already has governance state/i)).toBeInTheDocument();
    expect(screen.getByText(/3 definitions, 12 assignments/)).toBeInTheDocument();

    await waitFor(() => expect(screen.getByRole("button", { name: "Recover" })).toBeEnabled());
    await user.click(screen.getByRole("button", { name: "Recover" }));
    expect(confirm).toHaveBeenCalled();
    expect(String(confirm.mock.calls[0]?.[0])).toMatch(/already has governance state/i);
    expect(recover).not.toHaveBeenCalled();
    confirm.mockRestore();
  });
});
