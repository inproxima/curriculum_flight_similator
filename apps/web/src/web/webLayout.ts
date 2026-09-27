/**
 * Deterministic Alignment Web layout (no ELK, O(n)). Positions depend only on the node set, never on filters,
 * so diff, ripple, and before/after views keep every element in the same place.
 *
 *   y ─ program-outcome band (barycentre of the courses that contribute)
 *     ─ course headers, one column per course, ordered by year/term then code, grouped into year·term bands
 *     ─ per column: course outcomes, then assessments, then topics owned by that course only
 *     ─ shared-topic band (topics covered by several courses, at the barycentre of their columns)
 */
import type { WebEdge, WebNode, WebView } from "../api/webHooks";

export const COL_W = 220;
export const COL_GAP = 28;
export const BAND_GAP = 60;
export const PLO_W = 190;
export const PLO_GAP = 16;
export const SIZES: Record<string, number> = { course: 74, course_outcome: 70, assessment: 58, topic: 32, program_outcome: 78 };
const STACK_GAP = 8;
const SECTION_GAP = 22;
const HEADER_Y = 150;
const TOPIC_W = 170;

export interface Band { label: string; x0: number; x1: number }
export interface WebLayout {
  positions: Record<string, { x: number; y: number }>;
  widths: Record<string, number>;
  bands: Band[];
  columnX: Record<string, number>;
  width: number;
  height: number;
}

type Cols = WebView["columns"];

const sortKey = (n: WebNode) => n.key;

/** Spread items (sorted by desired x) so none overlap; ties broken by key for determinism. */
function sweep(items: { id: string; key: string; want: number }[], w: number, gap: number, minX: number): Record<string, number> {
  const sorted = [...items].sort((a, b) => a.want - b.want || a.key.localeCompare(b.key));
  const out: Record<string, number> = {};
  let cursor = minX;
  for (const it of sorted) {
    const x = Math.max(it.want - w / 2, cursor);
    out[it.id] = x;
    cursor = x + w + gap;
  }
  return out;
}

export function layoutWeb(nodes: WebNode[], edges: WebEdge[], columns: Cols): WebLayout {
  const positions: WebLayout["positions"] = {};
  const widths: Record<string, number> = {};
  const columnX: Record<string, number> = {};
  const bands: Band[] = [];
  const byId = new Map(nodes.map((n) => [n.id, n]));

  // columns
  let x = 0;
  let band: string | null = null;
  const present = columns.filter((c) => byId.has(c.course_id));
  const orphanOwned = nodes.some((n) => n.type !== "course" && n.type !== "program_outcome" && n.type !== "topic" && !(n.course_id && byId.has(n.course_id)));
  const cols = orphanOwned ? [...present, { course_id: "__unassigned__", key: "~", title: "Not linked to a course", band: "Unassigned", year: null, term: null, time_index: null }] : present;
  for (const c of cols) {
    if (band !== null && c.band !== band) x += BAND_GAP;
    if (c.band !== band) bands.push({ label: c.band, x0: x, x1: x + COL_W });
    else bands[bands.length - 1].x1 = x + COL_W;
    band = c.band;
    columnX[c.course_id] = x;
    x += COL_W + COL_GAP;
  }
  const width = Math.max(x - COL_GAP, PLO_W);

  // column stacks
  const stacks: Record<string, { clo: WebNode[]; assessment: WebNode[]; topic: WebNode[] }> = {};
  const shared: WebNode[] = [];
  for (const n of [...nodes].sort((a, b) => sortKey(a).localeCompare(sortKey(b)))) {
    if (n.type === "course") {
      positions[n.id] = { x: columnX[n.id], y: HEADER_Y };
      widths[n.id] = COL_W;
      continue;
    }
    if (n.type === "program_outcome") continue;
    if (n.type === "topic" && (n.course_ids ?? []).filter((c) => byId.has(c)).length !== 1) {
      shared.push(n);
      continue;
    }
    const owner = n.type === "topic" ? (n.course_ids ?? []).find((c) => byId.has(c)) : n.course_id && byId.has(n.course_id) ? n.course_id : null;
    const col = owner ?? "__unassigned__";
    stacks[col] ??= { clo: [], assessment: [], topic: [] };
    stacks[col][n.type === "course_outcome" ? "clo" : n.type === "assessment" ? "assessment" : "topic"].push(n);
  }
  let bottom = HEADER_Y + SIZES.course;
  for (const [col, st] of Object.entries(stacks)) {
    let y = HEADER_Y + SIZES.course + SECTION_GAP;
    for (const section of [st.clo, st.assessment, st.topic]) {
      if (!section.length) continue;
      for (const n of section) {
        positions[n.id] = { x: columnX[col] ?? 0, y };
        widths[n.id] = COL_W;
        y += SIZES[n.type] + STACK_GAP;
      }
      y += SECTION_GAP;
    }
    bottom = Math.max(bottom, y);
  }

  // program outcomes: barycentre of contributing columns
  const colCentre = (cid: string | null | undefined) => (cid && columnX[cid] !== undefined ? columnX[cid] + COL_W / 2 : null);
  const want: Record<string, number[]> = {};
  for (const e of edges) {
    if (e.type !== "contributes_to") continue;
    const src = byId.get(e.source);
    const tgt = byId.get(e.target);
    if (!src || !tgt || tgt.type !== "program_outcome") continue;
    const cx = colCentre(src.type === "course" ? src.id : src.course_id);
    if (cx !== null) (want[tgt.id] ??= []).push(cx);
  }
  const plos = nodes.filter((n) => n.type === "program_outcome");
  const avg = (xs: number[] | undefined) => (xs?.length ? xs.reduce((a, b) => a + b, 0) / xs.length : width + PLO_W);
  const ploX = sweep(plos.map((p) => ({ id: p.id, key: p.key, want: avg(want[p.id]) })), PLO_W, PLO_GAP, 0);
  for (const p of plos) {
    positions[p.id] = { x: ploX[p.id], y: 0 };
    widths[p.id] = PLO_W;
  }

  // shared topics: barycentre of covering columns
  const topicY = bottom + 40;
  const tX = sweep(
    shared.map((t) => ({ id: t.id, key: t.key, want: avg((t.course_ids ?? []).map(colCentre).filter((v): v is number => v !== null)) })),
    TOPIC_W,
    12,
    0,
  );
  for (const t of shared) {
    positions[t.id] = { x: tX[t.id], y: topicY };
    widths[t.id] = TOPIC_W;
  }
  const maxX = Math.max(width, ...Object.entries(positions).map(([id, p]) => p.x + (widths[id] ?? 0)));
  return { positions, widths, bands, columnX, width: maxX, height: topicY + (shared.length ? SIZES.topic : 0) };
}
