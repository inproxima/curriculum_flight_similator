import { describe, expect, it } from "vitest";
import { applyRipple, captionFor, clampStep, visibleAt, type Ripple } from "./ripple";

const ripple: Ripple = {
  max_hop: 2,
  steps: [
    { hop: 0, node_ids: ["a"], edge_keys: ["e0"] },
    { hop: 1, node_ids: ["o"], edge_keys: ["e1"] },
    { hop: 2, node_ids: ["p", "q"], edge_keys: [] },
  ],
  impacts: [
    { entity_id: "a", key: "A1", title: "", type: "assessment", course_key: null, status: "removed", hop: 0, path: ["a"], edge_keys: [], strength_before: null, strength_after: null, severity: "medium", rank: 2, explanation: "" },
    { entity_id: "o", key: "CO1", title: "", type: "course_outcome", course_key: null, status: "weakened", hop: 1, path: ["a", "o"], edge_keys: ["e1"], strength_before: 1, strength_after: 0.8, severity: "medium", rank: 3, explanation: "" },
    { entity_id: "p", key: "PLO4", title: "", type: "program_outcome", course_key: null, status: "broken", hop: 2, path: ["a", "o", "p"], edge_keys: [], strength_before: 0.8, strength_after: 0, severity: "high", rank: 1, explanation: "" },
    { entity_id: "q", key: "SYN 330", title: "", type: "course", course_key: null, status: "affected", hop: 2, path: ["a", "o", "q"], edge_keys: [], strength_before: null, strength_after: null, severity: "info", rank: 4, explanation: "" },
  ],
  edge_status: { e0: "removed", e1: "weakened" },
};

describe("ripple", () => {
  it("reveals monotonically and pulses only the current hop", () => {
    const f0 = visibleAt(ripple, 0), f1 = visibleAt(ripple, 1), f2 = visibleAt(ripple, 2);
    expect([...f0.nodes.keys()]).toEqual(["a"]);
    expect([...f1.nodes.keys()]).toEqual(["a", "o"]);
    expect(f2.nodes.size).toBe(4);
    for (const k of f1.nodes.keys()) expect(f2.nodes.has(k)).toBe(true);
    expect([...f2.pulseNodes]).toEqual(["p", "q"]);
    expect(f1.pulseEdges.has("e1") && !f1.pulseEdges.has("e0")).toBe(true);
    expect(f2.nodes.get("p")).toBe("broken");
  });

  it("clamps steps and captions by rank", () => {
    expect(clampStep(ripple, 9)).toBe(2);
    expect(clampStep(ripple, -1)).toBe(0);
    expect(captionFor(ripple, 2)).toBe("Step 2: PLO4 breaks; SYN 330 is affected.");
    expect(captionFor(ripple, 0)).toContain("the change");
  });

  it("projects statuses onto a view", () => {
    const view = {
      nodes: [{ id: "a", status: "removed" }, { id: "o", status: "unchanged" }, { id: "z", status: "modified" }],
      edges: [{ id: "e1", key: "e1", status: "unchanged" }],
    } as never;
    const { view: v } = applyRipple(view, ripple, 1);
    expect(v.nodes.map((n) => n.status)).toEqual(["removed", "weakened", "unchanged"]);
    expect(v.edges[0].status).toBe("weakened");
  });
});
