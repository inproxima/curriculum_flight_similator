/** Pure helpers for replaying a simulation's ripple one hop at a time. */
import type { WebNode, WebView } from "../api/webHooks";

export interface RippleImpact {
  entity_id: string;
  key: string;
  title: string;
  type: string;
  course_key: string | null;
  status: "removed" | "added" | "broken" | "weakened" | "strengthened" | "modified" | "affected";
  hop: number;
  path: string[];
  edge_keys: string[];
  strength_before: number | null;
  strength_after: number | null;
  severity: string;
  rank: number;
  explanation: string;
}
export interface Ripple {
  max_hop: number;
  steps: { hop: number; node_ids: string[]; edge_keys: string[] }[];
  impacts: RippleImpact[];
  edge_status: Record<string, string>;
}
export interface RippleFrame {
  nodes: Map<string, string>; // id → status, for everything reached by `step`
  edges: Map<string, string>;
  pulseNodes: Set<string>;
  pulseEdges: Set<string>;
}

export function clampStep(ripple: Ripple, step: number): number {
  return Math.max(0, Math.min(ripple.max_hop, Math.round(step)));
}

export function visibleAt(ripple: Ripple, step: number): RippleFrame {
  const s = clampStep(ripple, step);
  const byId = new Map(ripple.impacts.map((i) => [i.entity_id, i]));
  const nodes = new Map<string, string>();
  const edges = new Map<string, string>();
  const pulseNodes = new Set<string>();
  const pulseEdges = new Set<string>();
  for (const st of ripple.steps) {
    if (st.hop > s) break;
    for (const id of st.node_ids) {
      nodes.set(id, byId.get(id)?.status ?? "affected");
      if (st.hop === s) pulseNodes.add(id);
    }
    for (const k of st.edge_keys) {
      edges.set(k, ripple.edge_status[k] ?? "affected");
      if (st.hop === s) pulseEdges.add(k);
    }
  }
  return { nodes, edges, pulseNodes, pulseEdges };
}

const VERB: Record<string, string> = {
  removed: "removed",
  added: "added",
  broken: "breaks",
  weakened: "weakens",
  strengthened: "strengthens",
  modified: "changes",
  affected: "is affected",
};

export function captionFor(ripple: Ripple, step: number): string {
  const s = clampStep(ripple, step);
  const here = ripple.impacts.filter((i) => i.hop === s).sort((a, b) => a.rank - b.rank);
  if (!here.length) return s === 0 ? "Step 0: the change itself." : `Step ${s}: links only.`;
  const head = here.slice(0, 3).map((i) => `${i.key} ${VERB[i.status]}`).join("; ");
  const more = here.length > 3 ? ` (+${here.length - 3} more)` : "";
  return `${s === 0 ? "Step 0 — the change" : `Step ${s}`}: ${head}${more}.`;
}

/** The Web as it looks at a given ripple step: reached elements carry their status, everything else is unchanged. */
export function applyRipple(view: WebView, ripple: Ripple, step: number): { view: WebView; frame: RippleFrame } {
  const frame = visibleAt(ripple, step);
  const nodes = view.nodes.map((n) => ({ ...n, status: (frame.nodes.get(n.id) ?? "unchanged") as WebNode["status"] }));
  const edges = view.edges.map((e) => ({ ...e, status: ((e.key && frame.edges.get(e.key)) || "unchanged") as WebNode["status"] }));
  return { view: { ...view, nodes, edges }, frame };
}
