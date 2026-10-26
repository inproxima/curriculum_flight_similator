import { useAiStatus } from "../../api/aiHooks";
import { errorText, useCourseMissing, useMe, useOutlineImports, useOutlineMutations, type MissingPiece } from "../../api/outlineHooks";
import type { WebNode, WebView } from "../../api/webHooks";
import { useUi } from "../../store/ui";

export const KIND_TEXT: Record<string, string> = {
  course_no_outcomes: "No learning outcomes",
  course_no_assessments: "No assessments",
  course_no_topics: "No topics",
  course_no_description: "No course description",
  course_no_credits: "Credits not documented",
  course_no_year: "Year of study not documented",
  course_no_term: "Term not documented",
  course_no_prerequisite: "No prerequisite statement",
  clo_no_plo: "Outcome linked to no program outcome",
  clo_unassessed: "Outcome no assessment tests",
  assessment_no_clo: "Assessment linked to no outcome",
  topic_no_clo: "Topic supporting no outcome",
  course_pair_no_link: "Prerequisite with no outcome-level bridge",
  plo_no_clo: "Program outcome no course outcome serves",
  plo_never_assessed: "Program outcome never assessed",
};

const STATUS_BADGE: Record<string, string> = {
  ready: "proposed",
  applied: "documented",
  reverted: "",
  failed: "rejected",
  discarded: "",
  queued: "",
  running: "",
};

const RANK: Record<string, number> = { viewer: 0, reviewer: 1, editor: 2, admin: 3 };

function grouped(items: MissingPiece[]) {
  const g = new Map<string, MissingPiece[]>();
  for (const i of items) g.set(i.kind, [...(g.get(i.kind) ?? []), i]);
  return [...g.entries()];
}

/** Why the import can't start here, or null. */
export function useImportBlocker(view: WebView): string | null {
  const scenarioId = useUi((s) => s.scenarioId);
  const me = useMe();
  const ai = useAiStatus();
  const version = view.curriculum_version as { status?: string };
  if (scenarioId) return "Leave the scenario first: an outline is imported into the draft itself, not into a what-if.";
  if (version.status !== "draft") return "Outlines are imported into draft versions. Select or create a draft version.";
  if (me.data && (RANK[me.data.role] ?? 0) < RANK.editor) return "Importing an outline needs the editor role.";
  const routes = ai.data?.routes ?? [];
  for (const name of ["extract", "synthesize"]) {
    const r = routes.find((x) => x.name === name);
    if (r && !r.available) return `The AI ${name} route is unavailable: ${r.unavailable_reasons.join("; ")}`;
  }
  return null;
}

/** Course inspector card: what the program is missing for this course, and the outline import that fills it. */
export function MissingPiecesCard({ course, view }: { course: WebNode; view: WebView }) {
  const { versionId, setOutlineDialog, select } = useUi();
  const missing = useCourseMissing(versionId, course.id);
  const imports = useOutlineImports(versionId, course.id);
  const m = useOutlineMutations();
  const blocker = useImportBlocker(view);
  const items = missing.data?.items ?? [];
  const fillable = items.filter((i) => i.fillable).length;
  const recent = (imports.data ?? []).filter((i) => i.status !== "discarded").slice(0, 3);
  return (
    <section className="card missing-card" aria-label={`Missing pieces for ${course.key}`}>
      <div className="row wrap">
        <strong className="grow">
          Missing pieces {missing.data ? <span className={`badge ${items.length ? "gap" : "documented"}`}>{items.length}</span> : null}
        </strong>
        <button
          className="primary small"
          disabled={!!blocker}
          title={blocker ?? `Upload or pick ${course.key}'s course outline; an agent proposes what the program needs from it.`}
          onClick={() => setOutlineDialog({ courseId: course.id, importId: null })}
        >
          Import course outline…
        </button>
      </div>
      {blocker && <div className="muted small">{blocker}</div>}
      {missing.data && !items.length && <p className="muted small">Nothing is missing for {course.key} by the program's checks.</p>}
      {items.length > 0 && (
        <details>
          <summary className="small">
            {fillable} of {items.length} can be filled from a course outline
          </summary>
          <ul className="list small">
            {grouped(items).map(([kind, xs]) => (
              <li key={kind}>
                <span className={`badge ${xs[0].fillable ? "proposed" : ""}`}>{xs.length}</span> {KIND_TEXT[kind] ?? kind}
                {xs.length > 1 || xs[0].entity_ids[0] !== course.id ? (
                  <ul className="list">
                    {xs.slice(0, 6).map((x) => (
                      <li key={x.id}>
                        <button className="link small" onClick={() => select({ kind: "node", id: x.entity_ids[0] })}>
                          {x.message}
                        </button>
                      </li>
                    ))}
                    {xs.length > 6 && <li className="muted">…and {xs.length - 6} more</li>}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
        </details>
      )}
      {recent.length > 0 && (
        <div className="small" style={{ marginTop: 6 }}>
          <div className="muted">Outline imports</div>
          <ul className="list">
            {recent.map((i) => (
              <li key={i.id} className="row wrap">
                <span className={`badge ${STATUS_BADGE[i.status] ?? ""}`}>{i.status}</span>
                <span className="grow" title={i.document_title ?? undefined}>
                  {i.outline_year ?? "year ?"} · {new Date(i.applied_at ?? i.created_at).toLocaleDateString()}
                </span>
                <button className="link small" onClick={() => setOutlineDialog({ courseId: course.id, importId: i.id })}>
                  Open
                </button>
                {i.status === "applied" && (
                  <button
                    className="link small"
                    disabled={m.revert.isPending}
                    onClick={() => {
                      if (window.confirm(`Revert this outline import for ${course.key}? The draft goes back to how it was before it was applied.`))
                        m.revert.mutate(i.id, { onError: (e) => window.alert(errorText(e)) });
                    }}
                  >
                    Revert
                  </button>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  );
}
