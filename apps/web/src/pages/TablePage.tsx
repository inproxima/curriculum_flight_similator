import { useNavigate } from "react-router-dom";
import { useTable } from "../api/hooks";
import { CLASS_LABEL, TERM_LABEL } from "../lib/graphStyle";
import { useUi } from "../store/ui";

/** Keyboard-accessible alternative to the graph. */
export function TablePage() {
  const { versionId, select, selection } = useUi();
  const { data, isLoading } = useTable(versionId);
  const nav = useNavigate();
  if (isLoading || !data) return <div className="page muted">Loading…</div>;
  return (
    <div className="page">
      <h2>Courses and relationships (table view)</h2>
      <p className="muted">Same data as the map. Press Enter on a row to open it on the map. “Requires” lists formal prerequisites and inferred preparation, labelled.</p>
      <table className="data" aria-label="Courses">
        <thead>
          <tr><th>Code</th><th>Title</th><th>Year</th><th>Term</th><th>Classification</th><th>Credits</th><th>Requires / prepared by</th><th>Supports</th><th>Outcomes</th><th>Sourced fields</th></tr>
        </thead>
        <tbody>
          {data.rows.map((r) => (
            <tr
              key={r.id}
              tabIndex={0}
              aria-selected={selection?.kind === "node" && selection.id === r.id}
              onFocus={() => select({ kind: "node", id: r.id })}
              onKeyDown={(e) => { if (e.key === "Enter") nav("/"); }}
            >
              <td className="mono">{r.key}</td>
              <td>{r.title}</td>
              <td>{r.year ?? "unknown"}</td>
              <td>{TERM_LABEL[r.term ?? "unknown"]}</td>
              <td>{CLASS_LABEL[r.classification ?? "unknown"]}{r.pathway ? ` (${r.pathway})` : ""}</td>
              <td>{r.credits ?? "not documented"}</td>
              <td>{r.requires.map((x) => `${x.key} [${x.type === "formal_prerequisite" ? "formal" : "inferred"}${x.review_state !== "accepted" ? ", " + x.review_state : ""}]`).join("; ") || "—"}</td>
              <td>{r.supports.map((x) => `${x.key} [${x.type === "formal_prerequisite" ? "formal" : "inferred"}]`).join("; ") || "—"}</td>
              <td>{r.outcome_count}</td>
              <td className="small">{r.evidence_fields.join(", ") || "none"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
