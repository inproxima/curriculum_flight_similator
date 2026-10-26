import AxeBuilder from "@axe-core/playwright";
import { type Page } from "@playwright/test";
import { expect, test } from "./fixtures";

/** Alignment Web home, change → simulate, and the four simulation views. SYNTHETIC fixture only. */

async function selectSynthetic(page: Page) {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.goto("/");
  const program = page.getByRole("combobox", { name: "Program" });
  const value = await program.locator("option", { hasText: "Synthetic Biomedical Sciences" }).getAttribute("value");
  await program.selectOption(value!);
  const version = page.getByRole("combobox", { name: "Curriculum version and cohort" });
  const v1 = await version.locator("option", { hasText: "2024–25 (synthetic) · published" }).getAttribute("value");
  await version.selectOption(v1!);
  await expect(page.getByRole("group", { name: "Alignment scorecard" })).toBeVisible();
  await page.getByRole("button", { name: "Columns", exact: true }).click(); // these tests cover the column layout
}

async function pick(page: Page, query: string, option: RegExp) {
  await page.getByRole("searchbox", { name: "Search" }).fill(query);
  await page.getByRole("option", { name: option }).first().click();
}

const inspector = (page: Page) => page.getByRole("complementary", { name: "Inspector" });

test.beforeEach(async ({ page }) => selectSynthetic(page));

test("home is the alignment web: program outcomes, courses, and every element", async ({ page }) => {
  await expect(page.getByRole("main", { name: "Alignment web" })).toBeVisible();
  await expect(page.locator(".web-node.plo")).toHaveCount(10);
  expect(await page.locator(".web-node.course").count()).toBeGreaterThan(20);
  await expect(page.locator(".band-label", { hasText: "Year 1 · Fall" })).toBeVisible();
  await expect(inspector(page).getByRole("heading", { name: "Weakest program outcomes" })).toBeVisible();
});

