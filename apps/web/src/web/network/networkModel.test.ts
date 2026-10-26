import { describe, expect, it } from "vitest";
import type { WebEdge, WebNode } from "../../api/webHooks";
import { DEFAULT_WEB_FILTERS } from "../webFilters";
import {
  BAND,
  LENSES,
  activeLens,
  focusOf,
  insideShape,
  linkAlpha,
  linkShown,
  networkVisibility,
  nodeShown,
  pickAt,
  radius,
  shapeExtent,
  shortLabel,
  showLabel,
  yearX,
  type PickLink,
  type PickNode,
} from "./networkModel";

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

function fixture() {
  const nodes = [
    node("p1", "program_outcome", { key: "PLO1", title: "PLO1: Foundations" }),
    node("c1", "course", { key: "CHEM 351", year: 3, term: "fall" }),
    node("c2", "course", { key: "BIOL 211", year: 2, term: "winter" }),
    node("o1", "course_outcome", { key: "CHEM351-CO1", course_id: "c1", course_ids: ["c1"] }),
    node("o2", "course_outcome", { key: "BIOL211-CO2", course_id: "c2", course_ids: ["c2"] }),
    node("a1", "assessment", { key: "CHEM351-A-final", course_id: "c1", course_ids: ["c1"] }),
    node("t1", "topic", { key: "alkenes", title: "Alkenes and alkynes: electrophilic addition", course_id: "c1", course_ids: ["c1"] }),
  ];
  const edges = [
    edge("h1", "c1", "o1", "has_outcome", { category: "containment", connection: null, strength: null }),
    edge("h2", "c1", "a1", "has_assessment", { category: "containment", connection: null, strength: null }),
    edge("h3", "c1", "t1", "covers_topic", { category: "containment", connection: null, strength: null }),
    edge("h4", "c2", "o2", "has_outcome", { category: "containment", connection: null, strength: null }),
    edge("k1", "o1", "p1", "contributes_to"),
    edge("k2", "a1", "o1", "assesses", { provenance: "proposed", connection: "partial" }),
    edge("k3", "t1", "o1", "supports", { connection: "strong" }),
    edge("k4", "o2", "o1", "prepares_for", { category: "horizontal", connection: "strong" }),
  ];
  return { nodes, edges };
}

