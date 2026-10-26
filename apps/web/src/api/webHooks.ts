import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { perf } from "../lib/perf";
import { getJson, qs } from "./client";
import type { components } from "./schema";

export type WebView = components["schemas"]["WebView"];
export type WebNode = components["schemas"]["WebNode"];
export type WebEdge = components["schemas"]["WebEdge"];
export type WebGap = components["schemas"]["Gap"];
export type PloThread = components["schemas"]["PloThread"];
export type ThreadStage = components["schemas"]["ThreadStage"];
export type CoursePair = components["schemas"]["CoursePair"];
export type Scorecard = components["schemas"]["Scorecard"];
export type StrengthLabel = "strong" | "moderate" | "weak" | "missing";

const V1 = "/api/v1";

/** The Alignment Web for the baseline version, or projected through a scenario (with baseline comparison). */
export function useWeb(
  versionId: string | null,
  opts: { scenarioId: string | null; focus?: string | null; detail?: "auto" | "full" | "overview"; pathway?: string | null },
) {
  return useQuery({
    queryKey: ["web", versionId, opts.scenarioId, opts.focus ?? null, opts.detail ?? "auto", opts.pathway ?? null],
    enabled: !!versionId,
    placeholderData: keepPreviousData,
    queryFn: () => {
      const params = qs({ focus: opts.focus ?? undefined, detail: opts.detail ?? "auto", pathway: opts.pathway ?? undefined });
      const url = opts.scenarioId ? `${V1}/scenarios/${opts.scenarioId}/web${params}` : `${V1}/versions/${versionId}/web${params}`;
      return perf.time("web_fetch", () => getJson<WebView>(url), { versionId, scenarioId: opts.scenarioId });
    },
  });
}

/** Baseline Web alongside the scenario one (for the before/after split view). */
export function useBaselineWeb(versionId: string | null, enabled: boolean) {
  return useQuery({
    queryKey: ["web", versionId, null, null, "auto", null],
    enabled: !!versionId && enabled,
    queryFn: () => getJson<WebView>(`${V1}/versions/${versionId}/web`),
  });
}

export type LinkDecision = { decision: "accept" | "reject" | "edit"; strength?: number | null; level?: string | null; rationale?: string | null };

/** Inline review of a proposed link (draft versions only). */
export function useLinkDecisions(versionId: string | null) {
  const qc = useQueryClient();
  const done = () => {
    for (const k of ["web", "graph", "reviews", "entity"]) qc.invalidateQueries({ queryKey: [k] });
  };
  const post = <T,>(url: string, body: unknown) =>
    getJson<T>(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  return {
    decide: useMutation({
      mutationFn: (v: { key: string } & LinkDecision) => {
        const { key, ...body } = v;
        return post<WebEdge>(`${V1}/versions/${versionId}/relationships/${key}/decision`, body);
      },
      onSuccess: done,
    }),
    bulk: useMutation({
      mutationFn: (v: { keys: string[]; decision: "accept" | "reject" }) =>
        post<{ updated: number; errors: { key: string; message: string }[] }>(`${V1}/versions/${versionId}/relationships/decisions`, v),
      onSuccess: done,
    }),
    propose: useMutation({
      mutationFn: (v: { course_ids?: string[] | null; kinds?: string[]; max_calls?: number }) =>
        post<{ job_id: string }>(`${V1}/versions/${versionId}/ai-alignment`, v),
    }),
  };
}

export interface MapPreview {
  route: string;
  model: string;
  calls: number;
  estimate_usd: number;
  courses: number;
  elements: number;
  draft: boolean;
  cost_cap_usd: number;
  batches: { kind: string; label: string; sources: number; estimate_usd: number }[];
}

/** What mapping connections would cost (no model call). */
export function useMapPreview(versionId: string | null, courseIds: string[], enabled: boolean) {
  return useQuery({
    queryKey: ["map-preview", versionId, courseIds],
    enabled: !!versionId && enabled,
    staleTime: 30_000,
    queryFn: () =>
      getJson<MapPreview>(`${V1}/versions/${versionId}/ai-map/preview${qs({ course_id: courseIds.length ? courseIds : undefined })}`),
  });
}

export function useMapRun(versionId: string | null) {
  return useMutation({
    mutationFn: (b: { course_ids: string[] | null; forward?: boolean; max_cost_usd?: number | null }) =>
      getJson<{ job_id: string; calls: number; estimate_usd: number; cost_cap_usd: number; model: string }>(
        `${V1}/versions/${versionId}/ai-map`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(b) },
      ),
  });
}
