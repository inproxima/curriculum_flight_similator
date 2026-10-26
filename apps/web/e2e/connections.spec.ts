import AxeBuilder from "@axe-core/playwright";
import { type Page, type Route } from "@playwright/test";
import { expect, test } from "./fixtures";

/** Connection map: levels, "none found" vs "not mapped", prerequisite ↔ later views, and mapping with the AI.
 *  The Web payload is the real one (SYNTHETIC seed) with a mapped state patched in; AI endpoints are mocked. */

const PROPOSED_KEY = "00000000-0000-0000-0000-00000000e2e1";

const aiStatus = (r: Route) =>
  r.fulfill({
    json: {
      implemented: true, providers: {}, allowlist: {}, retrieval_mode: "fts", message: "", limits: { monthly_budget_usd: 1, max_cost_per_job_usd: 2 }, spend: { month_usd: 0 },
      routes: ["extract", "synthesize", "pedagogy"].map((name) => ({ name, provider: "openai", model: "m", purpose: "", fallback: null, available: true, active: ["openai", `model-${name}`], unavailable_reasons: [] })),
    },
  });

type N = { id: string; key: string; type: string; mapping?: Record<string, unknown> | null };
type E = Record<string, unknown> & { id: string; source: string; target: string; type: string };

/** The AI examined SYN202-CO2 and found no program outcome; it surmised SYN202-CO1 → PLO3 (partial, unreviewed). */
async function patchWeb(page: Page) {
  await page.route("**/api/v1/versions/*/web*", async (route) => {
    const res = await route.fetch();
    const view = (await res.json()) as { nodes: N[]; edges: E[] };
    const by = new Map(view.nodes.map((n) => [n.key, n]));
    const co1 = by.get("SYN202-CO1")!;
    const co2 = by.get("SYN202-CO2")!;
    const plo3 = by.get("PLO3")!;
    co2.mapping = { ...(co2.mapping ?? {}), contributes_to: { best: null, count: 0, verdict: "none", reason: "No program outcome concerns enzyme kinetics." } };
    const like = view.edges.find((e) => e.type === "contributes_to" && e.source === co1.id)!;
    view.edges.push({
      ...like, id: PROPOSED_KEY, key: PROPOSED_KEY, target: plo3.id, provenance: "proposed", review_state: "proposed", basis: "interpretation",
      connection: "partial", rationale: "Pathway regulation experiments touch on design. — “metabolic pathways”", level: null,
    });
    await route.fulfill({ response: res, json: view });
  });
}

async function openDraft(page: Page, path: string) {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.goto(path);
  const program = page.getByRole("combobox", { name: "Program" });
  await program.selectOption((await program.locator("option", { hasText: "Synthetic Biomedical Sciences" }).getAttribute("value"))!);
  const version = page.getByRole("combobox", { name: "Curriculum version and cohort" });
  await version.selectOption((await version.locator("option", { hasText: "2025–26 (synthetic draft)" }).getAttribute("value"))!);
}

