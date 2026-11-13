/**
 * Captures the User and Approver guide screenshots into `public/help-images/`.
 *
 *   cd frontend && npx vite --port 5199 &
 *   npm i --no-save playwright && npx playwright install chromium
 *   node screenshots/capture.cjs
 *
 * `HARNESS_URL` overrides the dev server (default http://localhost:5199), `CHROMIUM_PATH` a
 * local Chromium binary, and `PLAYWRIGHT_MODULE` where Playwright is installed.
 */
const path = require("node:path");
const { chromium } = require(process.env.PLAYWRIGHT_MODULE ?? "playwright");

const base = `${process.env.HARNESS_URL ?? "http://localhost:5199"}/screenshots/index.html`;
const outDir = path.resolve(__dirname, "../public/help-images");

const card = (page, title) =>
  page.locator("section", { has: page.getByRole("heading", { name: title, exact: true }) }).first();

async function open(page, role, route) {
  await page.goto(`${base}?role=${role}&path=${encodeURIComponent(route)}`);
  await page.getByRole("navigation").first().waitFor();
  await page.waitForTimeout(400);
}

async function analyzeCustomers(page) {
  await open(page, "user", "/access-explorer");
  await page.getByRole("treeitem", { name: /sales/ }).first().click();
  await page.getByRole("treeitem", { name: /crm/ }).click();
  await page.getByRole("treeitem", { name: /customers/ }).click();
  await card(page, "Suggested attributes").waitFor();
  const expiry = page.getByLabel("Requested expiry");
  await expiry.fill("2027-02-28T17:00");
  await expiry.blur();
}

const scenes = [
  ["user-home", async (page) => open(page, "user", "/")],
  [
    "user-access-explorer",
    async (page) => {
      await analyzeCustomers(page);
      await page.mouse.move(0, 0);
    },
  ],
  [
    "user-suggestions",
    async (page) => {
      await analyzeCustomers(page);
      return card(page, "Suggested attributes");
    },
  ],
  [
    "user-row-filter-values",
    async (page) => {
      await analyzeCustomers(page);
      await page.getByLabel("Find row-filter values").fill("CA");
      await page.getByRole("button", { name: /Select matches/ }).click();
      return card(page, "Suggested attributes");
    },
  ],
  [
    "user-columns",
    async (page) => {
      await analyzeCustomers(page);
      return card(page, "Columns");
    },
  ],
  [
    "user-row-filter",
    async (page) => {
      await analyzeCustomers(page);
      return card(page, "Row filter");
    },
  ],
  ["user-my-attributes", async (page) => open(page, "user", "/identity?tab=mine")],
  [
    "user-request-access",
    async (page) => {
      await open(page, "user", "/identity?tab=request");
      const form = card(page, "Request access");
      await form.getByLabel("Sensitivity path").selectOption("finance.budget.PII.Name");
      await form.getByLabel("Requested expiry").fill("2027-02-28T17:00");
      await form.getByLabel("Justification").fill("Budget owner report for FY27 planning");
      await form.getByLabel("Justification").blur();
      return form;
    },
  ],
  ["user-my-requests", async (page) => open(page, "user", "/identity?tab=my-requests")],
  ["approver-home", async (page) => open(page, "approver", "/")],
  ["approver-approvals", async (page) => open(page, "approver", "/approvals")],
  [
    "approver-attribute-explorer",
    async (page) => {
      await open(page, "approver", "/attribute-explorer");
      await page.getByLabel("Attribute").selectOption("sensitivity");
      await page.getByLabel("Value").selectOption("finance.budget");
      await page.getByRole("button", { name: "Analyze" }).click();
      await card(page, "Holders").waitFor();
    },
  ],
  [
    "approver-row-filter-overview",
    async (page) => {
      await open(page, "approver", "/attribute-explorer");
      await page.getByLabel("Attribute").selectOption("state");
      await page.getByRole("button", { name: "Analyze" }).click();
      await card(page, "Principals").waitFor();
    },
  ],
];

(async () => {
  const browser = await chromium.launch(
    process.env.CHROMIUM_PATH ? { executablePath: process.env.CHROMIUM_PATH } : {},
  );
  // Tall viewport so element shots never sit under the sticky top bar; page shots clip to `view`.
  const view = { width: 1520, height: 900 };
  const page = await browser.newPage({ viewport: { width: view.width, height: 2400 }, deviceScaleFactor: 1.5 });
  page.on("pageerror", (error) => console.error("pageerror:", error.message));
  const docsDir = path.resolve(__dirname, "../../docs");
  await page.route("**/__docs/*", (route) =>
    route.fulfill({ path: path.join(docsDir, path.basename(new URL(route.request().url()).pathname)) }),
  );
  const only = process.argv.slice(2);
  for (const [name, run] of scenes) {
    if (only.length && !only.includes(name)) continue;
    const target = await run(page);
    await page.waitForTimeout(300);
    const file = path.join(outDir, `${name}.png`);
    if (target) {
      await target.screenshot({ path: file });
    } else {
      await page.evaluate(() => window.scrollTo(0, 0));
      await page.screenshot({ path: file, clip: { x: 0, y: 0, ...view } });
    }
    console.log("wrote", path.relative(process.cwd(), file));
  }
  await browser.close();
})();
