import { describe, expect, it } from "vitest";
import type { WebEdge, WebNode, WebView } from "../api/webHooks";
import { DEFAULT_WEB_FILTERS, applyWebFilters, gapEdges, linkKind } from "./webFilters";
import { COL_W, layoutWeb } from "./webLayout";
import { PROVENANCE_DASH, STRENGTH_WIDTH, edgeLook } from "./webStyle";

const node = (id: string, type: WebNode["type"], extra: Partial<WebNode> = {}): WebNode => ({
  id, type, key: id, title: id, description: null, course_id: null, course_ids: [], year: null, term: null, time_index: null,
  classification: null, details: {}, origin: "x", is_synthetic: true, evidence: {}, status: "unchanged", metrics: {},
  metrics_before: null, aggregate: null, ...extra,
});
const edge = (id: string, source: string, target: string, type: string, extra: Partial<WebEdge> = {}): WebEdge => ({
  id, key: id, revision_id: null, source, target, type, category: "vertical", level: null, origin: "x", basis: "explicit_statement",
  review_state: "accepted", provenance: "documented", strength: { effective: 0.8, label: "strong" }, strength_before: null,
  rationale: null, has_evidence: false, contradicted: false, status: "unchanged", bundle: null, ...extra,
});

const columns = [
  { course_id: "c2", key: "C 200", title: "", year: 2, term: "fall", time_index: 20, band: "Year 2 · Fall" },
  { course_id: "c1", key: "C 100", title: "", year: 1, term: "fall", time_index: 10, band: "Year 1 · Fall" },
];
const cols = [...columns].sort((a, b) => a.time_index - b.time_index);

function fixture(): Pick<WebView, "nodes" | "edges"> {
  const nodes = [
    node("c1", "course", { year: 1, term: "fall" }),
    node("c2", "course", { year: 2, term: "fall" }),
    node("o1", "course_outcome", { course_id: "c1", course_ids: ["c1"] }),
    node("o2", "course_outcome", { course_id: "c2", course_ids: ["c2"] }),
    node("a1", "assessment", { course_id: "c1", course_ids: ["c1"] }),
    node("t1", "topic", { course_id: "c1", course_ids: ["c1"] }),
    node("ts", "topic", { course_id: "c1", course_ids: ["c1", "c2"] }),
    node("p1", "program_outcome"),
    node("p2", "program_outcome"),
  ];
  const edges = [
    edge("k1", "o1", "p1", "contributes_to"),
    edge("k2", "o2", "p1", "contributes_to", { strength: { effective: 0.3, label: "weak" } }),
    edge("k3", "a1", "o1", "assesses"),
    edge("k4", "o1", "o2", "prepares_for", { category: "horizontal", provenance: "proposed", review_state: "proposed" }),
    edge("k5", "c1", "o1", "has_outcome", { category: "containment", strength: null }),
    edge("k6", "a1", "o2", "prepares_for", { category: "horizontal" }),
  ];
  return { nodes, edges };
}

describe("webLayout", () => {
  it("is deterministic and orders course columns by time", () => {
    const f = fixture();
    const a = layoutWeb(f.nodes, f.edges, cols);
    const b = layoutWeb([...f.nodes].reverse(), [...f.edges].reverse(), cols);
    expect(a.positions).toEqual(b.positions);
    expect(a.columnX.c1).toBeLessThan(a.columnX.c2);
    expect(a.bands.map((x) => x.label)).toEqual(["Year 1 · Fall", "Year 2 · Fall"]);
  });

  it("stacks members under their course and never overlaps within a column", () => {
    const { positions } = layoutWeb(fixture().nodes, fixture().edges, cols);
    expect(positions.o1.x).toBe(positions.c1.x);
    expect(positions.a1.x).toBe(positions.c1.x);
    const ys = ["c1", "o1", "a1", "t1"].map((id) => positions[id].y);
    expect([...ys].sort((x, y) => x - y)).toEqual(ys);
    expect(new Set(ys).size).toBe(ys.length);
  });

  it("places program outcomes above and shared topics below at the barycentre", () => {
    const l = layoutWeb(fixture().nodes, fixture().edges, cols);
    expect(l.positions.p1.y).toBe(0);
    expect(l.positions.ts.y).toBeGreaterThan(l.positions.a1.y);
    const centre = (l.columnX.c1 + l.columnX.c2 + COL_W) / 2;
    expect(Math.abs(l.positions.ts.x + 85 - centre)).toBeLessThan(1);
    expect(l.positions.p2.x).toBeGreaterThan(l.positions.p1.x); // uncontributed outcome goes after the others
  });
});