test("connections page: levels, none found, across courses, and mapping a course with the AI (mocked)", async ({ page }) => {
  await page.route("**/api/v1/ai/status", aiStatus);
  await patchWeb(page);
  const previews: string[] = [];
  await page.route("**/api/v1/versions/*/ai-map/preview*", (r) => {
    previews.push(r.request().url());
    return r.fulfill({ json: { route: "synthesize", model: "model-synthesize", calls: 3, estimate_usd: 0.42, courses: 1, elements: 5, draft: true, cost_cap_usd: 2, batches: [] } });
  });
  let posted: { course_ids: string[] | null } | null = null;
  let stage: "none" | "running" | "stopping" | "done" = "none";
  let versionId = "";
  await page.route("**/api/v1/versions/*/ai-map", (r) => {
    posted = r.request().postDataJSON();
    stage = "running";
    return r.fulfill({ status: 202, json: { job_id: "jm", calls: 3, estimate_usd: 0.42, cost_cap_usd: 2, model: "model-synthesize" } });
  });
  let cancels = 0;
  await page.route("**/api/v1/jobs/jm/cancel", (r) => {
    cancels += 1;
    stage = "stopping";
    return r.fulfill({ json: mapJob("running", { cancel_requested: true }) });
  });
  // the job center's view of this user's AI jobs (the job itself is scripted: running, stopping, done)
  const mapJob = (status: string, extra: Record<string, unknown> = {}) => ({
    id: "jm", kind: "ai_map_alignment", status, current_stage: "mapping", progress: status === "running" ? 0.67 : 1, cancel_requested: false,
    attempts: 1, payload: { curriculum_version_id: versionId, course_ids: ["x"], label: "SYN 202", version_label: "2025–26 (synthetic draft)" },
    result: null, error: null, created_at: new Date(Date.now() - 95_000).toISOString(), updated_at: new Date().toISOString(),
    last_message: "Call 2/3: SYN 202 → later courses", ...extra,
  });
  await page.route(/\/api\/v1\/jobs\?.*kind=ai_/, (r) =>
    r.fulfill({
      json:
        stage === "none" ? [] : stage === "running" ? [mapJob("running")] : stage === "stopping" ? [mapJob("running", { cancel_requested: true })]
          : [mapJob("succeeded", { last_message: "finished", result: { proposed: 4, by_level: { direct: 1, strong: 2, partial: 1 }, verdicts: { linked: 3, none: 2 }, cost_usd: 0.38 } })],
    }));

  await openDraft(page, "/connections");
  versionId = await page.getByRole("combobox", { name: "Curriculum version and cohort" }).inputValue();
  await expect(page.getByRole("heading", { name: "Connection map" })).toBeVisible();
  const matrix = page.getByRole("table", { name: "Course outcome by program outcome connections" });
  await expect(matrix.getByRole("button", { name: "SYN101-CO1 → PLO1: Direct" })).toBeVisible(); // documented
  // surmised by the AI: partial, marked AI?, reviewable in place
  await matrix.getByRole("button", { name: "SYN202-CO1 → PLO3: Partial / surmised, proposed by the AI" }).click();
  const detail = page.getByRole("complementary", { name: "Selected connection" });
  await expect(detail.getByText("AI? proposed")).toBeVisible();
  await expect(detail.getByText("Review this proposed link")).toBeVisible();
  await expect(detail.getByRole("button", { name: "Accept" })).toBeEnabled();
  // examined and nothing found is not the same as not examined
  const co2 = matrix.getByRole("row", { name: /SYN202-CO2/ });
  await co2.getByRole("button", { name: /none found/ }).click();
  await expect(detail.getByText("The AI found no connection: No program outcome concerns enzyme kinetics.")).toBeVisible();
  // an assessment linked to nothing and never examined
  await page.getByRole("tab", { name: "Assessments → outcomes" }).click();
  await expect(page.getByRole("row", { name: /Tutorial participation/ }).getByText("? not mapped")).toBeVisible();

  // one course, horizontally: what it builds on (prerequisite SYN 102) and what builds on it later
  await page.getByRole("combobox", { name: "Course", exact: true }).selectOption({ label: "SYN 202 — Metabolic Biochemistry" });
  await page.getByRole("tab", { name: "Across courses (earlier ↔ later)" }).click();
  const across = page.getByRole("table", { name: "SYN 202: topics and outcomes across courses" });
  await expect(across.getByRole("button", { name: "SYN202-CO1 ← SYN 102: Direct" })).toBeVisible();

  const axe = await new AxeBuilder({ page }).include(".connections-page").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);

  // map just this course: the cost is shown first, and confirmed before anything is sent
  await expect(page.getByText("3 call(s) · about $0.42 · 5 element(s)")).toBeVisible();
  expect(previews.at(-1)).toContain("course_id=");
  let asked = "";
  page.once("dialog", (d) => {
    asked = d.message();
    void d.accept();
  });
  await page.getByRole("button", { name: "Map SYN 202's connections (AI)" }).click();
  expect(asked).toContain("about $0.42");
  expect(posted!.course_ids).toHaveLength(1);
  await expect(page.getByText(/Started\. Progress and the result are shown at the bottom right/)).toBeVisible();

  // while it runs: a progress card on every page, the tab title, and the button can't start a second run
  const jobs = page.getByRole("region", { name: "Background jobs" });
  await expect(jobs.getByText("Mapping connections")).toBeVisible();
  await expect(jobs.getByText("Call 2/3: SYN 202 → later courses")).toBeVisible();
  await expect(jobs.getByRole("progressbar", { name: "Mapping connections progress" })).toHaveAttribute("aria-valuenow", "67");
  await expect(page.getByRole("button", { name: "Mapping… 67%" })).toBeDisabled();
  await expect(page).toHaveTitle(/^⏳ 67% · /);
  await page.getByRole("link", { name: "Outcomes" }).click(); // another page: the card stays
  await expect(jobs.getByText("Mapping connections")).toBeVisible();
  const axeJobs = await new AxeBuilder({ page }).include(".job-center").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axeJobs.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);
  page.once("dialog", (d) => void d.accept());
  await jobs.getByRole("button", { name: "Stop" }).click();
  await expect(jobs.getByText("stopping…")).toBeVisible();
  expect(cancels).toBe(1);

  // when it ends: the card turns into a notification with the result, which stays until dismissed
  stage = "done";
  await expect(jobs.getByText("Connection map ready")).toBeVisible();
  await expect(jobs.getByText("4 links proposed: 1 direct · 2 strong · 1 partial")).toBeVisible();
  await expect(jobs.getByText(/2 “no connection found” · 1 min · \$0\.38/)).toBeVisible();
  await expect(page).toHaveTitle(/^✓ /);
  await jobs.getByRole("button", { name: "Open Connections" }).click();
  await expect(page).toHaveURL(/\/connections$/);
  await expect(jobs).toBeHidden(); // opening it dismisses it
  await page.reload();
  await expect(page.getByRole("heading", { name: "Connection map" })).toBeVisible();
  await expect(jobs).toBeHidden(); // and it stays dismissed
  await page.unrouteAll({ behavior: "ignoreErrors" });
});

