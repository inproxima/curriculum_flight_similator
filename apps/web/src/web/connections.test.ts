import { describe, expect, it } from "vitest";
import type { WebEdge, WebNode } from "../api/webHooks";
import { acrossCell, bestLevel, connIndex, linksBetween, mapState, membersOf, proposedOnly, summarize } from "./connections";
import { DEFAULT_WEB_FILTERS, applyWebFilters } from "./webFilters";
import { CONNECTION_COLOR, CONNECTION_GLYPH, TYPE_COLOR, edgeLook } from "./webStyle";

const node = (id: string, type: WebNode["type"], extra: Partial<WebNode> = {}): WebNode => ({
  id, type, key: id, title: id, description: null, course_id: null, course_ids: [], year: null, term: null, time_index: null,
  classification: null, details: {}, origin: "x", is_synthetic: true, evidence: {}, status: "unchanged", metrics: {},
  metrics_before: null, aggregate: null, ...extra,
});
const edge = (id: string, source: string, target: string, type: string, extra: Partial<WebEdge> = {}): WebEdge => ({
  id, key: id, revision_id: null, source, target, type, category: "vertical", level: null, origin: "x", basis: "explicit_statement",
  review_state: "accepted", provenance: "documented", strength: { effective: 0.8, label: "strong" }, strength_before: null,
  rationale: null, has_evidence: false, contradicted: false, status: "unchanged", bundle: null, connection: "direct", ...extra,
});
const m = (best: "direct" | "strong" | "partial" | null, verdict: "linked" | "none" | null = null, reason: string | null = null) => ({
  best, count: best ? 1 : 0, verdict, reason,
});

function fixture() {
  const nodes = [
    node("c2", "course", { key: "C 200", year: 2, term: "fall" }),
    node("c1", "course", { key: "C 100", year: 1, term: "fall" }),
    node("c3", "course", { key: "C 300", year: 3, term: "winter" }),
    node("p1", "program_outcome"),
    node("o1", "course_outcome", { course_id: "c1", course_ids: ["c1"], mapping: { contributes_to: m("direct"), prepares_for: m("partial") } }),
    node("o2", "course_outcome", { course_id: "c2", course_ids: ["c2"], mapping: { contributes_to: m(null, "none", "Kinetics only"), prepared_by: m("partial") } }),
    node("o3", "course_outcome", { course_id: "c3", course_ids: ["c3"], mapping: { contributes_to: m(null) } }),
    node("t2", "topic", { course_id: "c2", course_ids: ["c2"], mapping: { supports: m("strong"), prepared_by: m(null, "none", "new"), prepares_for: m("strong") } }),
  ];
  const edges = [
    edge("k1", "o1", "p1", "contributes_to"),
    edge("k2", "o1", "o2", "prepares_for", { category: "horizontal", provenance: "proposed", review_state: "proposed", connection: "partial" }),
    edge("k3", "t2", "o2", "supports", { provenance: "proposed", review_state: "proposed", connection: "strong" }),
    edge("k4", "t2", "o3", "prepares_for", { category: "horizontal", connection: "strong", provenance: "accepted_inference" }),
    edge("k5", "c1", "o1", "has_outcome", { category: "containment", strength: null, connection: null }),
  ];
  return { nodes, edges };
}

describe("connections", () => {
  it("indexes alignment links only and orders courses by year and term", () => {
    const ix = connIndex(fixture());
    expect(ix.courses.map((c) => c.id)).toEqual(["c1", "c2", "c3"]);
    expect(ix.out.get("c1")).toBeUndefined(); // containment carries no connection level
    expect(linksBetween(ix, "o1", "p1", "contributes_to").map((e) => e.id)).toEqual(["k1"]);
  });

  it("takes the most direct level, and flags cells that are only AI proposals", () => {
    const f = fixture();
    expect(bestLevel(f.edges.slice(0, 4))).toBe("direct");
    expect(bestLevel([f.edges[1], f.edges[2]])).toBe("strong");
    expect(bestLevel([])).toBeNull();
    expect(proposedOnly([f.edges[1], f.edges[2]])).toBe(true);
    expect(proposedOnly([f.edges[1], f.edges[3]])).toBe(false);
    expect(proposedOnly([])).toBe(false);
  });

  it("tells 'none found' apart from 'not mapped', and leaves out directions that do not apply", () => {
    const [, , , , o1, o2, o3, t2] = fixture().nodes;
    expect(mapState(o1, "contributes_to")).toBe("direct");
    expect(mapState(o2, "contributes_to")).toBe("none");
    expect(mapState(o3, "contributes_to")).toBe("unmapped");
    expect(mapState(o1, "prepared_by")).toBeNull(); // first-year: nothing earlier
    expect(mapState(t2, "prepared_by")).toBe("none");
  });

  it("summarizes each direction", () => {
    const s = summarize(fixture().nodes, "course_outcome", "contributes_to");
    expect(s).toEqual({ direct: 1, strong: 0, partial: 0, none: 1, unmapped: 1, total: 3 });
    expect(summarize(fixture().nodes, "topic", "prepares_for").strong).toBe(1);
  });

  it("finds what an element builds on and what builds on it, per course", () => {
    const f = fixture();
    const ix = connIndex(f);
    expect(membersOf(f, "c2", "course_outcome").map((n) => n.id)).toEqual(["o2"]);
    expect(acrossCell(ix, "o2", "c1").before.map((e) => e.id)).toEqual(["k2"]); // o1 (year 1) prepares for o2
    expect(acrossCell(ix, "o2", "c1").after).toEqual([]);
    expect(acrossCell(ix, "t2", "c3").after.map((e) => e.id)).toEqual(["k4"]);
    expect(acrossCell(ix, "t2", "c1")).toEqual({ after: [], before: [] });
  });
});

describe("connection layers", () => {
  it("colours links by level (with dots and AI?) or by type", () => {
    const [k1, k2] = fixture().edges;
    const lv = edgeLook(k2);
    expect(lv.stroke).toBe(CONNECTION_COLOR.partial);
    expect(lv.chip).toContain(CONNECTION_GLYPH.partial);
    expect(lv.chip).toContain("AI?");
    expect(lv.title).toContain("Partial / surmised connection");
    expect(edgeLook(k1, { colorBy: "type" }).stroke).toBe(TYPE_COLOR.contributes_to);
    expect(edgeLook(k1, { colorBy: "type" }).chip).not.toContain(CONNECTION_GLYPH.direct);
  });

  it("filters links by connection level", () => {
    const f = fixture();
    const all = applyWebFilters(f, DEFAULT_WEB_FILTERS);
    expect(all.edges.has("k2")).toBe(true);
    const noPartial = applyWebFilters(f, { ...DEFAULT_WEB_FILTERS, connections: ["direct", "strong"] });
    expect(noPartial.edges.has("k2")).toBe(false);
    expect(noPartial.edges.has("k1")).toBe(true);
    expect(noPartial.edges.has("k4")).toBe(true);
  });
});
