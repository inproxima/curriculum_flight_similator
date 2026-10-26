import { describe, expect, it } from "vitest";
import type { Job } from "../api/hooks";
import { arrange, duration, isActive, jobScope, jobTitle, resultLines, titlePrefix, visibleJobs } from "./jobCenterModel";

const T0 = Date.parse("2026-09-27T21:24:42Z");
const job = (id: string, extra: Partial<Job> = {}): Job => ({
  id, kind: "ai_map_alignment", status: "running", current_stage: "mapping", progress: 0.54, cancel_requested: false, attempts: 1,
  payload: { curriculum_version_id: "v1", course_ids: null }, result: null, error: null,
  created_at: new Date(T0).toISOString(), updated_at: new Date(T0 + 11 * 60000).toISOString(), last_message: "Call 18/33: BCEM 393 → later courses",
  ...extra,
});
const done = (id: string, minutesAgo: number, extra: Partial<Job> = {}) =>
  job(id, { status: "succeeded", progress: 1, updated_at: new Date(T0 + 60 * 60000 - minutesAgo * 60000).toISOString(), ...extra });
const NOW = T0 + 60 * 60000;

describe("job center", () => {
  it("shows running jobs, and finished ones from the last day until dismissed; only AI jobs", () => {
    const jobs = [job("run"), done("new", 5), done("seen", 5), done("old", 60 * 25), job("ingest", { kind: "ingest_document" })];
    expect(visibleJobs(jobs, ["seen"], NOW).map((j) => j.id)).toEqual(["run", "new"]);
    expect(isActive(job("q", { status: "queued" }))).toBe(true);
  });

  it("puts running jobs first and summarizes finished ones beyond two", () => {
    const { active, done: shown, more } = arrange([done("a", 30), job("run"), done("b", 1), done("c", 10), done("d", 20)]);
    expect(active.map((j) => j.id)).toEqual(["run"]);
    expect(shown.map((j) => j.id)).toEqual(["b", "c"]); // newest first
    expect(more.map((j) => j.id)).toEqual(["d", "a"]);
  });

  it("names the job and its scope, with fallbacks for jobs without a label", () => {
    expect(jobTitle(job("r"))).toBe("Mapping connections");
    expect(jobTitle(done("d", 1))).toBe("Connection map ready");
    expect(jobTitle(done("f", 1, { status: "failed" }))).toBe("Connection mapping stopped");
    expect(jobTitle(job("p", { kind: "ai_propose_alignment" }))).toBe("Proposing missing links");
    expect(jobScope(job("r"), () => "2026–27 (entering Fall 2026)")).toBe("2026–27 (entering Fall 2026) · whole program");
    expect(jobScope(job("r", { payload: { course_ids: ["x"], label: "CHEM 351", version_label: "BHSc 2026–27" } }))).toBe("BHSc 2026–27 · CHEM 351");
    expect(jobScope(job("r", { payload: { course_ids: ["x", "y"] } }))).toBe("2 courses");
  });

  it("says what a finished mapping produced, and what survives a failure", () => {
    const ok = done("d", 1, {
      result: { proposed: 909, by_level: { direct: 146, strong: 571, partial: 192 }, verdicts: { linked: 596, none: 238 }, cost_usd: 5.75 },
      created_at: new Date(T0).toISOString(), updated_at: new Date(T0 + 22 * 60000 + 31000).toISOString(),
    });
    expect(resultLines(ok)).toEqual(["909 links proposed: 146 direct · 571 strong · 192 partial", "238 “no connection found” · 22 min · $5.75"]);
    const failed = done("f", 1, { status: "failed", error: "BudgetExceeded: job cost cap reached" });
    expect(resultLines(failed)).toEqual(["BudgetExceeded: job cost cap reached", "Links proposed before it stopped are kept (dashed, AI?)."]);
    expect(resultLines(done("p", 1, { kind: "ai_propose_alignment", result: { proposed: 1 } }))[0]).toMatch(/^1 missing link proposed/);
  });

  it("formats durations and the tab title", () => {
    expect(duration("2026-09-27T21:00:00Z", "2026-09-27T21:00:40Z")).toBe("40 s");
    expect(duration("2026-09-27T21:00:00Z", "2026-09-27T22:05:00Z")).toBe("1 h 5 min");
    expect(titlePrefix([job("a", { progress: 0.54 }), job("b", { progress: 0.9 })])).toBe("⏳ 54% · ");
    expect(titlePrefix([done("d", 1)])).toBe("✓ ");
    expect(titlePrefix([done("d", 1), done("f", 1, { status: "failed" })])).toBe("⚠ ");
    expect(titlePrefix([])).toBe("");
  });
});
