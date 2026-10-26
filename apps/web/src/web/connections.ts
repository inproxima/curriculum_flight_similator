/** Connection map data: which element connects to what, how directly, and what the AI found nothing for. Pure. */
import type { WebEdge, WebNode, WebView } from "../api/webHooks";
import { TERM_INDEX } from "../lib/graphStyle";
import { CONNECTION_LEVELS, type ConnectionLevel } from "./webStyle";

export type MapKind = "contributes_to" | "assesses" | "supports" | "prepared_by" | "prepares_for";
/** An element's state for one direction: its best connection level, "none" (examined, nothing found), or "unmapped". */
export type MapState = ConnectionLevel | "none" | "unmapped";
export const MAP_STATES: MapState[] = ["direct", "strong", "partial", "none", "unmapped"];

export interface ConnIndex {
  byId: Map<string, WebNode>;
  out: Map<string, WebEdge[]>;
  inc: Map<string, WebEdge[]>;
  courses: WebNode[]; // by year, term, code
}

export const ownerIds = (n: WebNode | undefined): string[] =>
  !n ? [] : n.course_ids?.length ? n.course_ids : n.course_id ? [n.course_id] : [];

const courseSort = (a: WebNode, b: WebNode) =>
  (a.year ?? 99) - (b.year ?? 99) || (TERM_INDEX[a.term ?? ""] ?? 9) - (TERM_INDEX[b.term ?? ""] ?? 9) || a.key.localeCompare(b.key);

export function connIndex(view: Pick<WebView, "nodes" | "edges">): ConnIndex {
  const byId = new Map(view.nodes.map((n) => [n.id, n]));
  const out = new Map<string, WebEdge[]>();
  const inc = new Map<string, WebEdge[]>();
  for (const e of view.edges) {
    if (e.bundle || !e.connection) continue; // only alignment links carry a connection level
    out.set(e.source, [...(out.get(e.source) ?? []), e]);
    inc.set(e.target, [...(inc.get(e.target) ?? []), e]);
  }
  const courses = view.nodes.filter((n) => n.type === "course").sort(courseSort);
  return { byId, out, inc, courses };
}

export function bestLevel(edges: WebEdge[]): ConnectionLevel | null {
  let best: ConnectionLevel | null = null;
  for (const e of edges) {
    const l = e.connection as ConnectionLevel | null | undefined;
    if (l && (best === null || CONNECTION_LEVELS.indexOf(l) < CONNECTION_LEVELS.indexOf(best))) best = l;
  }
  return best;
}

/** Links of one type from `source` to `target`. */
export function linksBetween(ix: ConnIndex, source: string, target: string, type: string): WebEdge[] {
  return (ix.out.get(source) ?? []).filter((e) => e.target === target && e.type === type);
}

/** The element's state for a direction; null when the direction does not apply (e.g. nothing earlier exists). */
export function mapState(n: WebNode, kind: MapKind): MapState | null {
  const m = n.mapping?.[kind];
  if (!m) return null;
  if (m.best) return m.best;
  return m.verdict === "none" ? "none" : "unmapped";
}

export function summarize(nodes: WebNode[], type: string, kind: MapKind): Record<MapState, number> & { total: number } {
  const out = { direct: 0, strong: 0, partial: 0, none: 0, unmapped: 0, total: 0 };
  for (const n of nodes) {
    if (n.type !== type) continue;
    const s = mapState(n, kind);
    if (!s) continue;
    out[s] += 1;
    out.total += 1;
  }
  return out;
}

/** Members of a course of one type, by key. */
export function membersOf(view: Pick<WebView, "nodes">, courseId: string, type: string): WebNode[] {
  return view.nodes.filter((n) => n.type === type && ownerIds(n).includes(courseId)).sort((a, b) => a.key.localeCompare(b.key));
}

/** prepares_for links between an element and the members of another course: → it prepares for, ← it builds on. */
export function acrossCell(ix: ConnIndex, id: string, courseId: string): { after: WebEdge[]; before: WebEdge[] } {
  const inCourse = (other: string) => ownerIds(ix.byId.get(other)).includes(courseId);
  return {
    after: (ix.out.get(id) ?? []).filter((e) => e.type === "prepares_for" && inCourse(e.target)),
    before: (ix.inc.get(id) ?? []).filter((e) => e.type === "prepares_for" && inCourse(e.source)),
  };
}

/** All proposed (AI-surmised, unreviewed) links in a set. */
export const proposedOnly = (edges: WebEdge[]) => edges.length > 0 && edges.every((e) => e.provenance === "proposed");
