import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { TablePager } from "@/components/ui";
import { useClientPagination } from "@/hooks/useClientPagination";

function PagedList({ items }: { items: string[] }) {
  const pager = useClientPagination(items, { resetKey: items.join("|") });
  return (
    <div>
      <ul aria-label="Rows">
        {pager.rows.map((item) => (
          <li key={item}>{item}</li>
        ))}
      </ul>
      <TablePager
        total={pager.total}
        page={pager.page}
        pageSize={pager.pageSize}
        onPageChange={pager.setPage}
        onPageSizeChange={pager.setPageSize}
      />
    </div>
  );
}

describe("TablePager", () => {
  it("defaults to 10 rows and can page and change page size", async () => {
    const user = userEvent.setup();
    const items = Array.from({ length: 25 }, (_, index) => `row-${index + 1}`);
    render(<PagedList items={items} />);

    expect(screen.getByText("1–10 of 25")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Rows" }).children).toHaveLength(10);

    await user.click(screen.getByRole("button", { name: "Next" }));
    expect(screen.getByText("11–20 of 25")).toBeInTheDocument();
    expect(screen.getByText("row-11")).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Rows per page"), "25");
    expect(screen.getByText("1–25 of 25")).toBeInTheDocument();
    expect(screen.getByRole("list", { name: "Rows" }).children).toHaveLength(25);
  });
});
