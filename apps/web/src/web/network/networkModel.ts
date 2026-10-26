/** Network view of the Alignment Web: a force-directed graph (the approach of meta-knowledge-graph's concept
 *  graph: canvas, faint links, colour-coded nodes, labels on zoom, dim-don't-delete filters, hover/click focus).
 *  Pure data rules here; drawing in NetworkGraph.tsx. Layers never move nodes: they only brighten, fade or hide. */
import type { WebEdge, WebNode, WebView } from "../../api/webHooks";
import { TERM_INDEX } from "../../lib/graphStyle";
import { DEFAULT_WEB_FILTERS, applyWebFilters, edgePasses, linkKind, type LinkKind, type WebFilters } from "../webFilters";

export type Shape = "diamond" | "square" | "circle" | "box" | "hexagon";

/** Muted node hues (links carry the saturated connection-level colours); shape repeats the type. */
export const NET_STYLE: Record<string, { color: string; shape: Shape; r: number; label: string }> = {
  program_outcome: { color: "#4b5f8f", shape: "diamond", r: 11, label: "Program outcome" },
  course: { color: "#3c4660", shape: "box", r: 8.5, label: "Course" },
  course_outcome: { color: "#6d7fa6", shape: "circle", r: 4.6, label: "Course outcome" },
  assessment: { color: "#8a6cc2", shape: "square", r: 4.4, label: "Assessment" },
  topic: { color: "#4f8a7b", shape: "hexagon", r: 4.2, label: "Topic" },
};

/** One-click views: which elements are bright and which links are drawn. */
export interface Lens {
  key: string;
  label: string;
  nodeTypes: string[];
  linkKinds: LinkKind[];
}
export const LENSES: Lens[] = [
  { key: "all", label: "Everything", nodeTypes: DEFAULT_WEB_FILTERS.nodeTypes, linkKinds: DEFAULT_WEB_FILTERS.linkKinds },
  { key: "clo_plo", label: "Outcomes → program outcomes", nodeTypes: ["program_outcome", "course", "course_outcome"], linkKinds: ["clo_plo"] },
  { key: "assess", label: "Assessments → outcomes", nodeTypes: ["course", "course_outcome", "assessment"], linkKinds: ["assessment_clo"] },
  { key: "topics", label: "Topics → outcomes", nodeTypes: ["course", "course_outcome", "topic"], linkKinds: ["topic_clo"] },
  {
    key: "across",
    label: "Earlier ↔ later",
    nodeTypes: ["course", "course_outcome", "assessment", "topic"],
    linkKinds: ["clo_clo", "assessment_next", "topic_prep", "prerequisite", "preparation"],
  },
];

const same = (a: readonly string[], b: readonly string[]) => a.length === b.length && a.every((x) => b.includes(x));
export const activeLens = (f: WebFilters) => LENSES.find((l) => same(l.nodeTypes, f.nodeTypes) && same(l.linkKinds, f.linkKinds))?.key ?? null;

export type NodeState = "bright" | "context" | "faded";
export interface NetVis {
  node: Map<string, NodeState>;
  /** link id → opacity factor (absent = not drawn) */
  link: Map<string, number>;
  bright: number;
  drawn: number;
}

/** Bright = passes the element layers. A link is drawn when it passes the link layers and touches a bright element;
 *  its other end is drawn faintly as context. Everything else fades (or is hidden with `hideRest`). */
export function networkVisibility(view: Pick<WebView, "nodes" | "edges">, f: WebFilters): NetVis {
  const byId = new Map(view.nodes.map((n) => [n.id, n]));
  const brightIds = applyWebFilters(view, f).nodes;
  const node = new Map<string, NodeState>(view.nodes.map((n) => [n.id, brightIds.has(n.id) ? "bright" : "faded"]));
  const link = new Map<string, number>();
  for (const e of view.edges) {
    if (e.bundle || !byId.has(e.source) || !byId.has(e.target)) continue;
    const s = brightIds.has(e.source);
    const t = brightIds.has(e.target);
    if (!(s || t) || !edgePasses(e, byId, f)) continue;
    link.set(e.id, s && t ? 1 : 0.45);
    for (const end of [e.source, e.target]) if (node.get(end) === "faded") node.set(end, "context");
  }
  return { node, link, bright: brightIds.size, drawn: link.size };
}

export interface Focus {
  nodes: Set<string>;
  links: Set<string>;
}

/** What to keep lit around an element: its drawn links and their ends. A course also lights its members and their
 *  links; a program outcome also lights the courses of the outcomes that serve it. An element with no drawn link
 *  (outside the chosen view) lights all of its links instead, so a selection never lights up nothing. */