test("selecting Collaboration shows its thread and focuses its neighbourhood", async ({ page }) => {
  await inspector(page).getByRole("button", { name: /PLO7: Collaboration/ }).click();
  await expect(inspector(page).getByText("Never reaches the 'assess' level").first()).toBeVisible();
  await expect(inspector(page).getByText(/No course outcome assesses this program outcome/)).toBeVisible();
  await expect(page.getByText(/Focused on/)).toBeVisible();
  const dimmed = await page.locator(".web-node.dim").count();
  expect(dimmed).toBeGreaterThan(20);
  await expect(page.locator(".web-node.hl", { hasText: "SYN340-CO1" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByText(/Focused on/)).toHaveCount(0);
});

test("layers filter the web", async ({ page }) => {
  const edges = () => page.locator(".react-flow__edge").count();
  await page.getByRole("button", { name: "Fit View" }).click();
  const before = await edges();
  await page.getByLabel("Layers").getByText("Assessment → course outcome").click();
  await expect.poll(edges).toBeLessThan(before);
  await page.getByLabel("Layers").getByText("Assessments & assignments").click();
  await expect(page.locator(".web-node.assessment")).toHaveCount(0);
  await page.getByRole("button", { name: "Reset layers" }).click();
  expect(await page.locator(".web-node.assessment").count()).toBeGreaterThan(10);
});

test("a course shows vertical and horizontal alignment, and a link explains its strength", async ({ page }) => {
  await pick(page, "SYN 210", /^course SYN 210/);
  await expect(inspector(page).getByRole("heading", { name: /SYN 210 —/ })).toBeVisible();
  await inspector(page).getByRole("tab", { name: "Before ↔ after" }).click();
  await expect(inspector(page).getByText("Prepared by (earlier courses)")).toBeVisible();
  await expect(inspector(page).getByRole("button", { name: "SYN 110" }).first()).toBeVisible();
  await inspector(page).getByRole("tab", { name: "Outcomes ↕ program" }).click();
  await inspector(page).getByRole("button", { name: "why?" }).first().click();
  await expect(inspector(page).getByRole("heading", { name: "Why this strength?" })).toBeVisible();
  await expect(inspector(page).getByText("trust × intensity × support")).toBeVisible();
});

test("change an assignment, simulate, and follow the ripple", async ({ page }) => {
  await pick(page, "Communication portfolio", /assessment SYN420-A1/);
  await inspector(page).getByText("Change this…").click();
  await inspector(page).getByRole("button", { name: "Remove assessment" }).click();
  await expect(page.getByRole("combobox", { name: "Baseline or scenario" })).toContainText("What-if from the alignment web");
  const simulate = page.getByRole("button", { name: /Simulate/ });
  await expect(simulate).toBeEnabled({ timeout: 15_000 });
  await simulate.click();

  await expect(page.getByRole("tab", { name: "What changed" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("status").filter({ hasText: "removed" })).toBeVisible();
  await expect(page.locator(".web-node.st-removed", { hasText: "Communication portfolio" })).toBeVisible();

  await page.getByRole("tab", { name: "Ripple" }).click();
  const slider = page.getByRole("slider", { name: "Ripple step" });
  await slider.fill("0");
  await expect(page.getByText(/Step 0 — the change: SYN420-A1 removed/)).toBeVisible();
  await slider.fill("1");
  await expect(page.getByText(/Step 1: .*SYN420-CO1 weakens/)).toBeVisible();

  await page.getByRole("tab", { name: "Before / after" }).click();
  const table = page.getByRole("table", { name: "Scorecard before and after" });
  await expect(table).toContainText("Avg strength");
  await expect(table).toContainText("worse");

  await page.getByRole("tab", { name: "Impact list" }).click();
  await expect(page.getByText(/SYN420-CO1 is no longer assessed/)).toBeVisible();
  await page.getByRole("button", { name: "Show me" }).first().click();
  await expect(page.getByRole("tab", { name: "Ripple" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByText(/Highlighting:/)).toBeVisible();

  const results = await new AxeBuilder({ page }).include(".sim-panel").withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .exclude(".react-flow__renderer").analyze();
  expect(results.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
});

test("ask the assistant for a change, apply the proposal, then simulate (network-mocked)", async ({ page }) => {
  const ids = await page.evaluate(async () => {
    const progs = await (await fetch("/api/v1/programs")).json();
    const p = progs.find((x: { is_synthetic: boolean }) => x.is_synthetic);
    const vs = await (await fetch(`/api/v1/programs/${p.id}/versions`)).json();
    const v = vs.find((x: { label: string }) => x.label.startsWith("2024–25"));
    const ents = await (await fetch(`/api/v1/versions/${v.id}/entities`)).json();
    return { a: ents.find((x: { key: string }) => x.key === "SYN340-A1").id };
  });
  const convId = "00000000-0000-0000-0000-00000000beef";
  const envelope = {
    answer: "Removing the team project report leaves Collaboration without any assessed evidence.",
    insufficient_documentation: false, citations: [], highlighted_entities: [], highlighted_relationship_keys: [], findings: [],
    assumptions: [], unanswered_questions: [],
    proposed_changes: [{ change: { op: "remove_assessment", entity_id: ids.a, assumptions: [] }, summary: "Remove assessment Team project report", model_summary: null, rationale: "Explore the dependency", assumptions: [] }],
    validation: { dropped_citations: [], dropped_entities: [], dropped_relationships: [], dropped_proposals: [], warnings: [] },
    meta: { mode: "simulate", route: "pedagogy", provider: "anthropic", model: "claude-sonnet-5", cost_usd: 0, input_tokens: 1, output_tokens: 1, tool_calls: [], fallback_from: null, label: "AI interpretation", model_run_id: "x" },
  };
  let asked = false;
  await page.route("**/api/v1/ai/status", (r) => r.fulfill({ json: { implemented: true, providers: {}, allowlist: {}, retrieval_mode: "fts", message: "", limits: { monthly_budget_usd: 1, max_cost_per_job_usd: 1 }, spend: { month_usd: 0 },
    routes: ["pedagogy", "extract", "synthesize"].map((name) => ({ name, provider: "anthropic", model: "m", purpose: "", fallback: null, available: true, active: ["anthropic", "m"], unavailable_reasons: [] })) } }));
  await page.route("**/api/v1/conversations", (r) => r.fulfill({ status: 201, json: { id: convId, scenario_id: null } }));
  await page.route(`**/api/v1/conversations/${convId}/messages`, (r) => {
    if (r.request().method() === "POST") {
      asked = true;
      return r.fulfill({ status: 202, json: { message_id: "m1", job_id: "j2" } });
    }
    return r.fulfill({ json: { messages: asked ? [{ id: "m2", role: "assistant", content: envelope, created_at: "" }] : [] } });
  });
  await page.route("**/api/v1/jobs/j2/stream", (r) => r.fulfill({ contentType: "text/event-stream",
    body: 'id: 1\nevent: job\ndata: {"message":"Checking alignment","status":"progress","job":{"status":"succeeded"}}\n\nevent: end\ndata: {}\n\n' }));
  // the AI status is fetched (and cached) on first load, so reload now that the mocks are in place
  await page.reload();
  await expect(page.getByRole("group", { name: "Alignment scorecard" })).toBeVisible();

  await page.getByRole("textbox", { name: "What would you like to change?" }).fill("What if we drop the team project report?");
  await page.getByRole("button", { name: "Ask AI" }).click();
  const tray = page.locator(".composer-tray");
  await expect(tray.getByText(/Collaboration without any assessed evidence/)).toBeVisible();
  await tray.getByRole("button", { name: "Apply in new scenario" }).click();
  await expect(tray.getByText(/Created a new scenario/)).toBeVisible();
  await page.getByRole("button", { name: /Simulate/ }).click();
  await page.getByRole("tab", { name: "Impact list" }).click();
  await expect(page.getByText(/SYN340-CO1 is no longer assessed/)).toBeVisible();
});