describe("webFilters", () => {
  it("classifies link kinds", () => {
    const f = fixture();
    const byId = new Map(f.nodes.map((n) => [n.id, n]));
    expect(f.edges.map((e) => linkKind(e, byId))).toEqual(["clo_plo", "clo_plo", "assessment_clo", "clo_clo", "containment", "assessment_next"]);
  });

  it("applies element, link, strength, and provenance layers", () => {
    const f = fixture();
    const all = applyWebFilters(f, DEFAULT_WEB_FILTERS);
    expect(all.edges.has("k5")).toBe(false); // containment hidden by default
    expect(all.edges.has("k4")).toBe(true);
    expect(applyWebFilters(f, { ...DEFAULT_WEB_FILTERS, strengths: ["strong", "moderate"] }).edges.has("k2")).toBe(false);
    expect(applyWebFilters(f, { ...DEFAULT_WEB_FILTERS, provenance: ["documented"] }).edges.has("k4")).toBe(false);
    const noAssess = applyWebFilters(f, { ...DEFAULT_WEB_FILTERS, nodeTypes: DEFAULT_WEB_FILTERS.nodeTypes.filter((t) => t !== "assessment") });
    expect(noAssess.nodes.has("a1")).toBe(false);
    expect(noAssess.edges.has("k3")).toBe(false);
    const y2 = applyWebFilters(f, { ...DEFAULT_WEB_FILTERS, years: [2] });
    expect(y2.nodes.has("o1")).toBe(false);
    expect(y2.nodes.has("o2") && y2.nodes.has("p1")).toBe(true);
  });

  it("shows only what changed when asked", () => {
    const f = fixture();
    f.edges[2] = { ...f.edges[2], status: "removed" };
    const v = applyWebFilters(f, { ...DEFAULT_WEB_FILTERS, changedOnly: true });
    expect([...v.edges]).toEqual(["k3"]);
    expect(v.nodes.has("a1") && v.nodes.has("o1") && !v.nodes.has("p2")).toBe(true);
  });

  it("draws gap edges only between visible nodes", () => {
    const g = { id: "g", kind: "course_pair_no_link", severity: "low", message: "", entity_ids: ["c1", "c2"], source: "c1", target: "c2" };
    expect(gapEdges([g], new Set(["c1", "c2"]))).toHaveLength(1);
    expect(gapEdges([g], new Set(["c1"]))).toHaveLength(0);
  });
});

describe("webStyle", () => {
  it("encodes strength by width and provenance by dash, with a text chip", () => {
    const strong = edgeLook(edge("a", "x", "y", "contributes_to"));
    const weak = edgeLook(edge("b", "x", "y", "contributes_to", { strength: { effective: 0.2, label: "weak" } }));
    const proposed = edgeLook(edge("c", "x", "y", "prepares_for", { provenance: "proposed" }));
    expect(strong.width).toBe(STRENGTH_WIDTH.strong);
    expect(weak.width).toBeLessThan(strong.width);
    expect(strong.dash).toBe(PROVENANCE_DASH.documented);
    expect(proposed.dash).toBe(PROVENANCE_DASH.proposed);
    expect(strong.chip).toBe("S");
    expect(proposed.chip).toContain("AI?");
  });

  it("never conveys a diff status by colour alone", () => {
    for (const status of ["added", "removed", "strengthened", "weakened", "modified"] as const) {
      const look = edgeLook(edge("d", "x", "y", "assesses", { status }), { diff: true });
      expect(look.chip.length).toBeGreaterThan(1);
      expect(look.title.toLowerCase()).toContain(status);
    }
    expect(edgeLook(edge("e", "x", "y", "assesses"), { diff: true }).opacity).toBeLessThan(1);
  });
});