test("web: nodes carry their mapped state, links are coloured by level, and the legend explains both", async ({ page }) => {
  await page.route("**/api/v1/ai/status", aiStatus);
  await patchWeb(page);
  await openDraft(page, "/");
  await expect(page.getByRole("group", { name: "Alignment scorecard" })).toBeVisible();
  await page.getByRole("button", { name: "Columns", exact: true }).click(); // node chips belong to the column layout
  await page.getByRole("searchbox", { name: "Search" }).fill("SYN 202");
  await page.getByRole("option", { name: /SYN 202 — Metabolic Biochemistry/ }).first().click();

  const co2 = page.locator(".react-flow__node").filter({ has: page.locator(".code", { hasText: "SYN202-CO2" }) });
  await expect(co2.locator(".map-chip.none")).toHaveText(/no program outcome/);
  await expect(co2.locator(".map-chip.none")).toHaveAttribute("title", /No program outcome concerns enzyme kinetics/);
  // links are labelled with their level (which edge comes first in the DOM depends on seeded ids)
  await expect(page.locator('.react-flow__edge[aria-label*="Direct connection"]').filter({ visible: true }).first()).toBeVisible();

  const layers = page.getByRole("complementary", { name: "Layers" });
  await expect(layers.getByText("Connection level")).toBeVisible();
  await expect(layers.getByRole("checkbox", { name: /Partial \/ surmised/ })).toBeChecked();
  await expect(layers.getByRole("radio", { name: "level" })).toBeChecked();

  await page.getByRole("button", { name: "Legend" }).click();
  await expect(page.getByText("Colour = connection level:")).toBeVisible();
  await expect(page.locator(".web-legend").getByText("none found")).toBeVisible();

  // inspector: the course's horizontal view lists each topic/outcome with what it builds on and what builds on it
  const inspector = page.getByRole("complementary", { name: "Inspector" });
  await inspector.getByRole("tab", { name: "Before ↔ after" }).click();
  await expect(inspector.getByRole("columnheader", { name: "← builds on" })).toBeVisible();
  await page.unrouteAll({ behavior: "ignoreErrors" });
});
