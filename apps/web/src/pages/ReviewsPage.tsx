import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useDecideReview, useReviews, type ReviewItem } from "../api/hooks";
import { ApiError } from "../api/client";
import { EvidenceChip } from "../components/Evidence";
import { useUi } from "../store/ui";

const KIND_LABEL: Record<string, string> = {
  candidate_entity: "New entity from source",
  candidate_relationship: "New requisite from source",
  inferred_mapping: "Inferred relationship",
  conflicting_requirement: "Conflict between sources",
  possible_duplicate: "Possible duplicate",
  uncertain_course_match: "Uncertain course code",
  missing_academic_year: "Missing academic year",
  unreadable_page: "Unreadable page",
  weak_evidence: "Weak evidence",
};

export function ReviewsPage() {
  const versionId = useUi((s) => s.versionId);
  const [scope, setScope] = useState<"version" | "all">("version");
  const [status, setStatus] = useState<string[]>(["open", "deferred"]);
  const { data, isLoading } = useReviews(scope === "version" ? versionId : null, status);
  const [kind, setKind] = useState<string>("");
  const [selected, setSelected] = useState<string[]>([]);
  const [bulkMsg, setBulkMsg] = useState<string | null>(null);
  const qc = useQueryClient();
  const items = ((data?.items as ReviewItem[] | undefined) ?? []).filter((i) => !kind || i.kind === kind);
  const bulk = async (decision: "accept" | "reject" | "defer") => {
    const r = await fetch("/api/v1/reviews/bulk", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: selected, decision, rationale: "Bulk decision in review inbox" }) }).then((x) => x.json());
    setBulkMsg(`${r.ok} applied, ${r.failed} failed${r.failed ? ": " + r.results.filter((x: { ok: boolean }) => !x.ok).map((x: { error: string }) => x.error).join("; ") : ""}`);
    setSelected([]);
    qc.invalidateQueries({ queryKey: ["reviews"] });
    qc.invalidateQueries({ queryKey: ["graph"] });
  };
  return (
    <div className="page">
      <div className="row">
        <h2 className="grow">Review inbox</h2>
        <select aria-label="Scope" value={scope} onChange={(e) => setScope(e.target.value as "version" | "all")}>
          <option value="version">Selected curriculum version</option>
          <option value="all">All versions and documents</option>
        </select>
        <select aria-label="Status" value={status.join(",")} onChange={(e) => setStatus(e.target.value.split(","))}>
          <option value="open,deferred">Open and deferred</option>
          <option value="accepted,edited,rejected">Decided</option>
        </select>
      </div>
      <p className="muted">Accepting materializes the proposal into a draft version only. Published versions are immutable; use a scenario or a new draft. Items marked “AI-proposed” come from a model; their quotes were verified against the source, but the interpretation is not.</p>
      <div className="row wrap" style={{ margin: "8px 0" }}>
        <select aria-label="Filter by kind" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="">All kinds ({data?.items.length ?? 0})</option>
          {Object.entries(KIND_LABEL).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        <label className="row small"><input type="checkbox" checked={items.length > 0 && selected.length === items.length}
          onChange={(e) => setSelected(e.target.checked ? items.map((i) => i.id) : [])} /> Select all shown</label>
        <button className="small" disabled={!selected.length} onClick={() => bulk("accept")}>Accept selected ({selected.length})</button>
        <button className="small" disabled={!selected.length} onClick={() => bulk("reject")}>Reject selected</button>
        <button className="small" disabled={!selected.length} onClick={() => bulk("defer")}>Defer selected</button>
        {bulkMsg && <span className="small">{bulkMsg}</span>}
      </div>
      {isLoading && <p className="muted">Loading…</p>}
      {data && data.total === 0 && <p className="muted">Nothing to review.</p>}
      {items.map((it) => (
        <div key={it.id} className="row" style={{ alignItems: "flex-start" }}>
          <input type="checkbox" aria-label={`Select ${it.title}`} style={{ marginTop: 14 }} checked={selected.includes(it.id)}
            onChange={(e) => setSelected(e.target.checked ? [...selected, it.id] : selected.filter((x) => x !== it.id))} />
          <div className="grow"><ReviewCard item={it} /></div>
        </div>
      ))}
    </div>
  );
}

