import AxeBuilder from "@axe-core/playwright";
import { type Page, type Route } from "@playwright/test";
import { expect, test } from "./fixtures";

/** Course outline import from the course inspector (AI endpoints network-mocked; SYNTHETIC data only). */

const IMP = "00000000-0000-0000-0000-0000000000a1";
const SPAN = "00000000-0000-0000-0000-0000000000b1";

function plan() {
  const ev = (quote: string) => [{ span_id: SPAN, page: 1, quote, source: "outline", field: "statement" }];
  const base = { noop: false, selectable: true, default_selected: true, blocked_reason: null, proposed: null, key_preview: null, diff: [], evidence: [], link: null, depends_on: [], excludes: [], fills: [], carries_links: 0, drops_links: 0, agent: null };
  const end = (key: string | null, label: string, type: string, ref: string) => ({ ref, entity_id: key ? `id-${key}` : null, key, type, label });
  return {
    course: { id: "c", key: "SYN 202", title: "Metabolic Biochemistry" },
    document: { id: "d", filename: "pasted.txt", mime_type: "text/plain", academic_year: "2025-26" },
    edition: { outline: { start_year: 2025, academic_year: "2025-26", publication_date: null, source: "entered", quote: "2025-26" }, version: { academic_year: "2025-26", start_year: 2025, label: "2025–26 (synthetic draft)" }, fit: "match" },
    sections: [
      { key: "outcomes", label: "Learning outcomes", status: "present", complete: true, reason: "The outline lists the course's outcomes.", existing: 2, extracted: 2, existing_edition: "2024-25", compare: 1 },
      { key: "assessments", label: "Assessments", status: "absent", complete: null, reason: "The outline lists no assessments; the program's 1 stay as they are.", existing: 1, extracted: 0, existing_edition: "2024-25", compare: 1 },
      { key: "topics", label: "Topics", status: "absent", complete: null, reason: "The outline lists no topics; the program's 1 stay as they are.", existing: 1, extracted: 0, existing_edition: null, compare: null },
    ],
    items: [
      { ...base, id: "o:n1", section: "outcomes", action: "replace", title: "Update SYN202-CO1", reason: "Newer outline (2025-26) replaces the older wording.", target: end("SYN202-CO1", "Describe regulation of core metabolic pathways.", "course_outcome", "e1"), proposed: { type: "course_outcome", title: "Describe the regulation of metabolic pathways." }, diff: [{ field: "statement", before: "Describe regulation of core metabolic pathways.", after: "Describe the regulation of metabolic pathways." }], evidence: ev("1. Describe the regulation of metabolic pathways."), carries_links: 3 },
      { ...base, id: "o:n2", section: "outcomes", action: "add", title: "New outcome: Design experiments on metabolic flux.", reason: "New in this outline.", proposed: { type: "course_outcome", title: "Design experiments on metabolic flux." }, key_preview: "SYN202-CO3", evidence: ev("2. Design experiments on metabolic flux.") },
      { ...base, id: "o:e2", section: "outcomes", action: "retire", title: "Retire SYN202-CO2: Interpret enzyme kinetics data.", reason: "Not in the 2025-26 outline's list.", target: end("SYN202-CO2", "Interpret enzyme kinetics data.", "course_outcome", "e2"), drops_links: 2, excludes: ["l3"] },
      { ...base, id: "l1", section: "links", action: "link", title: "SYN202-A1 assesses new outcome", reason: "Stated in the outline.", link: { source: end("SYN202-A1", "Enzyme kinetics problem set", "assessment", "e3"), target: end(null, "Design experiments on metabolic flux.", "course_outcome", "n2"), rel_type: "assesses", basis: "stated", level: null, strength: null, model_strength: 0.9, rationale: "The assessment table lists outcome 2." }, depends_on: ["o:n2"], evidence: ev("Problem set (30%) assesses outcome 2") },
      { ...base, id: "l2", section: "links", action: "link", title: "biochemistry supports new outcome", reason: "AI interpretation of the outline.", link: { source: end("biochemistry", "Metabolic biochemistry", "topic", "e4"), target: end(null, "Design experiments on metabolic flux.", "course_outcome", "n2"), rel_type: "supports", basis: "inferred", level: null, strength: 0.7, model_strength: 0.7, rationale: "Teaching metabolism underpins flux experiments." }, depends_on: ["o:n2"], evidence: [{ span_id: null, page: null, quote: "metabolic flux", source: "element", field: null }] },
      { ...base, id: "l3", section: "links", action: "link", title: "SYN202-A1 assesses SYN202-CO2", default_selected: false, reason: "AI interpretation of the outline.", link: { source: end("SYN202-A1", "Enzyme kinetics problem set", "assessment", "e3"), target: end("SYN202-CO2", "Interpret enzyme kinetics data.", "course_outcome", "e2"), rel_type: "assesses", basis: "inferred", level: null, strength: 0.65, model_strength: 0.65, rationale: "Kinetics problems." }, excludes: ["o:e2"] },
    ],
    missing_before: [{ id: "gap:clo_no_plo:x", kind: "clo_no_plo", severity: "medium", message: "SYN202-CO2 is not linked to any program outcome.", entity_ids: ["x"], fillable: true }],
    program_missing: [],
    unfillable: [{ id: "gap:course_no_prerequisite:c", kind: "course_no_prerequisite", severity: "low", message: "No prerequisite statement.", entity_ids: ["c"], fillable: false, why: "Prerequisites come from the calendar entry." }],
    warnings: [],
    dropped: [{ what: "e99 assesses n1", reason: "unknown or skipped element ref" }],
    notes: ["Topics are listed by week only."],
    agent: { model_run_id: "r", model: "m" },
    summary: { selected: 5, changes: { add: 1, replace: 1, retire: 1, link: 2 }, missing_before: 1, missing_after: 0, closes: ["gap:clo_no_plo:x"], introduces: [], program_before: 0, program_after: 0, program_closes: [] },
  };
}

