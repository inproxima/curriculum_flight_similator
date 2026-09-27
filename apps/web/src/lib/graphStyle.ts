import { MarkerType, type Edge } from "@xyflow/react";
import type { GraphEdge, GraphNode } from "../api/types";

export const TERM_INDEX: Record<string, number> = { fall: 0, winter: 1, spring: 2, summer: 2, full_year: 3 };
const SLOTS = 5; // per year: fall, winter, spring/summer, full year, term not stated
export const TERM_LABEL: Record<string, string> = {
  fall: "Fall", winter: "Winter", spring: "Spring", summer: "Summer", full_year: "Full year", unknown: "Term unknown",
};
export const CLASS_LABEL: Record<string, string> = {
  required: "Required", elective: "Elective", pathway_required: "Pathway", optional: "Optional", unknown: "Unknown",
};
export const TYPE_LABEL: Record<string, string> = {
  formal_prerequisite: "Formal prerequisite",
  corequisite: "Co-requisite",
  inferred_preparation: "Expected preparation (inferred)",
  prepares_for: "Topic/outcome preparation",
  contributes_to: "Contributes to program outcome",
  assesses: "Assessment alignment",
  has_outcome: "Has outcome",
  covers_topic: "Covers topic",
  has_assessment: "Has assessment",
  possible_overlap: "Possible overlap",
};
export const LAYER_LABEL: Record<string, string> = {
  prerequisites: "Formal prerequisites",
  preparation: "Expected preparation",
  plo_alignment: "Program-outcome alignment",
  topics: "Topic progression",
  assessment: "Assessment alignment",
};

/** Lane index: years in order, terms within a year; unknown placement goes to a final lane. */
export function partitionOf(n: GraphNode, courseById: Map<string, GraphNode>): number {
  if (n.type === "program_outcome") return 1000;
  if (n.type === "topic") return 999;
  const course = n.type === "course" ? n : n.owner_course ? courseById.get(n.owner_course) : undefined;
  if (!course || course.year == null) return 998;
  const t = course.term && course.term in TERM_INDEX ? TERM_INDEX[course.term] : 4;
  return (course.year - 1) * SLOTS + t;
}

export function laneLabel(p: number): string {
  if (p === 1000) return "Program outcomes";
  if (p === 999) return "Topics";
  if (p === 998) return "Timing unknown";
  const year = Math.floor(p / SLOTS) + 1;
  const t = p % SLOTS;
  return `Year ${year} · ${["Fall", "Winter", "Spring/Summer", "Full year", "Term not stated"][t]}`;
}

const COLORS = {
  formal: "#34405a",
  inferred: "#6b5ca5",
  prep: "#5a7fa8",
  align: "#8a96a8",
  assess: "#9a7cc4",
  overlap: "#b0a48a",
  added: "#2f7d4f",
  removed: "#a63d3d",
};

export function edgeStyle(e: GraphEdge, dim: boolean, hl: boolean): Partial<Edge> {
  let stroke = COLORS.align;
  let dash: string | undefined;
  let width = 1.4;
  let label: string | undefined;
  let marker = true;
  switch (e.type) {
    case "formal_prerequisite":
      stroke = COLORS.formal;
      width = 1.8;
      if (e.alternative_group) label = "or";
      break;
    case "corequisite":
      stroke = COLORS.formal;
      dash = "2 3";
      label = "co-req";
      marker = false;
      break;
    case "inferred_preparation":
      stroke = COLORS.inferred;
      dash = "7 5";
      break;
    case "prepares_for":
      stroke = COLORS.prep;
      dash = e.basis === "explicit_statement" ? "1 0" : "3 4";
      break;
    case "contributes_to":
      stroke = COLORS.align;
      label = e.level ? { introduce: "I", reinforce: "R", assess: "A" }[e.level] : undefined;
      break;
    case "assesses":
      stroke = COLORS.assess;
      break;
    case "possible_overlap":
      stroke = COLORS.overlap;
      dash = "2 6";
      label = "overlap";
      marker = false;
      break;
    default:
      stroke = "#b5bfcc";
      width = 1;
  }
  if (e.basis !== "explicit_statement" && !dash) dash = "6 4";
  if (e.review_state === "proposed") {
    label = label ? `${label} · proposed` : "proposed";
  }
  if (e.derived) label = label ? `${label} · via ${e.via.length}` : `via ${e.via.length}`;
  if (e.status === "added") {
    stroke = COLORS.added;
    width = 2.4;
    label = `+ added${label ? ` · ${label}` : ""}`;
  } else if (e.status === "removed") {
    stroke = COLORS.removed;
    width = 2.2;
    dash = "4 3";
    label = `− removed${label ? ` · ${label}` : ""}`;
  }
  if (hl) width += 1.4;
  return {
    style: { stroke, strokeWidth: width, strokeDasharray: dash, opacity: dim ? 0.15 : 1 },
    label,
    labelStyle: { fontSize: 10, fill: stroke },
    labelBgStyle: { fill: "#fff", fillOpacity: 0.85 },
    markerEnd: marker ? { type: MarkerType.ArrowClosed, color: stroke, width: 14, height: 14 } : undefined,
    zIndex: hl ? 10 : 0,
  };
}

export const LEGEND: { label: string; stroke: string; dash?: string; note?: string }[] = [
  { label: "Formal prerequisite (documented)", stroke: COLORS.formal },
  { label: "Alternative (OR) prerequisite — label “or”", stroke: COLORS.formal },
  { label: "Co-requisite", stroke: COLORS.formal, dash: "2 3" },
  { label: "Expected preparation (inferred)", stroke: COLORS.inferred, dash: "7 5" },
  { label: "Topic/outcome preparation", stroke: COLORS.prep },
  { label: "Program-outcome contribution (I/R/A)", stroke: COLORS.align },
  { label: "Assessment alignment", stroke: COLORS.assess },
  { label: "Possible overlap (no dependency)", stroke: COLORS.overlap, dash: "2 6" },
  { label: "Scenario: added (+)", stroke: COLORS.added },
  { label: "Scenario: removed (−)", stroke: COLORS.removed, dash: "4 3" },
];
