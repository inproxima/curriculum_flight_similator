import { expect, test, type Page } from "@playwright/test";

async function pickEntity(page: Page, text: string, key: string) {
  const box = page.getByRole("searchbox", { name: "Search" });
  await box.fill(text);
  await page.getByRole("option", { name: new RegExp(`^course ${key}`) }).click();
  await expect(page.getByRole("heading", { name: new RegExp(`^${key} —`) })).toBeVisible();
}

test.beforeEach(async ({ page }) => {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.goto("/");
});

test("select program and version; synthetic data is labelled", async ({ page }) => {
  await expect(page.getByRole("status")).toContainText("not University of Calgary curriculum data");
  await expect(page.getByRole("combobox", { name: "Program" })).toContainText("[SYNTHETIC]");
  await expect(page.getByRole("combobox", { name: "Curriculum version and cohort" })).toContainText("published");
  await expect(page.locator(".course-node").first()).toBeVisible();
  expect(await page.locator(".course-node").count()).toBeGreaterThan(20);
  await expect(page.getByText(/Year 1 · Fall/i)).toBeVisible();
});

test("inspect a course and open its evidence", async ({ page }) => {
  await pickEntity(page, "SYN 407", "SYN 407");
  await expect(page.getByText("Documented description")).toBeVisible();
  await page.getByRole("tabpanel").locator(".evidence-chip").first().click();
  const dlg = page.getByRole("dialog");
  await expect(dlg).toContainText("page 7");
  await expect(dlg.locator(".pdf-highlight").first()).toBeVisible({ timeout: 15_000 });
  await page.keyboard.press("Escape");
});

test("highlight expected preparation and downstream learning", async ({ page }) => {
  await pickEntity(page, "SYN 407", "SYN 407");
  await page.getByRole("tab", { name: "Expected preparation" }).click();
  await expect(page.getByText("Through formal requisites")).toBeVisible();
  await expect(page.getByText(/does not establish mastery/)).toBeVisible();
  await page.getByRole("button", { name: "Highlight on map" }).click();
  await expect(page.getByText(/Highlighting:/)).toBeVisible();
  expect(await page.locator(".course-node.dim").count()).toBeGreaterThan(5);
  await page.getByRole("tab", { name: "Future use" }).click();
  await expect(page.getByText(/Later courses relying on this/)).toBeVisible();
});

test("create and edit a scenario, analyse, reload and reopen", async ({ page }) => {
  const title = `E2E remove SYN 310 ${Date.now()}`;
  await page.getByRole("link", { name: "Scenarios" }).click();
  await page.getByRole("textbox", { name: "New scenario title" }).fill(title);
  await page.getByRole("button", { name: "Create scenario" }).click();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();

  await page.getByRole("link", { name: "Map", exact: true }).click();
  await pickEntity(page, "SYN 310", "SYN 310");
  await page.getByRole("tab", { name: "Scenario edit" }).click();
  await page.getByRole("button", { name: /Remove SYN 310/ }).click();
  await expect(page.getByRole("dialog", { name: "Preview change" })).toContainText("Remove SYN 310");
  await page.getByRole("textbox", { name: "Assumption" }).fill("Appraisal content moves elsewhere");
  await page.getByRole("button", { name: "Apply to scenario" }).click();
  await expect(page.locator(".course-node.removed")).toHaveCount(1, { timeout: 15_000 });

  await page.getByRole("link", { name: "Scenarios" }).click();
  await page.getByRole("button", { name: "Run deterministic analysis" }).click();
  await expect(page.getByText("SYN 407 prerequisite can no longer be satisfied")).toBeVisible();
  await expect(page.getByText(/Direct documented consequences/)).toBeVisible();

  await page.reload();
  await expect(page.getByRole("heading", { name: title })).toBeVisible();
  await expect(page.getByText(/Remove course SYN 310/)).toBeVisible();
  await expect(page.getByText("Assumption: Appraisal content moves elsewhere")).toBeVisible();

  await page.getByRole("button", { name: "Show on map" }).first().click();
  await expect(page.getByText(/Highlighting:/)).toBeVisible();
});

test("accessible table alternative is keyboard navigable", async ({ page }) => {
  await page.getByRole("link", { name: "Table" }).click();
  const table = page.getByRole("table", { name: "Courses" });
  await expect(table).toBeVisible();
  const rows = table.locator("tbody tr");
  expect(await rows.count()).toBeGreaterThan(20);
  await rows.first().focus();
  await page.keyboard.press("Tab");
  await expect(rows.nth(1)).toHaveAttribute("aria-selected", "true");
  await expect(table).toContainText("[formal]");
  await expect(table).toContainText("[inferred");
});

test("outcome matrix explains its denominator", async ({ page }) => {
  await page.getByRole("link", { name: "Outcomes" }).click();
  await expect(page.getByText(/Documented coverage: \d+\/8 program outcomes/)).toBeVisible();
  await expect(page.getByText(/divided by all program outcomes/).first()).toBeVisible();
  await page.getByRole("tab", { name: "Potential issues" }).click();
  await expect(page.getByText(/PLO6 .* has no documented assessment/)).toBeVisible();
});
