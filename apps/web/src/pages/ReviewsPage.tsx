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
      <p className="muted">Accepting materializes the proposal into a draft version only. Published versions are immutable; use a scenario or a new draft.</p>
      {isLoading && <p className="muted">Loading…</p>}
      {data && data.total === 0 && <p className="muted">Nothing to review.</p>}
      {(data?.items as ReviewItem[] | undefined)?.map((it) => <ReviewCard key={it.id} item={it} />)}
    </div>
  );
}

function ReviewCard({ item }: { item: ReviewItem }) {
  const decide = useDecideReview();
  const [err, setErr] = useState<string | null>(null);
  const [year, setYear] = useState("");
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
        <span className={`badge ${item.status}`}>{item.status}</span>
      </div>
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
