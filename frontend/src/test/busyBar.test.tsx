import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { IdentityAttributeAssignment } from "@/api/types";
import { fakeServices, renderApp } from "./renderApp";

describe("global busy indicator", () => {
  it("shows a progress bar and status while a mutation is in flight", async () => {
    const user = userEvent.setup();
    let resolveRevoke!: (value: IdentityAttributeAssignment) => void;
    const revokePromise = new Promise<IdentityAttributeAssignment>((resolve) => {
      resolveRevoke = resolve;
    });
    const services = fakeServices();
    services.identityAttributes.revoke = vi.fn().mockReturnValue(revokePromise);

    renderApp("/identity?tab=assignments", services);

    const revokeButton = await screen.findByRole("button", { name: "Revoke" });
    await user.click(revokeButton);

    expect(await screen.findByRole("button", { name: "Revoking…" })).toBeDisabled();
    expect(await screen.findByRole("progressbar")).toBeInTheDocument();
    expect(await screen.findByText("Revoking attribute…")).toBeInTheDocument();

    resolveRevoke({
      assignmentId: "a1",
      principalName: "ana.lopez@example.com",
      principalType: "USER",
      attributeKey: "dga_domain",
      attributeValue: "finance.budget",
      source: "MANUAL",
      grantedBy: "admin",
      justification: null,
      expiresAt: null,
      createdAt: "2026-01-01T00:00:00Z",
      revokedAt: "2026-01-02T00:00:00Z",
      revokedBy: "admin",
      active: false,
    });
    await waitFor(() => {
      expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
    });
  });
});
