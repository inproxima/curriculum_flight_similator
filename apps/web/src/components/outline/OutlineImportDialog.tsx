import * as Dialog from "@radix-ui/react-dialog";
import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useAiStatus } from "../../api/aiHooks";
import { useDocuments } from "../../api/hooks";
import { errorText, useOutlineImport, useOutlineMutations, type OutlineImport } from "../../api/outlineHooks";
import type { WebNode, WebView } from "../../api/webHooks";
import { useUi } from "../../store/ui";
import { JobProgress } from "../JobProgress";
import { KIND_TEXT } from "./MissingPiecesCard";
import { OutlinePlan } from "./OutlinePlan";

type Mode = "file" | "paste" | "existing";

const COUNT_TEXT: Record<string, [string, string]> = {
  add: ["addition", "additions"],
  replace: ["update", "updates"],
  keep: ["new citation", "new citations"],
  retire: ["retirement", "retirements"],
  detach: ["topic detached", "topics detached"],
  set_field: ["course detail", "course details"],
  link: ["link", "links"],
};

function compact(code: string) {
  return code.replace(/\s+/g, "").toUpperCase();
}

function SourceStep({ course, view, onStarted }: { course: WebNode; view: WebView; onStarted: (importId: string, jobId: string | null) => void }) {
  const versionId = useUi((s) => s.versionId);
  const docs = useDocuments();
  const ai = useAiStatus();
  const m = useOutlineMutations();
  const [mode, setMode] = useState<Mode>("file");
  const [err, setErr] = useState<string | null>(null);
  const version = view.curriculum_version as { academic_year?: string | null; label?: string };
  const [year, setYear] = useState(""); // never pre-filled: the year decides what the outline may replace
  const options = useMemo(() => {
    const code = compact(course.key);
    const rows = (docs.data ?? []).flatMap((d) =>
      d.versions.map((v) => ({
        id: v.id,
        label: `${d.title}${v.academic_year ? ` · ${v.academic_year}` : ""}${v.original_filename ? ` · ${v.original_filename}` : ""}`,
        match: compact(d.title).includes(code) || compact(v.original_filename ?? "").includes(code),
        created: v.created_at,
        forbidden: Array.isArray(v.ai_providers) && v.ai_providers.length === 0,
      })),
    );
    return rows.sort((a, b) => Number(b.match) - Number(a.match) || b.created.localeCompare(a.created));
  }, [docs.data, course.key]);
  const route = (name: string) => ai.data?.routes.find((r) => r.name === name);
  const who = [route("extract"), route("synthesize")]
    .filter(Boolean)
    .map((r) => `${r!.active?.join(" · ") ?? r!.model} (${r!.name === "extract" ? "extraction" : "reconciliation"})`)
    .join(" and ");

  const submit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setErr(null);
    const fd = new FormData(e.currentTarget);
    for (const [k, v] of [...fd.entries()]) if (v === "" || (v instanceof File && !v.name)) fd.delete(k);
    if (!versionId) return;
    m.create.mutate(
      { versionId, courseId: course.id, form: fd },
      { onSuccess: (r) => onStarted(r.outline_import.id, r.job_id ?? null), onError: (x) => setErr(errorText(x)) },
    );
  };
  return (
    <form className="col outline-source" onSubmit={submit} aria-label={`Course outline for ${course.key}`}>
      <p className="small">
        The outline is read, its outcomes, assessments and topics are extracted and checked word for word against its text, and an agent
        compares them with what <strong>{version.label ?? "this draft"}</strong> records for <strong>{course.key}</strong>: what is new,
        what a newer outline replaces, and which links would fill the missing pieces. <strong>Nothing changes until you apply the plan.</strong>
      </p>
      <fieldset className="row wrap">
        <legend className="small">Outline</legend>
        <label className="row"><input type="radio" name="mode" checked={mode === "file"} onChange={() => setMode("file")} /> Upload a file</label>
        <label className="row"><input type="radio" name="mode" checked={mode === "paste"} onChange={() => setMode("paste")} /> Paste text</label>
        <label className="row"><input type="radio" name="mode" checked={mode === "existing"} onChange={() => setMode("existing")} /> Use an uploaded document</label>
      </fieldset>
      {mode === "file" && <input type="file" name="file" accept=".pdf,.docx,.txt,.md" required aria-label="Outline file (PDF, DOCX or text)" />}
      {mode === "paste" && <textarea name="pasted_text" rows={8} required aria-label="Outline text" placeholder="Paste the full course outline" />}
      {mode === "existing" && (
        <select name="document_version_id" required aria-label="Uploaded document" defaultValue={options.find((o) => o.match)?.id ?? ""}>
          <option value="" disabled>Choose a document…</option>
          {options.map((o) => (
            <option key={o.id} value={o.id} disabled={o.forbidden}>
              {o.match ? "★ " : ""}
              {o.label}
              {o.forbidden ? " (AI not allowed)" : ""}
            </option>
          ))}
        </select>
      )}
      <div className="row wrap">
        <label className="col small">
          Academic year of the outline
          <input
            type="text"
            name="academic_year"
            value={year}
            onChange={(e) => setYear(e.target.value)}
            placeholder={version.academic_year ? `e.g. ${version.academic_year}` : "e.g. 2026-27"}
          />
        </label>
        <label className="col small">
          Publication date (optional)
          <input type="date" name="publication_date" />
        </label>
        {mode !== "existing" && (
          <label className="col small grow">
            Title (optional)
            <input type="text" name="title" placeholder={`${course.key} course outline`} />
          </label>
        )}
      </div>
      <p className="muted small">
        The academic year decides whether this outline is newer than the program's current content (a newer outline replaces older outcomes,
        assessments and topics). Leave it blank to use the year the outline itself states (for example “Fall 2026”); the plan warns if what
        you enter disagrees with the document.
      </p>
      <p className="muted small">
        Sends the outline text and {course.key}'s current outcomes, assessments and topics to {who || "the configured AI providers"}.
        {ai.data ? ` Per-import cost limit: $${ai.data.limits.max_cost_per_job_usd.toFixed(2)}.` : ""}
      </p>
      {err && <p className="error small">{err}</p>}
      <div className="row">
        <button className="primary" disabled={m.create.isPending}>{m.create.isPending ? "Starting…" : "Read the outline and plan"}</button>
      </div>
    </form>
  );
}