function imp(status: string, etag: string, extra: Record<string, unknown> = {}) {
  return {
    id: IMP, curriculum_version_id: "v", course_entity_id: "c", course_key: "SYN 202", document_version_id: "d", document_title: "SYN 202 course outline",
    job_id: "j9", status, options: {}, outline_year: "2025-26", warnings: [], error: null, counts: null,
    created_at: "2026-09-27T10:00:00Z", updated_at: "2026-09-27T10:00:00Z", applied_at: null, reverted_at: null,
    plan: status === "ready" ? plan() : null, result: null, etag, ...extra,
  };
}

const sse = (msg: string) => (r: Route) =>
  r.fulfill({ contentType: "text/event-stream", body: `id: 1\nevent: job\ndata: {"id":1,"stage":"reconcile","status":"running","message":"${msg}","job":{"status":"succeeded","progress":1,"stage":"ready"}}\n\nevent: end\ndata: {}\n\n` });

async function openDraftCourse(page: Page) {
  await page.goto("/");
  await page.evaluate(() => localStorage.clear());
  await page.goto("/");
  const program = page.getByRole("combobox", { name: "Program" });
  await program.selectOption((await program.locator("option", { hasText: "Synthetic Biomedical Sciences" }).getAttribute("value"))!);
  const version = page.getByRole("combobox", { name: "Curriculum version and cohort" });
  await version.selectOption((await version.locator("option", { hasText: "2025–26 (synthetic draft)" }).getAttribute("value"))!);
  await expect(page.getByRole("group", { name: "Alignment scorecard" })).toBeVisible();
  await page.getByRole("searchbox", { name: "Search" }).fill("SYN 202");
  await page.getByRole("option", { name: /SYN 202 — Metabolic Biochemistry/ }).first().click();
}

