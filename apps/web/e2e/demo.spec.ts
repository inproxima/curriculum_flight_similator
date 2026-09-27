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
  // These tests use the SYNTHETIC fixture program (other, real programs may exist in the workspace).
  const program = page.getByRole("combobox", { name: "Program" });
  await expect(program.locator("option", { hasText: "Synthetic Biomedical Sciences" })).toHaveCount(1);
  const value = await program.locator("option", { hasText: "Synthetic Biomedical Sciences" }).getAttribute("value");
  await program.selectOption(value!);
  await expect(page.getByRole("combobox", { name: "Curriculum version and cohort" })).toContainText("2024–25 (synthetic)");
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

test("assistant panel renders a verified envelope (network-mocked, no model call)", async ({ page }) => {
  // Real span id so the evidence chip resolves against the running API.
  const span = await page.evaluate(async () => {
    const progs = await (await fetch("/api/v1/programs")).json();
    const p = progs.find((x: { is_synthetic: boolean }) => x.is_synthetic);
    const vs = await (await fetch(`/api/v1/programs/${p.id}/versions`)).json();
    const ents = await (await fetch(`/api/v1/versions/${vs[0].id}/entities`)).json();
    const e = ents.find((x: { key: string }) => x.key === "SYN 407");
    const d = await (await fetch(`/api/v1/versions/${vs[0].id}/entities/${e.id}`)).json();
    return { spanId: d.field_evidence.description[0].span_id, entityId: e.id };
  });
  const convId = "00000000-0000-0000-0000-00000000c0de";
  const envelope = {
    answer: "SYN 407 builds on SYN 310 and SYN 301 (formal prerequisites).",
    insufficient_documentation: false,
    citations: [{ span_id: span.spanId, quote: "Description: Student-led seminars", page: 7, document: "Synthetic Program Outline", synthetic: true }],
    highlighted_entities: [{ id: span.entityId, key: "SYN 407" }],
    highlighted_relationship_keys: [],
    findings: [{ title: "Formal requisite", explanation: "Documented rule.", basis: "source_supported" }],
    assumptions: ["Required path"],
    unanswered_questions: [],
    proposed_changes: [{ change: { op: "move_course", entity_id: span.entityId, year: 4, term: "winter", assumptions: [] }, summary: "Move SYN 407 from Y4 fall to Y4 winter", model_summary: "move", rationale: "Test", assumptions: [] }],
    validation: { dropped_citations: [{ ref: "span_x" }], dropped_entities: [], dropped_relationships: [], dropped_proposals: [], warnings: [] },
    meta: { mode: "explore", route: "extract", provider: "openai", model: "gpt-6-sol", cost_usd: 0.01, input_tokens: 1, output_tokens: 1, tool_calls: ["get_entity"], fallback_from: null, label: "AI interpretation", model_run_id: "x" },
  };
  let asked = false;
  await page.route("**/api/v1/conversations?*", (r) => r.fulfill({ json: [] }));
  await page.route("**/api/v1/conversations", (r) => r.fulfill({ status: 201, json: { id: convId, scenario_id: null } }));
  await page.route(`**/api/v1/conversations/${convId}/messages`, (r) => {
    if (r.request().method() === "POST") {
      asked = true;
      return r.fulfill({ status: 202, json: { message_id: "m1", job_id: "j1" } });
    }
    return r.fulfill({ json: { messages: asked ? [
      { id: "m1", role: "user", content: { text: "q", mode: "explore" }, created_at: "" },
      { id: "m2", role: "assistant", content: envelope, created_at: "" },
    ] : [] } });
  });
  await page.route("**/api/v1/jobs/j1/stream", (r) => r.fulfill({ contentType: "text/event-stream",
    body: 'id: 1\nevent: job\ndata: {"message":"Reading SYN 407","status":"progress","job":{"status":"succeeded"}}\n\nevent: end\ndata: {}\n\n' }));

  await page.getByRole("button", { name: "Assistant", exact: true }).click();
  await page.getByRole("textbox", { name: "Ask the assistant" }).fill("What does SYN 407 build on?");
  await page.getByRole("button", { name: "Send" }).click();
  const panel = page.getByRole("complementary", { name: "Assistant" });
  await expect(panel.getByText("SYN 407 builds on SYN 310")).toBeVisible();
  await expect(panel.getByText("AI interpretation", { exact: true }).first()).toBeVisible();
  await expect(panel.getByText("1 item(s) removed by verification")).toBeVisible();
  await expect(panel.getByRole("button", { name: /Synthetic Program Outline p\.7/ })).toBeVisible();
  await panel.getByRole("button", { name: "Highlight on map" }).click();
  await expect(page.getByText(/Highlighting:/)).toBeVisible();
  await expect(panel.getByRole("button", { name: /Apply in new scenario/ })).toBeVisible();
});