function ResultStep({ imp, course, onClose }: { imp: OutlineImport; course: WebNode; onClose: () => void }) {
  const m = useOutlineMutations();
  const qc = useQueryClient();
  const select = useUi((s) => s.select);
  const [err, setErr] = useState<string | null>(null);
  const r = imp.result;
  const counts = r?.counts ?? {};
  const s = r?.summary as { closes?: string[]; missing_before?: number; missing_after?: number } | undefined;
  const reverted = imp.status === "reverted";
  return (
    <div className="col">
      <div className={reverted ? "notice" : "info"} role="status">
        {reverted ? (
          <>This import was reverted: {course.key} is back to how it was before it was applied.</>
        ) : (
          <>
            Applied to the draft.{" "}
            {Object.entries(counts)
              .map(([k, v]) => {
                const [one, many] = COUNT_TEXT[k] ?? [k, `${k}s`];
                return `${v} ${v === 1 ? one : many}`;
              })
              .join(" · ")}
            {s?.closes ? ` — closed ${s.closes.length} of ${s.missing_before} missing piece(s); ${s.missing_after} remain.` : ""}
          </>
        )}
      </div>
      {!reverted && r && r.missing_after.length > 0 && (
        <details className="small">
          <summary>Still missing ({r.missing_after.length})</summary>
          <ul className="list">
            {r.missing_after.map((g) => (
              <li key={g.id}>{KIND_TEXT[g.kind] ?? g.kind}: {g.message}</li>
            ))}
          </ul>
        </details>
      )}
      {!reverted && r?.map_job_id && (
        <JobProgress
          jobId={r.map_job_id}
          title={`Mapping ${course.key}'s connections (AI, about $${(r.map_estimate_usd ?? 0).toFixed(2)})`}
          onEnd={() => {
            for (const k of ["web", "graph", "missing", "map-preview"]) qc.invalidateQueries({ queryKey: [k] });
          }}
        />
      )}
      {err && <p className="error small">{err}</p>}
      <div className="row">
        <button
          className="primary"
          onClick={() => {
            select({ kind: "node", id: course.id });
            onClose();
          }}
        >
          Show on the web
        </button>
        {!reverted && (
          <button onClick={() => m.revert.mutate(imp.id, { onError: (e) => setErr(errorText(e)) })} disabled={m.revert.isPending}>
            {m.revert.isPending ? "Reverting…" : "Revert this import"}
          </button>
        )}
      </div>
    </div>
  );
}

