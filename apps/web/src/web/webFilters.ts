/** Layers for the Alignment Web: which elements and links are shown. Pure; the layout never depends on these. */
import type { WebEdge, WebGap, WebNode, WebView } from "../api/webHooks";
import type { ColorBy } from "./webStyle";

export type LinkKind =
  | "clo_plo"
  | "assessment_clo"
  | "topic_clo"
  | "clo_clo"
  | "assessment_next"
  | "topic_prep"
  | "prerequisite"
  | "preparation"
  | "containment"
  | "overlap";

export interface WebFilters {
  nodeTypes: string[];
  linkKinds: LinkKind[];
  strengths: string[];
  connections: string[]; // direct | strong | partial
  colorBy: ColorBy;
  provenance: string[];
  showGaps: boolean;
  changedOnly: boolean;
  years: number[]; // empty = all; 0 = timing unknown
}

export const NODE_TYPE_LABEL: Record<string, string> = {
  program_outcome: "Program outcomes",
  course: "Courses",
  course_outcome: "Course learning outcomes",
  assessment: "Assessments & assignments",
  topic: "Topics",
};

export const LINK_KIND_LABEL: Record<LinkKind, string> = {
  clo_plo: "Course outcome → program outcome",
  assessment_clo: "Assessment → course outcome",
  topic_clo: "Topic → course outcome",
  clo_clo: "Course outcome → later course outcome",
  assessment_next: "Assessment → later course outcome",
  topic_prep: "Topic preparation",
  prerequisite: "Formal prerequisites (course level)",
  preparation: "Expected preparation (course level)",
  containment: "Course contains (outcome / assessment / topic)",
  overlap: "Possible overlap",
};

export const VERTICAL_KINDS: LinkKind[] = ["clo_plo", "assessment_clo", "topic_clo"];
export const HORIZONTAL_KINDS: LinkKind[] = ["clo_clo", "assessment_next", "topic_prep", "prerequisite", "preparation"];

export const DEFAULT_WEB_FILTERS: WebFilters = {
  nodeTypes: ["program_outcome", "course", "course_outcome", "assessment", "topic"],
  linkKinds: ["clo_plo", "assessment_clo", "topic_clo", "clo_clo", "assessment_next", "topic_prep"],
  strengths: ["strong", "moderate", "weak"],
  connections: ["direct", "strong", "partial"],
  colorBy: "connection",
  provenance: ["documented", "accepted_inference", "proposed", "scenario"],
  showGaps: true,
  changedOnly: false,
  years: [],
};

export function linkKind(e: WebEdge, byId: Map<string, WebNode>): LinkKind {
  switch (e.type) {
    case "contributes_to":
      return "clo_plo";
    case "assesses":
      return "assessment_clo";
    case "supports":
      return "topic_clo";
    case "prepares_for": {
      const t = byId.get(e.source)?.type;
      return t === "assessment" ? "assessment_next" : t === "topic" ? "topic_prep" : "clo_clo";
    }
    case "formal_prerequisite":
    case "corequisite":
      return "prerequisite";
    case "inferred_preparation":
      return "preparation";
    case "possible_overlap":
      return "overlap";
    default:
      return "containment";
  }
}

function yearOf(n: WebNode, byId: Map<string, WebNode>): number | null | "any" {
  if (n.type === "program_outcome") return "any";
  if (n.type === "course") return n.year ?? null;
  const owners = (n.course_ids?.length ? n.course_ids : n.course_id ? [n.course_id] : []).map((c) => byId.get(c)).filter(Boolean) as WebNode[];
  if (!owners.length) return null;
  return owners[0].year ?? null;
}

export const isChanged = (status: string | undefined) => !!status && status !== "unchanged";

export function applyWebFilters(view: Pick<WebView, "nodes" | "edges">, f: WebFilters): { nodes: Set<string>; edges: Set<string> } {
  const byId = new Map(view.nodes.map((n) => [n.id, n]));
  const touchedByChange = new Set<string>();
  if (f.changedOnly) {
    for (const e of view.edges) if (isChanged(e.status)) { touchedByChange.add(e.source); touchedByChange.add(e.target); }
  }
  const nodes = new Set<string>();
  for (const n of view.nodes) {
    if (!f.nodeTypes.includes(n.type)) continue;
    if (f.years.length) {
      const y = yearOf(n, byId);
      if (y !== "any" && !f.years.includes(y ?? 0)) continue;
    }
    if (f.changedOnly && !isChanged(n.status) && !touchedByChange.has(n.id)) continue;
    nodes.add(n.id);
  }
  const edges = new Set<string>();
  for (const e of view.edges) {
    if (!nodes.has(e.source) || !nodes.has(e.target)) continue;
    if (edgePasses(e, byId, f)) edges.add(e.id);
  }
  return { nodes, edges };
}

/** The link layers alone (kind, source, strength, connection level, changed-only), whatever its ends. */
export function edgePasses(e: WebEdge, byId: Map<string, WebNode>, f: WebFilters): boolean {
  if (!f.linkKinds.includes(linkKind(e, byId)) && !e.bundle) return false;
  if (!f.provenance.includes(e.provenance)) return false;
  const label = e.strength?.label;
  if (label && label !== "missing" && !f.strengths.includes(label)) return false;
  if (e.connection && !(f.connections ?? DEFAULT_WEB_FILTERS.connections).includes(e.connection)) return false;
  if (f.changedOnly && !isChanged(e.status)) return false;
  return true;
}

/** Dotted "missing link" edges drawn for gaps that connect two visible nodes (e.g. a prerequisite with no bridge). */
export function gapEdges(gaps: WebGap[], visible: Set<string>): { id: string; source: string; target: string; gap: WebGap }[] {
  return gaps
    .filter((g) => g.source && g.target && visible.has(g.source) && visible.has(g.target))
    .map((g) => ({ id: g.id, source: g.source!, target: g.target!, gap: g }));
}

/** Node ids flagged by a gap (orphans, unassessed outcomes, never-assessed program outcomes). */
export function gapNodes(gaps: WebGap[]): Map<string, WebGap[]> {
  const m = new Map<string, WebGap[]>();
  for (const g of gaps) if (!g.source) for (const id of g.entity_ids) m.set(id, [...(m.get(id) ?? []), g]);
  return m;
}
