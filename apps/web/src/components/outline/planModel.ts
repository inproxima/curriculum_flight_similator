/** Pure selection logic for an outline-import plan (unit-tested; no React). */

export interface ModelItem {
  id: string;
  section: string;
  action: string;
  title: string;
  noop: boolean;
  selectable: boolean;
  default_selected: boolean;
  blocked_reason?: string | null;
  reason: string;
  depends_on?: string[];
  excludes?: string[];
  fills?: string[];
  link?: { basis: string; model_strength?: number | null; strength?: number | null; rel_type: string } | null;
}
export interface ModelPlan {
  items: ModelItem[];
}

export function initialSelection(plan: ModelPlan): Set<string> {
  return new Set(plan.items.filter((i) => i.default_selected && i.selectable && !i.noop).map((i) => i.id));
}

function index(plan: ModelPlan) {
  const byId = new Map(plan.items.map((i) => [i.id, i]));
  const dependents = new Map<string, string[]>();
  for (const i of plan.items) for (const d of i.depends_on ?? []) dependents.set(d, [...(dependents.get(d) ?? []), i.id]);
  return { byId, dependents };
}

/** Turn one item on or off, keeping the selection consistent:
 *  on  → also selects what it depends on, and deselects anything it excludes (and whatever depends on those);
 *  off → also deselects everything that depends on it.
 *  Returns the new selection and the other items that changed. */
export function toggle(plan: ModelPlan, sel: Set<string>, id: string, on: boolean): { sel: Set<string>; also: string[] } {
  const { byId, dependents } = index(plan);
  const next = new Set(sel);
  const changed = new Set<string>();
  const remove = (x: string) => {
    if (!next.has(x)) return;
    next.delete(x);
    changed.add(x);
    for (const d of dependents.get(x) ?? []) remove(d);
  };
  const addable = (x: string, seen = new Set<string>()): boolean => {
    const it = byId.get(x);
    if (!it || !it.selectable || it.noop) return false;
    if (seen.has(x)) return true;
    seen.add(x);
    return (it.depends_on ?? []).every((d) => addable(d, seen));
  };
  const add = (x: string) => {
    const it = byId.get(x)!;
    if (next.has(x)) return;
    next.add(x);
    changed.add(x);
    for (const d of it.depends_on ?? []) add(d);
    for (const e of it.excludes ?? []) remove(e);
  };
  if (on) {
    if (!addable(id)) return { sel: new Set(sel), also: [] }; // something it needs can't be applied
    add(id);
  } else remove(id);
  changed.delete(id);
  return { sel: next, also: [...changed] };
}

/** Select (or clear) every selectable link in a group; inferred links only at or above a strength threshold.
 *  Selecting never overrides another choice: a link that would deselect a selected item (e.g. an element being
 *  retired) or that needs something that can't be added is skipped and counted. */
export function selectLinks(
  plan: ModelPlan,
  sel: Set<string>,
  ids: string[],
  on: boolean,
  minStrength = 0,
): { sel: Set<string>; skipped: number } {
  let cur = new Set(sel);
  let skipped = 0;
  for (const id of ids) {
    const it = plan.items.find((i) => i.id === id);
    if (!it?.link) continue;
    const s = it.link.basis === "stated" ? 1 : it.link.model_strength ?? it.link.strength ?? 0;
    if (on && (s < minStrength || cur.has(id))) continue;
    if (on && (it.excludes ?? []).some((e) => cur.has(e))) {
      skipped += 1;
      continue;
    }
    const r = toggle(plan, cur, id, on);
    if (on && !r.sel.has(id)) skipped += 1;
    cur = r.sel;
  }
  return { sel: cur, skipped };
}

export function blockedReason(item: ModelItem): string | null {
  if (item.noop) return item.reason || "Nothing to change.";
  if (!item.selectable) return item.blocked_reason ?? item.reason ?? "Not available.";
  return null;
}

export function changeCount(plan: ModelPlan, sel: Set<string>): number {
  return plan.items.filter((i) => sel.has(i.id) && !i.noop).length;
}

export function sectionCounts(plan: ModelPlan, sel: Set<string>): Record<string, { total: number; selected: number; unchanged: number }> {
  const out: Record<string, { total: number; selected: number; unchanged: number }> = {};
  for (const i of plan.items) {
    const key = i.section === "links" ? `links:${i.link?.basis ?? "inferred"}` : i.section;
    const s = (out[key] ??= { total: 0, selected: 0, unchanged: 0 });
    s.total += 1;
    if (i.noop) s.unchanged += 1;
    if (sel.has(i.id)) s.selected += 1;
  }
  return out;
}

export function applyLabel(changes: number, summary?: { closes: string[]; missing_before: number } | null): string {
  const base = changes === 1 ? "Apply 1 change" : `Apply ${changes} changes`;
  if (!summary) return base;
  return `${base} · closes ${summary.closes.length} of ${summary.missing_before} missing piece${summary.missing_before === 1 ? "" : "s"}`;
}

export const ACTION_TEXT: Record<string, string> = {
  add: "New",
  replace: "Update",
  keep: "Unchanged",
  retire: "Retire",
  detach: "Detach",
  set_field: "Course detail",
  link: "Link",
};
