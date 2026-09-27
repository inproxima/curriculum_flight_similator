import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError, getJson } from "../api/client";
import { useDocuments, useJobs, useVersionSources, useVersions, type DocumentVersionOut, type Job } from "../api/hooks";
import { useUi } from "../store/ui";

const DOC_TYPES = ["program_outline", "review_report", "webpage_extract", "course_outline", "assessment_document", "pasted_text", "other"];
const AUTHORITIES: [string, string][] = [
  ["authoritative", "Authoritative for this version"],
  ["supporting", "Supporting"],
  ["historical_reference", "Historical reference"],
  ["exploratory", "Exploratory"],
];

export function DocumentsPage() {
  const { versionId } = useUi();
  const docs = useDocuments();
  const sources = useVersionSources(versionId);
  const jobs = useJobs();
  const qc = useQueryClient();
  const [lastJob, setLastJob] = useState<string | null>(null);
  return (
    <div className="page" style={{ display: "grid", gridTemplateColumns: "minmax(420px, 1fr) minmax(360px, 1fr)", gap: 16 }}>
      <div>
        <UploadForm onUploaded={(jobId) => { setLastJob(jobId); qc.invalidateQueries({ queryKey: ["documents"] }); }} />
        {lastJob && <JobProgress jobId={lastJob} />}
        <h2 style={{ marginTop: 16 }}>Sources assigned to the selected version</h2>
        {sources.data?.length === 0 && <p className="muted">No sources assigned. Documents are never merged into a version implicitly.</p>}
        {sources.data?.map((s) => (
          <div key={s.source.id} className="card small">
            <div className="row wrap">
              <strong className="grow">{s.document.title}</strong>
              <span className="badge">{s.source.authority.replace("_", " ")}</span>
              {s.source.is_exploratory_cross_version && <span className="badge unknown">exploratory cross-version</span>}
              {s.document_version.is_synthetic && <span className="badge synthetic">SYNTHETIC</span>}
            </div>
            <div className="muted">{s.source.applicability}</div>
            <DatesLine dv={s.document_version} />
          </div>
        ))}
      </div>
      <div>
        <h2>All documents</h2>
        {docs.data?.map((d) => (
          <div key={d.id} className="card small">
            <div className="row wrap"><strong className="grow">{d.title}</strong><span className="badge">{d.document_type.replace("_", " ")}</span></div>
            {d.versions.map((v) => <DocVersionRow key={v.id} dv={v} />)}
          </div>
        ))}
        <h2 style={{ marginTop: 16 }}>Recent jobs</h2>
        <JobList jobs={jobs.data ?? []} />
      </div>
    </div>
  );
}

function DatesLine({ dv }: { dv: DocumentVersionOut }) {
  return (
    <div className="muted">
      Published: {dv.publication_date ?? "unknown"} · Retrieved: {dv.retrieval_date ?? "unknown"} · Academic year: {dv.academic_year ?? <span className="error">not recorded</span>} · Cohort: {dv.cohort_applicability ?? "not recorded"}
    </div>
  );
}

function DocVersionRow({ dv }: { dv: DocumentVersionOut }) {
  const { programId } = useUi();
  const versions = useVersions(programId);
  const qc = useQueryClient();
  const [target, setTarget] = useState("");
  const [authority, setAuthority] = useState("supporting");
  const [applicability, setApplicability] = useState("");
  const [exploratory, setExploratory] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const assign = async () => {
    setMsg(null);
    try {
      await getJson(`/api/v1/document-versions/${dv.id}/sources`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ curriculum_version_id: target, authority, applicability: applicability || null, is_exploratory_cross_version: exploratory }),
      });
      setMsg("Assigned. Candidates are being resolved against that version; see the review inbox.");
      qc.invalidateQueries({ queryKey: ["sources"] });
      qc.invalidateQueries({ queryKey: ["reviews"] });
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    }
  };
  return (
    <div style={{ borderTop: "1px solid var(--line-2)", marginTop: 6, paddingTop: 6 }}>
      <div className="row wrap">
        <span className="mono">{dv.original_filename}</span>
        <span className={`badge ${dv.processing_status === "processed" ? "documented" : dv.processing_status === "failed" ? "rejected" : "proposed"}`}>{dv.processing_status}</span>
        <span className="muted">{dv.page_count ?? "?"} pages · sha256 {dv.sha256.slice(0, 10)}…</span>
        {dv.is_synthetic && <span className="badge synthetic">SYNTHETIC</span>}
      </div>
      <DatesLine dv={dv} />
      <details>
        <summary>Assign to a curriculum version</summary>
        <div className="col" style={{ marginTop: 6 }}>
          <select aria-label="Target curriculum version" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="">Choose version…</option>
            {versions.data?.map((v) => <option key={v.id} value={v.id}>{v.label} ({v.status})</option>)}
          </select>
          <select aria-label="Authority" value={authority} onChange={(e) => setAuthority(e.target.value)}>
            {AUTHORITIES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
          </select>
          <input type="text" placeholder="Applicability (e.g. cohort entering Fall 2025)" value={applicability} onChange={(e) => setApplicability(e.target.value)} />
          <label className="row"><input type="checkbox" checked={exploratory} onChange={(e) => setExploratory(e.target.checked)} /> Exploratory cross-version mapping (visibly labelled)</label>
          <button disabled={!target} onClick={assign}>Assign</button>
          {msg && <p className="small">{msg}</p>}
        </div>
      </details>
    </div>
  );
}

