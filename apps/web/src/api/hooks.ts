import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getJson, qs, unwrap } from "./client";
import type { components } from "./schema";
import { perf } from "../lib/perf";
import type { Downstream, EntityDetail, GraphView, PriorLearning, RelationshipDetail } from "./types";

export type Program = components["schemas"]["ProgramOut"];
export type Version = components["schemas"]["VersionOut"];
export type Pathway = components["schemas"]["PathwayOut"];
export type DocumentOut = components["schemas"]["DocumentOut"];
export type DocumentVersionOut = components["schemas"]["DocumentVersionOut"];
export type ReviewItem = components["schemas"]["ReviewItemOut"];
export type Job = components["schemas"]["JobOut"];
export type Span = components["schemas"]["SpanOut"];

const V1 = "/api/v1";

export function usePrograms() {
  return useQuery({ queryKey: ["programs"], queryFn: async () => unwrap(await api.GET("/api/v1/programs")) });
}

export function useVersions(programId: string | null) {
  return useQuery({
    queryKey: ["versions", programId],
    enabled: !!programId,
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/programs/{program_id}/versions", { params: { path: { program_id: programId! } } })),
  });
}

export function usePathways(programId: string | null) {
  return useQuery({
    queryKey: ["pathways", programId],
    enabled: !!programId,
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/programs/{program_id}/pathways", { params: { path: { program_id: programId! } } })),
  });
}

export function useGraph(
  versionId: string | null,
  opts: { layers: string[]; expand: string[]; focus: string | null; scenarioId: string | null; compare: boolean },
) {
  return useQuery({
    queryKey: ["graph", versionId, opts],
    enabled: !!versionId,
    placeholderData: keepPreviousData,
    queryFn: () => {
      const params = qs({ layers: opts.layers.length ? opts.layers : ["none"], expand: opts.expand, focus: opts.focus ?? undefined });
      const url = opts.scenarioId
        ? `${V1}/scenarios/${opts.scenarioId}/graph${params}${params ? "&" : "?"}compare=${opts.compare}`
        : `${V1}/versions/${versionId}/graph${params}`;
      return perf.time("graph_fetch", () => getJson<GraphView>(url), { versionId, scenarioId: opts.scenarioId });
    },
  });
}

/** Entity detail in the context of either the baseline version or a scenario projection. */
export function useEntity(versionId: string | null, entityId: string | null, scenarioId: string | null) {
  return useQuery({
    queryKey: ["entity", versionId, scenarioId, entityId],
    enabled: !!versionId && !!entityId,
    queryFn: () =>
      getJson<EntityDetail>(
        scenarioId ? `${V1}/scenarios/${scenarioId}/entities/${entityId}` : `${V1}/versions/${versionId}/entities/${entityId}`,
      ),
  });
}

export function usePrior(versionId: string | null, entityId: string | null, scenarioId: string | null, pathway: string | null) {
  return useQuery({
    queryKey: ["prior", versionId, scenarioId, entityId, pathway],
    enabled: !!versionId && !!entityId,
    queryFn: () =>
      getJson<PriorLearning>(
        (scenarioId ? `${V1}/scenarios/${scenarioId}` : `${V1}/versions/${versionId}`) +
          `/entities/${entityId}/prior-learning${qs({ pathway })}`,
      ),
  });
}

export function useDownstream(versionId: string | null, entityId: string | null, scenarioId: string | null) {
  return useQuery({
    queryKey: ["downstream", versionId, scenarioId, entityId],
    enabled: !!versionId && !!entityId,
    queryFn: () =>
      getJson<Downstream>(
        (scenarioId ? `${V1}/scenarios/${scenarioId}` : `${V1}/versions/${versionId}`) + `/entities/${entityId}/downstream`,
      ),
  });
}

export function useRelationship(versionId: string | null, key: string | null) {
  return useQuery({
    queryKey: ["relationship", versionId, key],
    enabled: !!versionId && !!key,
    queryFn: () => getJson<RelationshipDetail>(`${V1}/versions/${versionId}/relationships/${key}`),
  });
}

export function useSpan(spanId: string | null) {
  return useQuery({
    queryKey: ["span", spanId],
    enabled: !!spanId,
    staleTime: Infinity,
    queryFn: async () => unwrap(await api.GET("/api/v1/evidence/{span_id}", { params: { path: { span_id: spanId! } } })),
  });
}

export function useSearch(versionId: string | null, q: string) {
  return useQuery({
    queryKey: ["search", versionId, q],
    enabled: !!versionId && q.trim().length >= 2,
    queryFn: () =>
      getJson<{
        entities: { id: string; type: string; key: string; title: string }[];
        documents: { chunk_id: string; document_version_id: string; page: number; section: string; headline: string }[];
        retrieval_mode: string;
      }>(`${V1}/versions/${versionId}/search${qs({ q })}`),
  });
}

export function useTable(versionId: string | null) {
  return useQuery({
    queryKey: ["table", versionId],
    enabled: !!versionId,
    queryFn: () =>
      getJson<{
        rows: {
          id: string;
          key: string;
          title: string;
          credits: number | null;
          year: number | null;
          term: string | null;
          classification: string | null;
          pathway: string | null;
          requires: { id: string; key: string; type: string; basis: string; review_state: string }[];
          supports: { id: string; key: string; type: string; basis: string; review_state: string }[];
          outcome_count: number;
          evidence_fields: string[];
        }[];
      }>(`${V1}/versions/${versionId}/table`),
  });
}

export function useDocuments() {
  return useQuery({ queryKey: ["documents"], queryFn: async () => unwrap(await api.GET("/api/v1/documents")) });
}

export function useVersionSources(versionId: string | null) {
  return useQuery({
    queryKey: ["sources", versionId],
    enabled: !!versionId,
    queryFn: () =>
      getJson<
        {
          source: components["schemas"]["SourceOut"];
          document_version: DocumentVersionOut;
          document: { id: string; title: string; document_type: string; source_url: string | null };
        }[]
      >(`${V1}/versions/${versionId}/sources`),
  });
}

export function useJobs(activeOnly = false) {
  return useQuery({
    queryKey: ["jobs", activeOnly],
    refetchInterval: 4000,
    queryFn: async () =>
      unwrap(await api.GET("/api/v1/jobs", { params: { query: activeOnly ? { status: "running" } : {} } })),
  });
}

export function useReviews(versionId: string | null, status: string[] = ["open", "deferred"]) {
  return useQuery({
    queryKey: ["reviews", versionId, status],
    queryFn: async () =>
      unwrap(
        await api.GET("/api/v1/reviews", {
          params: { query: { status, curriculum_version_id: versionId ?? undefined, limit: 200 } },
        }),
      ),
  });
}

export function useDecideReview() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { id: string; decision: "accept" | "reject" | "defer" | "edit"; choice?: "existing" | "proposed"; rationale?: string; edited_payload?: Record<string, unknown> }) =>
      unwrap(
        await api.POST("/api/v1/reviews/{item_id}/decision", {
          params: { path: { item_id: v.id } },
          body: { decision: v.decision, choice: v.choice ?? null, rationale: v.rationale ?? null, edited_payload: v.edited_payload ?? null },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["reviews"] });
      qc.invalidateQueries({ queryKey: ["graph"] });
      qc.invalidateQueries({ queryKey: ["entity"] });
      qc.invalidateQueries({ queryKey: ["table"] });
    },
  });
}
