import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { getJson, qs } from "./client";

const V1 = "/api/v1";
const post = <T,>(url: string, body?: unknown) =>
  getJson<T>(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: body === undefined ? undefined : JSON.stringify(body) });

export interface RouteStatus {
  name: string;
  provider: string;
  model: string;
  purpose: string;
  fallback: [string, string] | null;
  available: boolean;
  active: [string, string] | null;
  unavailable_reasons: string[];
}
export interface AiStatus {
  implemented: boolean;
  providers: Record<string, boolean>;
  allowlist: Record<string, boolean>;
  routes: RouteStatus[];
  retrieval_mode: string;
  message: string;
  limits: { monthly_budget_usd: number; max_cost_per_job_usd: number };
  spend: { month_usd: number };
}

export interface Citation { span_id: string; quote: string; page: number; document: string; synthetic: boolean }
export interface Proposal { change: Record<string, unknown> & { op: string }; summary: string; model_summary: string | null; rationale: string | null; assumptions: string[] }
export interface Envelope {
  answer: string;
  insufficient_documentation: boolean;
  citations: Citation[];
  highlighted_entities: { id: string; key: string }[];
  highlighted_relationship_keys: string[];
  findings: { title: string; explanation: string; basis: "source_supported" | "graph_or_rule_derived" | "ai_interpretation" }[];
  assumptions: string[];
  unanswered_questions: string[];
  proposed_changes: Proposal[];
  validation: { dropped_citations: unknown[]; dropped_entities: string[]; dropped_relationships: string[]; dropped_proposals: { summary: string; reason: string }[]; warnings: string[] };
  meta: { mode: string; route: string; provider: string; model: string; cost_usd: number | null; input_tokens: number; output_tokens: number; tool_calls: string[]; fallback_from: string | null; label: string; model_run_id: string };
  error?: { code: string; message: string };
}
export interface Critique {
  critique_of: string;
  overall: "no_concerns" | "minor_concerns" | "major_concerns";
  concerns: { proposal_index: number | null; concern: string; severity: string; suggestion: string }[];
  questions_for_faculty: string[];
  meta: { model: string; provider: string; cost_usd: number | null; label: string };
}
export interface ChatMessage { id: string; role: "user" | "assistant" | "critique"; content: Record<string, unknown>; created_at: string }

export function useAiStatus() {
  return useQuery({ queryKey: ["ai-status"], queryFn: () => getJson<AiStatus>(`${V1}/ai/status`), staleTime: 30_000 });
}

export function useAiUsage() {
  return useQuery({
    queryKey: ["ai-usage"],
    refetchInterval: 10_000,
    queryFn: () =>
      getJson<{
        by_route: { route: string; provider: string; model: string; status: string; runs: number; input_tokens: number; output_tokens: number; cost_usd: number }[];
        recent: { id: string; route: string; purpose: string; provider: string; requested_model: string; returned_model: string | null; status: string; input_tokens: number | null; output_tokens: number | null; cost_usd: number | null; duration_ms: number | null; fallback_from: string | null; error: string | null; prompt_version: string; evidence_ids: number; created_at: string }[];
      }>(`${V1}/ai/usage`),
  });
}

export function useConversations(versionId: string | null) {
  return useQuery({
    queryKey: ["conversations", versionId],
    enabled: !!versionId,
    queryFn: () => getJson<{ id: string; scenario_id: string | null; title: string | null; created_at: string }[]>(`${V1}/conversations${qs({ curriculum_version_id: versionId })}`),
  });
}

export function useMessages(conversationId: string | null) {
  return useQuery({
    queryKey: ["messages", conversationId],
    enabled: !!conversationId,
    queryFn: () => getJson<{ messages: ChatMessage[] }>(`${V1}/conversations/${conversationId}/messages`),
  });
}

export function useAiMutations() {
  const qc = useQueryClient();
  return {
    createConversation: useMutation({
      mutationFn: (b: { curriculum_version_id: string; scenario_id: string | null }) => post<{ id: string }>(`${V1}/conversations`, b),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["conversations"] }),
    }),
    send: useMutation({
      mutationFn: (v: { conversationId: string; text: string; mode: string }) =>
        post<{ message_id: string; job_id: string }>(`${V1}/conversations/${v.conversationId}/messages`, { text: v.text, mode: v.mode }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["messages"] }),
    }),
    critique: useMutation({ mutationFn: (messageId: string) => post<{ job_id: string }>(`${V1}/messages/${messageId}/critique`) }),
    extract: useMutation({ mutationFn: (dvId: string) => post<{ job_id: string }>(`${V1}/document-versions/${dvId}/ai-extract`) }),
    mappings: useMutation({ mutationFn: (versionId: string) => post<{ job_id: string }>(`${V1}/versions/${versionId}/ai-mappings`) }),
    policy: useMutation({
      mutationFn: (v: { dvId: string; providers: string[] | null }) =>
        getJson(`${V1}/document-versions/${v.dvId}/ai-policy`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ai_providers: v.providers }) }),
      onSuccess: () => qc.invalidateQueries({ queryKey: ["documents"] }),
    }),
    explain: useMutation({
      mutationFn: (runId: string) =>
        post<{ summary: string; key_points: { finding_ids: string[]; point: string }[]; judgment_calls: string[]; missing_information: string[]; stale: boolean; meta: { model: string; label: string } }>(`${V1}/analyses/${runId}/explain`),
    }),
  };
}

/** Wait for a durable job via SSE; resolves with the final job status. Survives reconnects (server replays events). */
export function waitForJob(jobId: string, onEvent?: (msg: string, status: string) => void): Promise<string> {
  return new Promise((resolve) => {
    const es = new EventSource(`${V1}/jobs/${jobId}/stream`);
    let status = "queued";
    es.addEventListener("job", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      status = d.job?.status ?? status;
      onEvent?.(d.message, d.status);
    });
    es.addEventListener("end", () => {
      es.close();
      resolve(status);
    });
  });
}
