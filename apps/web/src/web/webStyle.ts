/**
 * Visual encoding for the Alignment Web. Every state is shown by at least two channels (colour plus width, dash,
 * glyph, or text) so nothing depends on colour alone.
 */
import type { WebEdge } from "../api/webHooks";

export const TYPE_COLOR: Record<string, string> = {
  contributes_to: "#3f5a8f",
  assesses: "#7550b0",
  supports: "#23778a",
  prepares_for: "#2d63b8",
  formal_prerequisite: "#34405a",
  corequisite: "#34405a",
  inferred_preparation: "#6b5ca5",
  possible_overlap: "#a39676",
  has_outcome: "#b8c1cf",
  has_assessment: "#b8c1cf",
  covers_topic: "#b8c1cf",
};

export const STATUS_COLOR: Record<string, string> = {
  added: "#2f7d4f",
  strengthened: "#2f7d4f",
  removed: "#b03a3a",
  broken: "#b03a3a",
  weakened: "#b8730a",
  modified: "#b8730a",
  affected: "#5d6b82",
};

export const STATUS_GLYPH: Record<string, string> = {
  added: "+",
  strengthened: "▲",
  removed: "−",
  broken: "✕",
  weakened: "▼",
  modified: "~",
  affected: "•",
};

export const STATUS_TEXT: Record<string, string> = {
  added: "Added",
  strengthened: "Strengthened",
  removed: "Removed",
  broken: "Broken",
  weakened: "Weakened",
  modified: "Modified",
  affected: "Affected",
  unchanged: "Unchanged",
};

export const STRENGTH_WIDTH: Record<string, number> = { strong: 3.2, moderate: 2, weak: 1.1, missing: 1 };
export const STRENGTH_SHORT: Record<string, string> = { strong: "S", moderate: "M", weak: "W", missing: "—" };
export const STRENGTH_TEXT: Record<string, string> = { strong: "Strong", moderate: "Moderate", weak: "Weak", missing: "Missing" };
export const PROVENANCE_TEXT: Record<string, string> = {
  documented: "Documented in a source",
  accepted_inference: "Accepted interpretation",
  proposed: "Proposed (not yet reviewed)",
  scenario: "Scenario change",
};
export const PROVENANCE_DASH: Record<string, string | undefined> = {
  documented: undefined,
  accepted_inference: "10 3",
  proposed: "6 4",
  scenario: undefined,
};

export const THREAD_TEXT: Record<string, string> = {
  complete: "Introduced → reinforced → assessed",
  out_of_order: "All stages present, but out of order",
  never_assessed: "Never reaches the 'assess' level",
  incomplete: "Assessed, but a stage is missing",
  uncovered: "No course outcome contributes",
  levels_unstated: "Contributions state no level",
};

/** How direct a connection is (server: analysis.alignment.connection_level). Colour plus a glyph, never colour alone. */
export type ConnectionLevel = "direct" | "strong" | "partial";
export const CONNECTION_LEVELS: ConnectionLevel[] = ["direct", "strong", "partial"];
export const CONNECTION_COLOR: Record<ConnectionLevel, string> = { direct: "#1b7a3d", strong: "#2463a7", partial: "#9a5b00" };
export const NONE_COLOR = "#b42318"; // the AI examined the element and found no connection
export const UNMAPPED_COLOR = "#6b7385"; // not examined yet
export const CONNECTION_GLYPH: Record<ConnectionLevel, string> = { direct: "●●●", strong: "●●○", partial: "●○○" };
export const CONNECTION_TEXT: Record<ConnectionLevel, string> = {
  direct: "Direct",
  strong: "Strong",
  partial: "Partial / surmised",
};
export const CONNECTION_HELP: Record<ConnectionLevel, string> = {
  direct: "Stated in a source, or the source element explicitly addresses the target",
  strong: "Clearly implied",
  partial: "Plausible but indirect, or only part of the target (a surmise)",
};
export type ColorBy = "connection" | "type";

export interface EdgeLook {
  stroke: string;
  width: number;
  dash?: string;
  opacity: number;
  chip: string; // short text chip (never colour-only)
  title: string;
}

export function edgeLook(e: WebEdge, opts: { diff?: boolean; dim?: boolean; hl?: boolean; colorBy?: ColorBy } = {}): EdgeLook {
  const label = e.strength?.label ?? null;
  const conn = e.connection as ConnectionLevel | null | undefined;
  const byLevel = (opts.colorBy ?? "connection") === "connection" && !!conn;
  let stroke = byLevel ? CONNECTION_COLOR[conn!] : TYPE_COLOR[e.type] ?? "#8a96a8";
  let width = label ? STRENGTH_WIDTH[label] : e.category === "containment" ? 1 : 1.6;
  let dash = PROVENANCE_DASH[e.provenance];
  if (e.category === "containment") dash = "2 3";
  const parts: string[] = [];
  if (byLevel) parts.push(CONNECTION_GLYPH[conn!]);
  else if (label) parts.push(STRENGTH_SHORT[label]);
  if (e.provenance === "proposed") parts.push("AI?");
  if (e.bundle) parts.push(`×${e.bundle.count}`);
  let opacity = 1;
  if (opts.diff && e.status !== "unchanged") {
    stroke = STATUS_COLOR[e.status] ?? stroke;
    width = Math.max(width, 2.4);
    parts.unshift(STATUS_GLYPH[e.status] ?? "");
    if (e.status === "removed") dash = "4 3";
  } else if (opts.diff) {
    opacity = 0.25;
  }
  if (opts.dim) opacity = Math.min(opacity, 0.12);
  if (opts.hl) width += 1.4;
  const title = [
    e.type.replace(/_/g, " "),
    conn ? `${CONNECTION_TEXT[conn]} connection` : null,
    label ? `strength ${STRENGTH_TEXT[label]} (${e.strength?.effective?.toFixed(2)})` : null,
    PROVENANCE_TEXT[e.provenance],
    e.status !== "unchanged" ? STATUS_TEXT[e.status] : null,
  ]
    .filter(Boolean)
    .join(" · ");
  return { stroke, width, dash, opacity, chip: parts.join(" "), title };
}