test("import a course outline: plan, dependencies, evidence, stale plan, apply (network-mocked)", async ({ page }) => {
  const previews: string[][] = [];
  let applies = 0;
  let current = imp("ready", "etag-1");
  await page.route("**/api/v1/ai/status", (r) => r.fulfill({ json: { implemented: true, providers: {}, allowlist: {}, retrieval_mode: "fts", message: "", limits: { monthly_budget_usd: 1, max_cost_per_job_usd: 2 }, spend: { month_usd: 0 },
    routes: ["extract", "synthesize", "pedagogy"].map((name) => ({ name, provider: "openai", model: "m", purpose: "", fallback: null, available: true, active: ["openai", `model-${name}`], unavailable_reasons: [] })) } }));
  await page.route("**/api/v1/versions/*/courses/*/outline-imports", (r) =>
    r.request().method() === "POST"
      ? r.fulfill({ status: 202, json: { outline_import: { ...imp("queued", ""), plan: undefined }, job_id: "j9", duplicate_document: false, reused: false } })
      : r.fulfill({ json: [] }));
  await page.route("**/api/v1/jobs/j9/stream", sse("Asking the agent what the program needs from this outline"));
  await page.route("**/api/v1/jobs/j10/stream", sse("Plan ready"));
  await page.route(`**/api/v1/outline-imports/${IMP}`, (r) => r.fulfill({ json: current }));
  await page.route(`**/api/v1/outline-imports/${IMP}/preview`, (r) => {
    previews.push((r.request().postDataJSON() as { selected_item_ids: string[] }).selected_item_ids.sort());
    return r.fulfill({ json: { ...plan().summary, selected: 1 } });
  });
  await page.route(`**/api/v1/outline-imports/${IMP}/rerun`, (r) => {
    current = imp("ready", "etag-2");
    return r.fulfill({ status: 202, json: { job_id: "j10", import_id: IMP } });
  });
  await page.route(`**/api/v1/outline-imports/${IMP}/apply`, (r) => {
    applies += 1;
    if (applies === 1)
      return r.fulfill({ status: 409, json: { error: { code: "plan_stale", message: "The course changed after this plan was made.", details: null } } });
    expect(r.request().headers()["if-match"]).toBe('"etag-2"');
    current = imp("applied", "etag-2", { counts: { replace: 1, add: 1, link: 2 }, result: { applied: [], edits: {}, counts: { replace: 1, add: 1, link: 2 }, new_entities: {}, missing_after: [], program_after: [], summary: { closes: ["gap:clo_no_plo:x"], missing_before: 1, missing_after: 0 } } });
    return r.fulfill({ json: current });
  });
  await page.route(`**/api/v1/evidence/${SPAN}`, (r) => r.fulfill({ json: { id: SPAN, document_version_id: "d", page_number: 1, char_start: 0, char_end: 12, quote: "1. Describe", bbox: null, document_title: "SYN 202 course outline", document_type: "course_outline", mime_type: "text/plain", publication_date: null, academic_year: "2025-26", is_synthetic: true } }));
  await page.route("**/api/v1/document-versions/d/pages*", (r) => r.fulfill({ json: [{ page_number: 1, text: "1. Describe the regulation of metabolic pathways.\n2. Design experiments on metabolic flux." }] }));

  await openDraftCourse(page);
  const card = page.getByRole("region", { name: "Missing pieces for SYN 202" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "Import course outline…" }).click();

  const dialog = page.getByRole("dialog", { name: /Import course outline — SYN 202/ });
  await expect(dialog).toBeVisible();
  await dialog.getByLabel("Paste text").check();
  await dialog.getByRole("textbox", { name: "Outline text" }).fill("SYN 202 outline 2025-26\n1. Describe the regulation of metabolic pathways.");
  await expect(dialog.getByText(/model-extract/)).toBeVisible(); // who receives the text is disclosed
  await dialog.getByRole("button", { name: "Read the outline and plan" }).click();
  await expect(dialog.getByText("Update SYN202-CO1")).toBeVisible();
  await expect(dialog.getByText(/Outline 2025-26/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: /Apply 5 changes · closes 1 of 1 missing piece/ })).toBeVisible();

  // deselecting the new outcome deselects the links that need it
  await dialog.getByRole("checkbox", { name: /New outcome: Design experiments/ }).uncheck();
  await expect(dialog.getByText(/Also deselected \(depends on it\)/)).toBeVisible();
  await expect(dialog.getByRole("checkbox", { name: /SYN202-A1 assesses Design experiments/ })).not.toBeChecked();
  await expect.poll(() => previews.at(-1)).toEqual(["o:e2", "o:n1"]);
  // a link that needs the retired outcome deselects the retirement
  await dialog.getByRole("checkbox", { name: /SYN202-A1 assesses SYN202-CO2/ }).check();
  await expect(dialog.getByRole("checkbox", { name: /Retire SYN202-CO2/ })).not.toBeChecked();

  // evidence opens on top of the dialog (text source) and closing it returns to the plan
  await dialog.locator(".evidence-chip").first().click();
  const viewer = page.getByRole("dialog", { name: /SYN 202 course outline — page 1/ });
  await expect(viewer.getByText(/Extracted text of page 1/)).toBeVisible();
  await viewer.getByRole("button", { name: "Close source viewer" }).click();
  await expect(viewer).toBeHidden();
  await expect(dialog.getByText("Update SYN202-CO1")).toBeVisible();

  const axe = await new AxeBuilder({ page }).include(".outline-dialog").withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze();
  expect(axe.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(" ")).slice(0, 3).join(" | ")}`)).toEqual([]);

  // the course changed meanwhile: apply is refused and the plan is re-run
  await dialog.getByRole("button", { name: /^Apply/ }).click();
  await expect(dialog.getByText("The course changed after this plan was made.")).toBeVisible();
  await dialog.getByRole("button", { name: "Re-run the plan" }).click();
  await expect(dialog.getByRole("button", { name: /Apply 5 changes/ })).toBeVisible(); // fresh plan, defaults restored
  await dialog.getByRole("button", { name: /Apply 5 changes/ }).click();
  await expect(dialog.getByText(/Applied to the draft\. 1 update · 1 addition · 2 links/)).toBeVisible();
  await expect(dialog.getByRole("button", { name: "Revert this import" })).toBeVisible();
});