export function focusOf(view: Pick<WebView, "nodes" | "edges">, id: string, vis: NetVis): Focus {
  const nodes = new Set([id]);
  const links = new Set<string>();
  const type = view.nodes.find((n) => n.id === id)?.type;
  const touching = (x: string) => view.edges.filter((e) => !e.bundle && (e.source === x || e.target === x));
  const add = (e: WebEdge) => {
    links.add(e.id);
    nodes.add(e.source);
    nodes.add(e.target);
  };
  const own = touching(id);
  for (const e of own) {
    if (vis.link.has(e.id)) add(e);
    if (e.category === "containment" && type === "course" && e.source === id) {
      nodes.add(e.target); // a member, even when containment links are not drawn
      for (const m of touching(e.target)) if (vis.link.has(m.id)) add(m);
    }
  }
  if (!links.size) for (const e of own) add(e);
  if (type === "program_outcome") {
    const serving = [...nodes].filter((n) => n !== id);
    for (const e of view.edges) if (e.category === "containment" && serving.includes(e.target)) nodes.add(e.source);
  }
  return { nodes, links };
}

/** What decides whether something is drawn: the layers (`vis`), the selection or highlight (`focus`), what is lit
 *  right now (`lit`: the hover preview, else the focus) and the two "hide" switches. */
export interface DrawState {
  vis: NetVis;
  focus: Focus | null;
  lit: Focus | null;
  hideRest: boolean;
  isolate: boolean;
}

/** An element is drawn unless "Only the selection" leaves it out, or "Hide the rest" removes it (faded, not lit). */
export function nodeShown(id: string, s: DrawState): boolean {
  if (s.isolate && s.focus?.nodes.size && !s.focus.nodes.has(id)) return false;
  return !(s.hideRest && (s.vis.node.get(id) ?? "faded") === "faded" && !s.lit?.nodes.has(id));
}

/** A link is drawn when it passes the link layers or is lit (an element outside the view shows all its links);
 *  "Only the selection" keeps just the selection's links. */
export function linkShown(id: string, s: DrawState): boolean {
  if (!s.vis.link.has(id) && !s.lit?.links.has(id)) return false;
  return !(s.isolate && s.focus?.links.size && !s.focus.links.has(id));
}

/** Shape geometry in multiples of the node radius, shared by drawing and picking so they cannot drift apart. */
export const SHAPE_GEOM = { square: 0.9, diamond: 1.25, hexagon: 1.08, boxW: 1.35, boxH: 1.05, boxCorner: 0.35 } as const;

/** Whether a point (dx, dy from the node's centre) is inside its drawn shape, grown by `pad`. */
export function insideShape(shape: Shape, dx: number, dy: number, r: number, pad = 0): boolean {
  const ax = Math.abs(dx);
  const ay = Math.abs(dy);
  if (shape === "square") return ax <= r * SHAPE_GEOM.square + pad && ay <= r * SHAPE_GEOM.square + pad;
  if (shape === "diamond") return ax + ay <= r * SHAPE_GEOM.diamond + pad * Math.SQRT2;
  if (shape === "box") return ax <= r * SHAPE_GEOM.boxW + pad && ay <= r * SHAPE_GEOM.boxH + pad;
  return Math.hypot(dx, dy) <= r * (shape === "hexagon" ? SHAPE_GEOM.hexagon : 1) + pad;
}

/** Farthest distance from a node's centre to the edge of its shape. */
export function shapeExtent(shape: Shape, r: number): number {
  if (shape === "square") return r * SHAPE_GEOM.square * Math.SQRT2;
  if (shape === "diamond") return r * SHAPE_GEOM.diamond;
  if (shape === "box") return r * Math.hypot(SHAPE_GEOM.boxW, SHAPE_GEOM.boxH);
  return r * (shape === "hexagon" ? SHAPE_GEOM.hexagon : 1);
}

export interface Box {
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}
/** A node as the picker sees it (graph units). `near` gives it a generous reach: it is lit, or in the view while
 *  nothing is lit. `label` is its name's box when one is drawn (program outcomes and courses). */
export interface PickNode {
  id: string;
  x: number;
  y: number;
  r: number;
  shape: Shape;
  near: boolean;
  label: Box | null;
}
export interface PickLink {
  id: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}
export type NetHit = { kind: "node" | "link"; id: string } | null;

/** Screen-pixel tolerances: the reach every visible element gets however small it is drawn (a ≥24 px target), the
 *  slack around shapes and links, and how far a press may move and still count as a click. */
export interface PickTolerance {
  reachPx: number;
  slopPx: number;
  linkPx: number;
  clickPx: number;
}
export const PICK_TOLERANCE: Record<"mouse" | "touch", PickTolerance> = {
  mouse: { reachPx: 9, slopPx: 3, linkPx: 4, clickPx: 6 },
  touch: { reachPx: 16, slopPx: 6, linkPx: 8, clickPx: 10 },
};

function segmentDistance(px: number, py: number, l: PickLink): number {
  const dx = l.x2 - l.x1;
  const dy = l.y2 - l.y1;
  const len2 = dx * dx + dy * dy;
  const t = len2 ? Math.max(0, Math.min(1, ((px - l.x1) * dx + (py - l.y1) * dy) / len2)) : 0;
  return Math.hypot(px - (l.x1 + t * dx), py - (l.y1 + t * dy));
}

