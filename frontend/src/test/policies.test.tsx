import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { fakeServices, renderApp, testPolicies, testPolicyDetail } from "./renderApp";

describe("Policies explorer", () => {
  it("separates metastore and catalog policies into tabs", async () => {
    const user = userEvent.setup();
    renderApp("/policies");

    expect(await screen.findByRole("heading", { name: "Policies" })).toBeInTheDocument();
    expect(await screen.findByText("metastore_default_mask")).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: /Metastore/ })).toHaveAttribute("aria-selected", "true");
    expect(screen.queryByText("approved_models")).not.toBeInTheDocument();

    await user.click(screen.getByRole("tab", { name: /Catalog/ }));
    expect(await screen.findByText("approved_models")).toBeInTheDocument();
    expect(screen.queryByText("metastore_default_mask")).not.toBeInTheDocument();
    expect(screen.getByText("system.ai")).toBeInTheDocument();
  });

  it("opens policy detail", async () => {
    renderApp(`/policies/${testPolicies.catalog[0]!.id}`);
    expect(await screen.findByRole("heading", { name: "approved_models" })).toBeInTheDocument();
    expect(screen.getByText("Catalog-specific ABAC policy")).toBeInTheDocument();
    expect(screen.getByText("Only approved model families")).toBeInTheDocument();
  });

  it("paginates affected assets and shows catalog/schema/table/column", async () => {
    const user = userEvent.setup();
    const assets = Array.from({ length: 30 }, (_, index) => ({
      assetType: "COLUMN",
      fullName: `sandbox.default.t.col_${index}`,
      catalogName: "sandbox",
      schemaName: "default",
      tableName: "t",
      columnName: `col_${index}`,
      matchedTagKey: "data_type",
      matchedTagValue: "string",
    }));
    renderApp(
      `/policies/${testPolicies.catalog[0]!.id}`,
      fakeServices({
        policy: {
          ...testPolicyDetail,
          affectedAssets: assets,
          affectedAssetsTruncated: false,
          affectedAssetsNote: null,
        },
      }),
    );

    const table = await screen.findByRole("table", { name: "Affected assets" });
    expect(within(table).getByText("Catalog")).toBeInTheDocument();
    expect(within(table).getByText("Schema")).toBeInTheDocument();
    expect(within(table).getByText("Table")).toBeInTheDocument();
    expect(within(table).getByText("Column")).toBeInTheDocument();
    expect(within(table).getByText("col_0")).toBeInTheDocument();
    expect(within(table).queryByText("col_10")).not.toBeInTheDocument();
    expect(screen.getByText("1–10 of 30")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(within(table).getByText("col_10")).toBeInTheDocument();
    expect(screen.getByText("11–20 of 30")).toBeInTheDocument();
  });

  it("shows an error when policies fail to load", async () => {
    renderApp("/policies", fakeServices({ policies: new Error("warehouse unavailable") }));
    expect(await screen.findByText("Could not load policies")).toBeInTheDocument();
    expect(screen.getByText("warehouse unavailable")).toBeInTheDocument();
  });
});