function UploadForm({ onUploaded }: { onUploaded: (jobId: string | null) => void }) {
  const [mode, setMode] = useState<"file" | "paste">("file");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const submit = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    const fd = new FormData(e.currentTarget);
    for (const [k, v] of [...fd.entries()]) if (v === "" || (v instanceof File && !v.name)) fd.delete(k);
    try {
      const r = await getJson<{ duplicate: boolean; job_id: string | null; document_version: { id: string } }>("/api/v1/documents", { method: "POST", body: fd });
      setMsg(r.duplicate ? "This exact file was already uploaded; the existing version is reused (no reprocessing)." : "Uploaded. Processing has started.");
      onUploaded(r.job_id);
      (e.target as HTMLFormElement).reset();
    } catch (err) {
      setMsg(err instanceof ApiError ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <form className="card col" onSubmit={submit} aria-label="Upload document">
      <h2>Import a source document</h2>
      <p className="muted small">PDF (text or scanned), DOCX, plain text, or pasted text. Publication date, retrieval date, academic year, and cohort are separate fields. None are guessed.</p>
      <div className="row">
        <label className="row"><input type="radio" checked={mode === "file"} onChange={() => setMode("file")} /> File</label>
        <label className="row"><input type="radio" checked={mode === "paste"} onChange={() => setMode("paste")} /> Paste text</label>
      </div>
      {mode === "file" ? <input type="file" name="file" accept=".pdf,.docx,.txt,.md" required aria-label="File" /> : <textarea name="pasted_text" rows={6} required aria-label="Pasted text" />}
      <input type="text" name="title" placeholder="Title" aria-label="Title" />
      <label className="row">Type <select name="document_type" defaultValue="other">{DOC_TYPES.map((t) => <option key={t} value={t}>{t.replace("_", " ")}</option>)}</select></label>
      <input type="text" name="source_url" placeholder="Source URL (reference only; not fetched)" aria-label="Source URL" />
      <div className="row wrap">
        <label className="col small">Publication date<input type="date" name="publication_date" /></label>
        <label className="col small">Retrieval date<input type="date" name="retrieval_date" /></label>
        <label className="col small">Academic year<input type="text" name="academic_year" placeholder="2025-26" /></label>
        <label className="col small">Cohort applicability<input type="text" name="cohort_applicability" placeholder="Entering Fall 2025" /></label>
      </div>
      <button className="primary" disabled={busy}>{busy ? "Uploading…" : "Upload"}</button>
      {msg && <p className="small">{msg}</p>}
    </form>
  );
}

export function JobProgress({ jobId }: { jobId: string }) {
  const [events, setEvents] = useState<{ id: number; stage: string | null; status: string; message: string }[]>([]);
  const [job, setJob] = useState<{ status: string; progress: number; stage: string | null } | null>(null);
  const qc = useQueryClient();
  useEffect(() => {
    setEvents([]);
    const es = new EventSource(`/api/v1/jobs/${jobId}/stream`);
    es.addEventListener("job", (ev) => {
      const d = JSON.parse((ev as MessageEvent).data);
      setEvents((xs) => (xs.some((x) => x.id === d.id) ? xs : [...xs, d]));
      setJob(d.job);
    });
    es.addEventListener("end", () => {
      es.close();
      qc.invalidateQueries({ queryKey: ["documents"] });
      qc.invalidateQueries({ queryKey: ["reviews"] });
      qc.invalidateQueries({ queryKey: ["jobs"] });
    });
    return () => es.close();
  }, [jobId, qc]);
  return (
    <div className="card" aria-live="polite">
      <div className="row"><strong className="grow">Processing</strong><span className="badge">{job?.status ?? "queued"}</span></div>
      <div className="progress" role="progressbar" aria-valuenow={Math.round((job?.progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${(job?.progress ?? 0) * 100}%` }} />
      </div>
      <ul className="list small">{events.map((e) => <li key={e.id}><span className="badge">{e.stage ?? "job"}</span> {e.message}</li>)}</ul>
      <div className="row">
        <button className="small" onClick={() => fetch(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" })}>Cancel</button>
      </div>
    </div>
  );
}

function JobList({ jobs }: { jobs: Job[] }) {
  const qc = useQueryClient();
  return (
    <table className="data small">
      <thead><tr><th>Kind</th><th>Status</th><th>Stage</th><th>Updated</th><th></th></tr></thead>
      <tbody>
        {jobs.slice(0, 20).map((j) => (
          <tr key={j.id}>
            <td>{j.kind}</td>
            <td><span className={`badge ${j.status === "succeeded" ? "documented" : j.status === "failed" ? "rejected" : "proposed"}`}>{j.status}</span>{j.error && <div className="error">{j.error}</div>}</td>
            <td>{j.current_stage}</td>
            <td>{new Date(j.updated_at).toLocaleTimeString()}</td>
            <td>{["failed", "cancelled", "partial"].includes(j.status) && (
              <button className="small" onClick={async () => { await fetch(`/api/v1/jobs/${j.id}/retry`, { method: "POST" }); qc.invalidateQueries({ queryKey: ["jobs"] }); }}>Retry</button>
            )}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
