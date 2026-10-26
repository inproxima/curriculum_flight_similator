import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { ApiError, getJson } from "./client";
import type { components } from "./schema";

export type OutlinePlan = components["schemas"]["OutlinePlan"];
export type PlanItem = components["schemas"]["PlanItem"];
export type PlanSummary = components["schemas"]["PlanSummary"];
export type MissingPiece = components["schemas"]["MissingPiece"];
export type OutlineImport = components["schemas"]["OutlineImportOut"];
export type OutlineImportBrief = components["schemas"]["OutlineImportBrief"];
export type OutlineImportCreated = components["schemas"]["OutlineImportCreated"];
export type CourseMissing = components["schemas"]["CourseMissingOut"];
export type LinkEdit = { strength?: number | null; level?: "introduce" | "reinforce" | "assess" | "" | null };

const V1 = "/api/v1";
const postJson = <T,>(url: string, body?: unknown, headers: Record<string, string> = {}) =>
  getJson<T>(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...headers },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

/** Everything an applied or reverted import can change on screen. */
export function invalidateCurriculum(qc: QueryClient) {
  for (const k of ["web", "graph", "entity", "reviews", "table", "documents", "sources", "missing", "outline-imports", "outline-import", "jobs"]) {
    qc.invalidateQueries({ queryKey: [k] });
  }
}

export function useMe() {
  return useQuery({
    queryKey: ["me"],
    staleTime: 60_000,
    queryFn: () => getJson<{ display_name: string; email: string; role: string; auth_mode: string }>(`${V1}/me`),
  });
}

/** What the program lacks for one course (content, unknowns, alignment gaps of its elements). */
export function useCourseMissing(versionId: string | null, courseId: string | null, enabled = true) {
  return useQuery({
    queryKey: ["missing", versionId, courseId],
    enabled: !!versionId && !!courseId && enabled,
    queryFn: () => getJson<CourseMissing>(`${V1}/versions/${versionId}/courses/${courseId}/missing`),
  });
}

export function useOutlineImports(versionId: string | null, courseId: string | null) {
  return useQuery({
    queryKey: ["outline-imports", versionId, courseId],
    enabled: !!versionId && !!courseId,
    queryFn: () => getJson<OutlineImportBrief[]>(`${V1}/versions/${versionId}/courses/${courseId}/outline-imports`),
  });
}

export function useOutlineImport(id: string | null) {
  return useQuery({
    queryKey: ["outline-import", id],
    enabled: !!id,
    queryFn: () => getJson<OutlineImport>(`${V1}/outline-imports/${id}`),
  });
}

export function useOutlineMutations() {
  const qc = useQueryClient();
  const refreshImport = (id: string) => {
    qc.invalidateQueries({ queryKey: ["outline-import", id] });
    qc.invalidateQueries({ queryKey: ["outline-imports"] });
  };
  return {
    create: useMutation({
      mutationFn: (v: { versionId: string; courseId: string; form: FormData }) =>
        getJson<OutlineImportCreated>(`${V1}/versions/${v.versionId}/courses/${v.courseId}/outline-imports`, { method: "POST", body: v.form }),
      onSuccess: () => {
        qc.invalidateQueries({ queryKey: ["outline-imports"] });
        qc.invalidateQueries({ queryKey: ["documents"] });
      },
    }),
    preview: useMutation({
      mutationFn: (v: { id: string; selected: string[]; edits: Record<string, LinkEdit> }) =>
        postJson<PlanSummary>(`${V1}/outline-imports/${v.id}/preview`, { selected_item_ids: v.selected, edits: v.edits }),
    }),
    apply: useMutation({
      mutationFn: (v: { id: string; etag: string; selected: string[]; edits: Record<string, LinkEdit>; mapAfter?: boolean }) =>
        postJson<OutlineImport>(
          `${V1}/outline-imports/${v.id}/apply`,
          { selected_item_ids: v.selected, edits: v.edits, map_after: !!v.mapAfter },
          { "If-Match": `"${v.etag}"` },
        ),
      onSuccess: (r) => {
        qc.setQueryData(["outline-import", r.id], r);
        invalidateCurriculum(qc);
        qc.invalidateQueries({ queryKey: ["ai-jobs"] }); // the "map this course" job, if one was started
      },
    }),
    revert: useMutation({
      mutationFn: (id: string) => postJson<OutlineImport>(`${V1}/outline-imports/${id}/revert`),
      onSuccess: (r) => {
        qc.setQueryData(["outline-import", r.id], r);
        invalidateCurriculum(qc);
      },
    }),
    rerun: useMutation({
      mutationFn: (v: { id: string; academic_year?: string | null }) =>
        postJson<{ job_id: string; import_id: string }>(`${V1}/outline-imports/${v.id}/rerun`, v.academic_year ? { academic_year: v.academic_year } : {}),
      onSuccess: (_r, v) => refreshImport(v.id),
    }),
    discard: useMutation({
      mutationFn: (id: string) => postJson<OutlineImportBrief>(`${V1}/outline-imports/${id}/discard`),
      onSuccess: (_r, id) => refreshImport(id),
    }),
  };
}

export function errorText(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  return e instanceof Error ? e.message : String(e);
}

export function errorCode(e: unknown): string | null {
  return e instanceof ApiError ? e.code : null;
}