describe("network model", () => {
  it("brightens what the layers select, draws their links, and keeps the other ends as faint context", () => {
    const v = fixture();
    const assess = LENSES.find((l) => l.key === "assess")!;
    const vis = networkVisibility(v, { ...DEFAULT_WEB_FILTERS, nodeTypes: assess.nodeTypes, linkKinds: assess.linkKinds });
    expect(vis.node.get("a1")).toBe("bright");
    expect(vis.node.get("o1")).toBe("bright");
    expect(vis.node.get("p1")).toBe("faded"); // program outcomes are not part of this view
    expect([...vis.link.keys()]).toEqual(["k2"]); // only assessment → outcome links
    const plo = networkVisibility(v, { ...DEFAULT_WEB_FILTERS, nodeTypes: ["course_outcome"], linkKinds: ["clo_plo"] });
    expect(plo.node.get("p1")).toBe("context"); // the far end of a drawn link
    expect(plo.link.get("k1")).toBe(0.45); // half strength when one end is only context
    expect(plo.bright).toBe(2);
  });

  it("recognises the one-click views, and hand-made layer combinations are custom", () => {
    expect(activeLens(DEFAULT_WEB_FILTERS)).toBe("all");
    const t = LENSES.find((l) => l.key === "topics")!;
    expect(activeLens({ ...DEFAULT_WEB_FILTERS, nodeTypes: [...t.nodeTypes].reverse(), linkKinds: t.linkKinds })).toBe("topics");
    expect(activeLens({ ...DEFAULT_WEB_FILTERS, nodeTypes: ["topic"] })).toBeNull();
  });

  it("focus: a course lights its members and their links; an element its links and ends", () => {
    const v = fixture();
    const vis = networkVisibility(v, DEFAULT_WEB_FILTERS);
    const course = focusOf(v, "c1", vis);
    expect([...course.nodes].sort()).toEqual(["a1", "c1", "o1", "o2", "p1", "t1"]);
    expect(course.links.has("k1") && course.links.has("k4")).toBe(true); // members' links, up and across
    const o2 = focusOf(v, "o2", vis);
    expect([...o2.nodes].sort()).toEqual(["o1", "o2"]);
    const plo = focusOf(v, "p1", vis);
    expect(plo.nodes.has("c1")).toBe(true); // the course of the outcome that serves it
  });

  it("places courses by year and term, and unknown years last", () => {
    const v = fixture();
    const byId = new Map(v.nodes.map((n) => [n.id, n]));
    const [c1, c2, o1] = [byId.get("c1")!, byId.get("c2")!, byId.get("o1")!];
    expect(yearX(c2, byId, 4)).toBeLessThan(yearX(c1, byId, 4));
    expect(yearX(o1, byId, 4)).toBe(yearX(c1, byId, 4)); // members follow their course
    expect(yearX(node("x", "course"), byId, 4)).toBeGreaterThan(3.5 * BAND);
  });

  it("labels, sizes and link fading", () => {
    const v = fixture();
    const byId = new Map(v.nodes.map((n) => [n.id, n]));
    expect(shortLabel(byId.get("p1")!)).toBe("PLO1");
    expect(shortLabel(byId.get("o1")!)).toBe("CO1"); // the cluster already names the course
    expect(shortLabel(byId.get("a1")!)).toBe("A-final");
    expect(shortLabel(byId.get("t1")!)).toBe("Alkenes and alkynes: elec…");
    expect(showLabel(byId.get("c1")!, 0.5, false)).toBe(true);
    expect(showLabel(byId.get("o1")!, 1, true)).toBe(false); // no pile-up when zoomed out
    expect(showLabel(byId.get("o1")!, 1.5, true)).toBe(true);
    expect(radius(byId.get("p1")!, 30)).toBeGreaterThan(radius(byId.get("o1")!, 30));
    expect(linkAlpha(50)).toBeCloseTo(0.32);
    expect(linkAlpha(900)).toBeLessThan(0.2);
    expect(linkAlpha(100000)).toBe(0.07);
  });

  it("an element outside the chosen view lights all of its links, so selecting it never lights up nothing", () => {
    const v = fixture();
    const vis = networkVisibility(v, { ...DEFAULT_WEB_FILTERS, nodeTypes: ["course_outcome"], linkKinds: ["clo_plo"] });
    expect(vis.node.get("t1")).toBe("faded");
    const topic = focusOf(v, "t1", vis);
    expect([...topic.links].sort()).toEqual(["h3", "k3"]);
    expect([...topic.nodes].sort()).toEqual(["c1", "o1", "t1"]);
    expect([...focusOf(v, "o1", vis).links]).toEqual(["k1"]); // an element in the view keeps to its drawn links
  });

  it("draws lit links even outside the view; the hide switches remove what they should", () => {
    const v = fixture();
    const vis = networkVisibility(v, { ...DEFAULT_WEB_FILTERS, nodeTypes: ["course_outcome"], linkKinds: ["clo_plo"] });
    const topic = focusOf(v, "t1", vis);
    const base = { vis, focus: null, lit: null, hideRest: false, isolate: false };
    expect(linkShown("k3", base)).toBe(false);
    expect(linkShown("k3", { ...base, focus: topic, lit: topic })).toBe(true);
    expect(linkShown("k1", { ...base, focus: topic, lit: topic, isolate: true })).toBe(false); // only the selection
    expect(nodeShown("a1", { ...base, focus: topic, lit: topic, isolate: true })).toBe(false);
    expect(nodeShown("a1", { ...base, hideRest: true })).toBe(false); // faded
    expect(nodeShown("o1", { ...base, hideRest: true })).toBe(true);
  });
});

