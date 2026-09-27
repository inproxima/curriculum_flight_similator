import { describe, expect, it } from "vitest";
import type { GraphNode, GraphView } from "../api/types";
import { edgeStyle, laneLabel, partitionOf } from "../lib/graphStyle";
import { applyClientFilters } from "./CurriculumMap";

const node = (id: string, year: number | null, term: string, classification = "required", extra: Partial<GraphNode> = {}): GraphNode => ({
  id, type: "course", key: id, title: id, description: null, year, term, classification, pathway: null, placements: [],
  details: {}, origin: "synthetic_fixture", is_synthetic: true, evidence: { fields: [], with_evidence: [], ratio: null },
  status: "unchanged", owner_course: null, expanded: false, ...extra,
});
const edge = (id: string, source: string, target: string, over: Partial<GraphView["edges"][number]> = {}) => ({
  id, key: id, revision_id: null, source, target, type: "formal_prerequisite", basis: "explicit_statement" as const, origin: "x",
  review_state: "accepted" as const, level: null, rule_id: null, has_evidence: true, contradicted: false, derived: false, via: [],
  status: "unchanged" as const, rationale: null, is_synthetic: true, ...over,
});

const view = (): GraphView => ({
  curriculum_version: { id: "v", label: "v", status: "published", program_id: "p", program_name: "P", academic_year: null, cohort: null, is_synthetic: true },
  scenario: null, scenario_revision: null, layers: [], available_layers: [], truncated: null, stats: { nodes: 3, edges: 2, courses: 3 },
  nodes: [node("A", 1, "fall"), node("B", 2, "winter", "elective"), node("C", null, "unknown")],
  edges: [edge("e1", "A", "B"), edge("e2", "A", "C", { type: "inferred_preparation", basis: "interpretation", review_state: "proposed" })],
});

describe("client filters", () => {
  it("filters by year and drops dangling edges", () => {
    const r = applyClientFilters(view(), { years: [1], classifications: [], reviewStates: ["accepted", "proposed"], basis: [], pathway: null });
    expect(r.nodes.map((n) => n.id)).toEqual(["A"]);
    expect(r.edges).toHaveLength(0);
  });
  it("filters edges by review state and basis", () => {
    const r = applyClientFilters(view(), { years: [], classifications: [], reviewStates: ["accepted"], basis: [], pathway: null });
    expect(r.edges.map((e) => e.id)).toEqual(["e1"]);
    const r2 = applyClientFilters(view(), { years: [], classifications: [], reviewStates: ["accepted", "proposed"], basis: ["interpretation"], pathway: null });
    expect(r2.edges.map((e) => e.id)).toEqual(["e2"]);
  });
});

describe("lanes and styles", () => {
  it("orders lanes by year then term and isolates unknown timing", () => {
    const by = new Map<string, GraphNode>();
    expect(partitionOf(node("A", 1, "fall"), by)).toBeLessThan(partitionOf(node("B", 1, "winter"), by));
    expect(partitionOf(node("B", 1, "winter"), by)).toBeLessThan(partitionOf(node("C", 2, "fall"), by));
    expect(laneLabel(partitionOf(node("D", null, "unknown"), by))).toBe("Timing unknown");
  });
  it("never conveys inferred or scenario status by color alone", () => {
    const inferred = edgeStyle(edge("x", "A", "B", { type: "inferred_preparation", basis: "interpretation", review_state: "proposed" }), false, false);
    expect(inferred.style?.strokeDasharray).toBeTruthy();
    expect(inferred.label).toContain("proposed");
    const removed = edgeStyle(edge("y", "A", "B", { status: "removed" }), false, false);
    expect(String(removed.label)).toContain("removed");
  });
});
