import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";
import { SearchableSelect } from "@/components/ui/SearchableSelect";

function Harness({ options }: { options: string[] }) {
  const [value, setValue] = useState("");
  return <SearchableSelect aria-label="Path" value={value} onChange={setValue} options={options} />;
}

describe("SearchableSelect", () => {
  it("sorts options and filters as you type", async () => {
    const user = userEvent.setup();
    render(<Harness options={["zeta.path", "alpha.path", "public.shared", "beta.path"]} />);

    await user.click(screen.getByRole("combobox", { name: "Path" }));
    const list = await screen.findByRole("listbox", { name: "Path" });
    const labels = within(list)
      .getAllByRole("option")
      .map((node) => node.textContent);
    expect(labels[0]).toBe("Select…");
    expect(labels.slice(1)).toEqual(["alpha.path", "beta.path", "public.shared", "zeta.path"]);

    await user.type(screen.getByRole("combobox", { name: "Path" }), "public");
    expect(within(screen.getByRole("listbox")).getAllByRole("option").map((n) => n.textContent)).toEqual([
      "public.shared",
    ]);
    await user.click(screen.getByRole("option", { name: "public.shared" }));
    expect(screen.getByRole("combobox", { name: "Path" })).toHaveValue("public.shared");
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });
});