/** Upload or pick a course outline for one course, watch it being read, review the agent's plan, apply or revert it. */
export function OutlineImportDialog({ view }: { view: WebView }) {
  const dialog = useUi((s) => s.outlineDialog);
  const setDialog = useUi((s) => s.setOutlineDialog);
  const [importId, setImportId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  useEffect(() => {
    setImportId(dialog?.importId ?? null);
    setJobId(null);
  }, [dialog]);
  const imp = useOutlineImport(importId);
  const course = view.nodes.find((n) => n.id === dialog?.courseId);
  const close = () => setDialog(null);
  const status = imp.data?.status;
  const runningJob = jobId ?? ((status === "queued" || status === "running") && imp.data?.job_id ? imp.data.job_id : null);

  let body: React.ReactNode = null;
  if (!course) body = <p className="muted">This course isn't in the current view.</p>;
  else if (!importId) body = <SourceStep course={course} view={view} onStarted={(i, j) => { setImportId(i); setJobId(j); }} />;
  else if (runningJob)
    body = (
      <JobProgress
        jobId={runningJob}
        title={`Reading the outline and planning for ${course.key}`}
        onEnd={() => {
          setJobId(null);
          imp.refetch();
        }}
      />
    );
  else if (imp.isLoading || !imp.data) body = <p className="muted">Loading…</p>;
  else if (status === "ready" && imp.data.plan)
    body = (
      <OutlinePlan
        key={imp.data.etag ?? imp.data.id}
        imp={imp.data}
        onApplied={() => imp.refetch()}
        onRerun={(j) => setJobId(j)}
        onDiscarded={() => setImportId(null)}
      />
    );
  else if (status === "applied" || status === "reverted") body = <ResultStep imp={imp.data} course={course} onClose={close} />;
  else if (status === "failed")
    body = (
      <div className="col">
        <div className="notice" role="alert">
          <strong>The outline couldn't be planned.</strong> {imp.data.error?.message as string}
        </div>
        <div className="row">
          <button onClick={() => setImportId(null)}>Try another outline</button>
        </div>
      </div>
    );
  else if (status === "discarded") body = <SourceStep course={course} view={view} onStarted={(i, j) => { setImportId(i); setJobId(j); }} />;

  return (
    <Dialog.Root open={!!dialog} onOpenChange={(o) => !o && close()}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content
          className="dialog outline-dialog"
          aria-describedby={undefined}
          onPointerDownOutside={(e) => e.preventDefault()}
          onInteractOutside={(e) => e.preventDefault()}
        >
          <header>
            <Dialog.Title asChild>
              <h2 className="grow">Import course outline{course ? ` — ${course.key} ${course.title}` : ""}</h2>
            </Dialog.Title>
            {imp.data && <span className="badge">{imp.data.status}</span>}
            <Dialog.Close asChild>
              <button aria-label="Close outline import">Close</button>
            </Dialog.Close>
          </header>
          <div className="body">{body}</div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