/** What is under a point (graph units, at zoom `k`), in this order:
 *  1. inside an element's shape: the one drawn on top;
 *  2. within a few pixels of a shape: the nearest;
 *  3. on the name of a program outcome or course;
 *  4. the nearest element within its reach (only `near` ones reach beyond their shape, so a faded element cannot
 *     take a click aimed at something lit);
 *  5. the nearest link within a few pixels (only links the caller offers: the visible ones);
 *  6. nothing: the background. */
export function pickAt(x: number, y: number, k: number, nodes: PickNode[], links: PickLink[], tol: PickTolerance = PICK_TOLERANCE.mouse): NetHit {
  const slop = tol.slopPx / k;
  for (let i = nodes.length - 1; i >= 0; i--) {
    const n = nodes[i];
    if (insideShape(n.shape, x - n.x, y - n.y, n.r)) return { kind: "node", id: n.id };
  }
  let best: PickNode | null = null;
  let bestD = Infinity;
  for (const n of nodes) {
    const d = Math.hypot(x - n.x, y - n.y);
    if (d < bestD && insideShape(n.shape, x - n.x, y - n.y, n.r, slop)) [best, bestD] = [n, d];
  }
  if (best) return { kind: "node", id: best.id };
  for (let i = nodes.length - 1; i >= 0; i--) {
    const b = nodes[i].label;
    if (b && x >= b.x0 && x <= b.x1 && y >= b.y0 && y <= b.y1) return { kind: "node", id: nodes[i].id };
  }
  for (const n of nodes) {
    if (!n.near) continue;
    const d = Math.hypot(x - n.x, y - n.y);
    if (d < bestD && d <= Math.max(shapeExtent(n.shape, n.r), tol.reachPx / k) + slop) [best, bestD] = [n, d];
  }
  if (best) return { kind: "node", id: best.id };
  let link: PickLink | null = null;
  let linkD = tol.linkPx / k;
  for (const l of links) {
    const d = segmentDistance(x, y, l);
    if (d <= linkD) [link, linkD] = [l, d];
  }
  return link ? { kind: "link", id: link.id } : null;
}

/** Year of a course, or of the (first) course that owns an element; program outcomes have none. */
export function yearOf(n: WebNode, byId: Map<string, WebNode>): number | null {
  if (n.type === "course") return n.year ?? null;
  const owner = (n.course_ids?.length ? n.course_ids : n.course_id ? [n.course_id] : []).map((c) => byId.get(c)).find(Boolean);
  return owner?.year ?? null;
}

export const BAND = 520; // graph units per year of study

/** x target by year of study (terms spread within the year); unknown years after the last one. */
export function yearX(n: WebNode, byId: Map<string, WebNode>, lastYear: number): number {
  const y = yearOf(n, byId);
  const course = n.type === "course" ? n : byId.get((n.course_ids?.[0] ?? n.course_id) || "");
  const term = TERM_INDEX[course?.term ?? ""] ?? 0.5;
  return ((y ?? lastYear + 1) - 1) * BAND + (term - 0.5) * BAND * 0.35;
}

/** Node radius: by type, a little larger with more links (hubs stand out). */
export function radius(n: WebNode, degree: number): number {
  const base = NET_STYLE[n.type]?.r ?? 4;
  return base + Math.min(4, Math.sqrt(degree) * (n.type === "program_outcome" || n.type === "course" ? 0.35 : 0.25));
}

/** Short on-canvas label: codes for program outcomes and courses; an element's key without its course prefix
 *  ("CO3" for CHEM351-CO3), which its cluster already says; topics by (shortened) name. */
export function shortLabel(n: WebNode): string {
  if (n.type === "program_outcome") return n.title.split(":")[0] || n.key;
  if (n.type === "topic") return n.title.length > 26 ? `${n.title.slice(0, 25)}…` : n.title;
  if (n.type === "course") return n.key;
  const m = n.key.match(/^[A-Z]{2,5}\s?\d{3}[A-Z]?-(.+)$/);
  return m ? m[1] : n.key;
}

/** Labels appear as you zoom in: anchors early, elements late (lit ones a little earlier, never as a pile-up). */
export function showLabel(n: WebNode, zoom: number, lit: boolean): boolean {
  if (n.type === "program_outcome" || n.type === "course") return lit || zoom > 0.35;
  return zoom > (lit ? 1.3 : 2.2);
}

/** Base link opacity: fainter the more links are drawn, so a dense view reads as texture, not a hairball. */
export function linkAlpha(drawn: number): number {
  return Math.max(0.07, Math.min(0.32, 0.32 * Math.sqrt(220 / Math.max(drawn, 1))));
}

export const kindOf = (e: WebEdge, byId: Map<string, WebNode>) => linkKind(e, byId);