describe("picking what is under the pointer", () => {
  const pn = (id: string, x: number, y: number, extra: Partial<PickNode> = {}): PickNode => ({ id, x, y, r: 5, shape: "circle", near: true, label: null, ...extra });

  it("knows the outline of each shape", () => {
    expect(insideShape("circle", 4.9, 0, 5)).toBe(true);
    expect(insideShape("circle", 3.6, 3.6, 5)).toBe(false);
    expect(insideShape("square", 4.4, 4.4, 5)).toBe(true); // corners at 0.9r
    expect(insideShape("square", 4.6, 0, 5)).toBe(false);
    expect(insideShape("diamond", 3, 3, 5)).toBe(true); // |dx| + |dy| within 1.25r
    expect(insideShape("diamond", 3.5, 3.5, 5)).toBe(false);
    expect(insideShape("box", 6.6, 5, 5)).toBe(true); // 1.35r wide, 1.05r high
    expect(insideShape("box", 0, 5.4, 5)).toBe(false);
    expect(insideShape("hexagon", 5.3, 0, 5)).toBe(true);
    expect(insideShape("circle", 5.5, 0, 5, 1)).toBe(true); // grown by a pad
    expect(shapeExtent("box", 5)).toBeGreaterThan(shapeExtent("circle", 5));
  });

  it("picks the element drawn on top, then the nearest within a few pixels of its shape", () => {
    const [a, b] = [pn("a", 0, 0), pn("b", 4, 0)];
    expect(pickAt(3, 0, 1, [a, b], [])).toEqual({ kind: "node", id: "b" }); // inside both: b is drawn last
    expect(pickAt(-6.5, 0, 1, [a, b], [])).toEqual({ kind: "node", id: "a" }); // 1.5 px outside a
    const crowd = [pn("c", 0, 0, { r: 1 }), pn("d", 10, 0, { r: 1 })];
    expect(pickAt(6, 0, 0.5, crowd, [])).toEqual({ kind: "node", id: "d" }); // the nearest centre wins
    expect(pickAt(4, 0, 0.5, crowd, [])).toEqual({ kind: "node", id: "c" });
  });

  it("gives every visible element a generous reach however small it is drawn", () => {
    const tiny = pn("t", 0, 0, { r: 1 });
    // zoomed out to a quarter: 9 px of reach is 36 graph units, plus 3 px (12) of slack
    expect(pickAt(40, 0, 0.25, [tiny], [])).toEqual({ kind: "node", id: "t" });
    expect(pickAt(50, 0, 0.25, [tiny], [])).toBeNull();
    // zoomed in, the shape is bigger than the reach: its outline plus the slack
    const big = pn("big", 0, 0, { r: 4 });
    expect(pickAt(4.5, 0, 4, [big], [])).toEqual({ kind: "node", id: "big" });
    expect(pickAt(5.5, 0, 4, [big], [])).toBeNull();
  });

  it("an element that is not lit reaches only its own shape, so it cannot take clicks aimed at lit links", () => {
    const faded = pn("f", 0, 0, { r: 1, near: false });
    const lit: PickLink = { id: "L", x1: -100, y1: 20, x2: 100, y2: 20 };
    expect(pickAt(0, 20, 0.5, [faded], [lit])).toEqual({ kind: "link", id: "L" });
    expect(pickAt(0, 4, 0.5, [faded], [lit])).toEqual({ kind: "node", id: "f" }); // on its shape, give or take the slack
  });

  it("elements beat links; a link is picked only when offered and within a few pixels; else the background", () => {
    const n = pn("n", 0, 0);
    const link: PickLink = { id: "L", x1: 0, y1: 0, x2: 100, y2: 0 };
    expect(pickAt(3, 0, 1, [n], [link])).toEqual({ kind: "node", id: "n" });
    expect(pickAt(50, 3, 1, [n], [link])).toEqual({ kind: "link", id: "L" });
    expect(pickAt(50, 6, 1, [n], [link])).toBeNull();
    expect(pickAt(50, 0, 1, [n], [])).toBeNull(); // a link that is not lit is not offered, so a click there clears
    expect(pickAt(500, 500, 1, [n], [link])).toBeNull();
  });

  it("the name of a program outcome or course picks it, after any element under the pointer", () => {
    const course = pn("c", 0, 0, { shape: "box", label: { x0: -20, x1: 20, y0: 8, y1: 20 } });
    const member = pn("m", 15, 14, { r: 2 });
    expect(pickAt(-15, 14, 1, [course, member], [])).toEqual({ kind: "node", id: "c" });
    expect(pickAt(15, 14, 1, [course, member], [])).toEqual({ kind: "node", id: "m" });
  });
});
