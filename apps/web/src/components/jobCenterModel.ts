/** What the job center shows for long-running AI jobs (connection mapping, missing-link proposals). Pure. */
import type { Job } from "../api/hooks";

export const AI_JOB_KINDS = ["ai_map_alignment", "ai_propose_alignment"] as const;
export const RECENT_MS = 24 * 3600 * 1000; // a finished job is announced until dismissed, for a day

export const isActive = (j: Pick<Job, "status">) => j.status === "queued" || j.status === "running";
export const isAiJob = (j: Pick<Job, "kind">) => (AI_JOB_KINDS as readonly string[]).includes(j.kind);

interface MapPayload {
  curriculum_version_id?: string;
  course_ids?: string[] | null;
  label?: string;
  version_label?: string | null;
  estimate_usd?: number;
}
interface MapResult {
  proposed?: number;
  by_level?: Record<string, number>;
  verdicts?: Record<string, number>;
  cost_usd?: number;
  batches?: number;
  batches_skipped_by_cap?: number;
}

export const payloadOf = (j: Job) => (j.payload ?? {}) as MapPayload;
const resultOf = (j: Job) => (j.result ?? {}) as MapResult;

/** Active jobs, then finished ones from the last day that nobody dismissed; newest first. */
export function visibleJobs(jobs: Job[], dismissed: string[], now = Date.now()): Job[] {
  return jobs.filter((j) => isAiJob(j) && (isActive(j) || (!dismissed.includes(j.id) && now - Date.parse(j.updated_at) < RECENT_MS)));
}

/** Running jobs first (all of them), then the newest finished ones; the rest are summarized as "+N more". */
export function arrange(visible: Job[], maxDone = 2): { active: Job[]; done: Job[]; more: Job[] } {
  const active = visible.filter(isActive);
  const finished = visible.filter((j) => !isActive(j)).sort((a, b) => Date.parse(b.updated_at) - Date.parse(a.updated_at));
  return { active, done: finished.slice(0, maxDone), more: finished.slice(maxDone) };
}

export function jobTitle(j: Job): string {
  const map = j.kind === "ai_map_alignment";
  if (isActive(j)) return map ? "Mapping connections" : "Proposing missing links";
  if (j.status === "failed") return map ? "Connection mapping stopped" : "Link proposals stopped";
  if (j.status === "cancelled") return map ? "Connection mapping cancelled" : "Link proposals cancelled";
  return map ? "Connection map ready" : "Missing-link proposals ready";
}

/** "BHSc 2026–27 · all 17 courses" (older jobs carry no label: "whole program" / "2 courses"). */
export function jobScope(j: Job, versionLabel?: (id: string) => string | undefined): string {
  const p = payloadOf(j);
  const scope = p.label ?? (p.course_ids?.length ? `${p.course_ids.length} course${p.course_ids.length === 1 ? "" : "s"}` : "whole program");
  const version = p.version_label ?? (p.curriculum_version_id ? versionLabel?.(p.curriculum_version_id) : undefined);
  return version ? `${version} · ${scope}` : scope;
}

export function duration(fromIso: string, toIso: string | number): string {
  const ms = (typeof toIso === "number" ? toIso : Date.parse(toIso)) - Date.parse(fromIso);
  const min = Math.floor(ms / 60000);
  if (min < 1) return `${Math.max(1, Math.round(ms / 1000))} s`;
  return min < 60 ? `${min} min` : `${Math.floor(min / 60)} h ${min % 60} min`;
}

/** What a finished job produced, in words. */
export function resultLines(j: Job): string[] {
  const r = resultOf(j);
  const took = duration(j.created_at, j.updated_at);
  if (j.status === "failed" || j.status === "cancelled") {
    const why = j.status === "failed" ? (j.error ?? "unknown error").slice(0, 240) : "Stopped on request.";
    return [why, "Links proposed before it stopped are kept (dashed, AI?)."];
  }
  if (j.kind === "ai_map_alignment") {
    const lv = r.by_level ?? {};
    const lines = [
      `${r.proposed ?? 0} link${r.proposed === 1 ? "" : "s"} proposed: ${lv.direct ?? 0} direct · ${lv.strong ?? 0} strong · ${lv.partial ?? 0} partial`,
    ];
    const tail = [`${r.verdicts?.none ?? 0} “no connection found”`, took];
    if (r.cost_usd != null) tail.push(`$${r.cost_usd.toFixed(2)}`);
    lines.push(tail.join(" · "));
    return lines;
  }
  const skipped = r.batches_skipped_by_cap ? ` · ${r.batches_skipped_by_cap} batch(es) skipped by the call limit` : "";
  return [`${r.proposed ?? 0} missing link${r.proposed === 1 ? "" : "s"} proposed · ${took}${skipped}`];
}

/** Browser-tab title while jobs run or wait to be seen: "⏳ 54% · …", "✓ …", "⚠ …". */
export function titlePrefix(visible: Job[]): string {
  const active = visible.filter(isActive);
  if (active.length) return `⏳ ${Math.round(Math.min(...active.map((j) => j.progress)) * 100)}% · `;
  if (visible.some((j) => j.status === "failed")) return "⚠ ";
  return visible.length ? "✓ " : "";
}
