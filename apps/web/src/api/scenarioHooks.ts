import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getJson, qs } from "./client";

const V1 = "/api/v1";

export interface Scenario {
  id: string;
  base_version_id: string;
  title: string;
  description: string | null;
  state: string;
  revision: number;
  head: number;
  change_count: number;
  created_at: string;
  updated_at: string;
  base_version_label?: string;
  base_is_current?: boolean;
  workload_assumptions: Record<string, unknown>;
}

export interface ScenarioChange {
  id: string;
  seq: number;
  op_type: string;
  target_entity_id: string | null;
  payload: Record<string, unknown>;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  assumptions: string[];
  applied: boolean;
  summary: string;
  created_at: string;
}

export interface FindingPath {
  ordinal: number;
  entity_ids: string[];
  labels: string[];
  edges: { key: string; type: string; basis: string; review_state: string }[];
}

export interface Finding {
  id: string;
  ordinal: number;
  rule_id: string;
  category: string;
  severity: "info" | "low" | "medium" | "high";
  consequence_class: "direct_documented" | "indirect_potential" | "uncertain_inferred" | "judgment_needed" | "missing_evidence";
  evidence_basis: string;
  title: string;
  explanation: string;
  affected_entity_ids: string[];
  affected: { id: string; key: string; type: string }[];
  assumptions: string[];
  suggested_actions: string[];
  paths: FindingPath[];
  evidence: { evidence_span_id: string | null; relationship_revision_id: string | null; requirement_rule_id: string | null; note: string | null }[];
}

export interface AnalysisRun {
  id: string;
  scenario_id: string | null;
  curriculum_version_id: string;
  scenario_revision: number | null;
  input_hash: string;
  algorithm_version: string;
  status: string;
  summary: Record<string, unknown> | null;
  started_at: string;
  finished_at: string | null;
  stale: boolean;
  cached?: boolean;
  findings?: Finding[];
}

export function useScenarios(versionId: string | null) {
  return useQuery({
    queryKey: ["scenarios", versionId],
    enabled: !!versionId,
    queryFn: () => getJson<Scenario[]>(`${V1}/scenarios${qs({ base_version_id: versionId })}`),
  });
}

export function useAllScenarios() {
  return useQuery({ queryKey: ["scenarios", "all"], queryFn: () => getJson<Scenario[]>(`${V1}/scenarios`) });
}

export function useScenario(id: string | null) {
  return useQuery({ queryKey: ["scenario", id], enabled: !!id, queryFn: () => getJson<Scenario>(`${V1}/scenarios/${id}`) });
}

export function useScenarioChanges(id: string | null) {
  return useQuery({
    queryKey: ["scenario-changes", id],
    enabled: !!id,
    queryFn: () => getJson<ScenarioChange[]>(`${V1}/scenarios/${id}/changes`),
  });
}

export function useAnalyses(id: string | null) {
  return useQuery({
    queryKey: ["analyses", id],
    enabled: !!id,
    queryFn: () => getJson<AnalysisRun[]>(`${V1}/scenarios/${id}/analyses`),
  });
}

export function useAnalysis(runId: string | null) {
  return useQuery({
    queryKey: ["analysis", runId],
    enabled: !!runId,
    queryFn: () => getJson<AnalysisRun>(`${V1}/analyses/${runId}`),
  });
}

function invalidateScenario(qc: ReturnType<typeof useQueryClient>) {
  for (const k of ["scenario", "scenarios", "scenario-changes", "analyses", "graph", "entity", "prior", "downstream", "compare"])
    qc.invalidateQueries({ queryKey: [k] });
}

async function send<T>(url: string, method: string, body?: unknown, revision?: number): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (revision !== undefined) headers["If-Match"] = String(revision);
  return getJson<T>(url, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
}

export function useScenarioMutations(id: string | null) {
  const qc = useQueryClient();
  const opts = { onSuccess: () => invalidateScenario(qc) };
  return {
    create: useMutation({
      mutationFn: (b: { base_version_id: string; title: string; description?: string }) => send<Scenario>(`${V1}/scenarios`, "POST", b),
      ...opts,
    }),
    addChange: useMutation({
      mutationFn: (v: { revision: number; change: Record<string, unknown> }) =>
        send<Scenario>(`${V1}/scenarios/${id}/changes`, "POST", v.change, v.revision),
      ...opts,
    }),
    undo: useMutation({ mutationFn: (revision: number) => send<Scenario>(`${V1}/scenarios/${id}/undo`, "POST", {}, revision), ...opts }),
    redo: useMutation({ mutationFn: (revision: number) => send<Scenario>(`${V1}/scenarios/${id}/redo`, "POST", {}, revision), ...opts }),
    analyze: useMutation({ mutationFn: () => send<AnalysisRun>(`${V1}/scenarios/${id}/analyses`, "POST", {}), ...opts }),
    update: useMutation({
      mutationFn: (v: { revision: number; body: Record<string, unknown> }) => send<Scenario>(`${V1}/scenarios/${id}`, "PATCH", v.body, v.revision),
      ...opts,
    }),
    rebase: useMutation({
      mutationFn: (v: { revision: number; new_base_version_id: string }) =>
        send<{ scenario: Scenario; conflicts: { seq: number; op_type: string; reason: string }[] }>(
          `${V1}/scenarios/${id}/rebase`, "POST", { new_base_version_id: v.new_base_version_id }, v.revision),
      ...opts,
    }),
    publish: useMutation({
      mutationFn: (v: { revision: number; label: string }) =>
        send<{ version_id: string }>(`${V1}/scenarios/${id}/publish`, "POST", { label: v.label }, v.revision),
      onSuccess: () => {
        invalidateScenario(qc);
        qc.invalidateQueries({ queryKey: ["versions"] });
      },
    }),
  };
}