function ReviewCard({ item }: { item: ReviewItem }) {
  const decide = useDecideReview();
  const [err, setErr] = useState<string | null>(null);
  const [year, setYear] = useState("");
  const [srcEdit, setSrcEdit] = useState("");
  const p = item.payload as Record<string, unknown>;
  const run = (decision: "accept" | "reject" | "defer" | "edit", extra: { choice?: "existing" | "proposed"; edited_payload?: Record<string, unknown> } = {}) => {
    setErr(null);
    decide.mutate({ id: item.id, decision, ...extra }, { onError: (e) => setErr(e instanceof ApiError ? e.message : String(e)) });
  };
  const decided = !["open", "deferred"].includes(item.status);
  const isConflict = item.kind === "conflicting_requirement";
  return (
    <div className="card" aria-label={item.title}>
      <div className="row wrap">
        <span className="badge">{KIND_LABEL[item.kind] ?? item.kind}</span>
        <strong className="grow">{item.title}</strong>
        {p.origin === "model" && <span className="badge inferred" title="Proposed by a model; quote verified against the source">AI-proposed</span>}
        {Array.isArray(p.sources) && (p.sources as unknown[]).length > 1 && <span className="badge">{(p.sources as unknown[]).length} sources</span>}
        <span className={`badge ${item.status}`}>{item.status}</span>
      </div>
      {Array.isArray(p.field_conflicts) && (p.field_conflicts as { field: string; kept: unknown; other: unknown; other_authority: string }[]).length > 0 && (
        <div className="notice small">
          Sources disagree:{" "}
          {(p.field_conflicts as { field: string; kept: unknown; other: unknown; other_authority: string }[]).map((c, i) => (
            <span key={i}><strong>{c.field}</strong>: kept “{String(c.kept)}” (higher-authority source), other source ({c.other_authority}) says “{String(c.other)}”. </span>
          ))}
        </div>
      )}
      {!!p.fields && typeof p.fields === "object" && (
        <div className="small muted">Documented fields: {Object.entries(p.fields as Record<string, { value: unknown }>).filter(([k]) => k !== "description").map(([k, v]) => `${k} = ${String(v.value)}`).join(" · ") || "none"}{(p.fields as Record<string, unknown>).description ? " · description" : ""}</div>
      )}
      {p.proposal === "inferred_preparation" && (
        <div className="small">Quote: “{String(p.quote)}”<br />Proposed earlier courses: <strong>{(p.sources as string[]).join(", ") || "none named"}</strong>
          <span className="row" style={{ marginTop: 4 }}><input type="text" aria-label="Earlier courses" placeholder="Edit: e.g. BIOL 211, BIOL 213" value={srcEdit} onChange={(e) => setSrcEdit(e.target.value)} />
            <button className="small" disabled={!srcEdit.trim()} onClick={() => run("edit", { edited_payload: { sources: srcEdit.split(",").map((x) => x.trim().toUpperCase()).filter(Boolean) } })}>Accept with these courses</button></span>
        </div>
      )}
      {item.detail && <p className="small">{item.detail}</p>}
      {isConflict && p.type === "requirement" && (
        <div className="row wrap small" style={{ gap: 16 }}>
          <div><h4>Currently recorded</h4><span className="mono">{String(p.existing_rendered)}</span><div className="muted">“{String(p.existing_text ?? "")}”</div></div>
          <div><h4>Other source ({String(p.document_authority)})</h4><span className="mono">{String(p.proposed_rendered)}</span><div className="muted">“{String(p.proposed_text ?? "")}”</div></div>
        </div>
      )}
      {isConflict && p.type === "field" && (
        <div className="small">Field <strong>{String(p.field)}</strong>: recorded <strong>{String(p.existing)}</strong>, source says <strong>{String(p.document)}</strong>.</div>
      )}
      {item.kind === "candidate_relationship" && <div className="small mono">{String(p.text)}</div>}
      {item.kind === "uncertain_course_match" && <div className="small">Similar known codes: {(p.similar as string[])?.join(", ") || "none"}</div>}
      <div className="row wrap" style={{ marginTop: 6 }}>
        {item.evidence_span_ids.map((s) => <EvidenceChip key={s} spanId={s} />)}
        {item.evidence_span_ids.length === 0 && item.kind === "inferred_mapping" && <span className="no-evidence">No source statement: interpretation. Accepting keeps it labelled inferred.</span>}
      </div>
      {!decided && (
        <div className="row wrap" style={{ marginTop: 8 }}>
          {isConflict ? (
            <>
              <button onClick={() => run("accept", { choice: "existing" })}>Keep recorded interpretation</button>
              <button onClick={() => run("accept", { choice: "proposed" })}>Apply other source’s statement</button>
            </>
          ) : item.kind === "missing_academic_year" ? (
            <>
              <input type="text" placeholder="e.g. 2018-19" aria-label="Academic year" value={year} onChange={(e) => setYear(e.target.value)} />
              <button disabled={!year} onClick={() => run("edit", { edited_payload: { academic_year: year } })}>Set academic year</button>
            </>
          ) : (
            <button className="primary" onClick={() => run("accept")}>
              {["possible_duplicate", "uncertain_course_match", "unreadable_page", "weak_evidence"].includes(item.kind) ? "Acknowledge" : "Accept"}
            </button>
          )}
          <button onClick={() => run("reject")}>Reject</button>
          <button onClick={() => run("defer")}>Defer</button>
        </div>
      )}
      {err && <p className="error small">{err}</p>}
    </div>
  );
}
