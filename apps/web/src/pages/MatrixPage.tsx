import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { getJson, qs } from "../api/client";
import { useUi } from "../store/ui";

interface Cell { state: "documented" | "inferred" | "none_found" | "insufficient" | "explicitly_absent"; levels: string[]; assessed: boolean }
interface Matrix {
  pathway_assumption: string;
  columns: { plo: { id: string; key: string; title: string; statement: string | null }; documented_courses: string[]; inferred_courses: string[]; levels: string[]; assessed: boolean; required_path: boolean }[];
  rows: { course: { id: string; key: string; title: string }; year: number | null; term: string | null; exposure: string; has_documented_outcomes: boolean; cells: Record<string, Cell> }[];
  coverage: { value: number | null; numerator: number; denominator: number; definition: string; assessed_value: number | null; assessed_definition: string };
  documentation: { courses_with_outcomes: number; courses_total: number; unknowns_treatment: string };
}
interface Issue { rule_id: string; severity: string; consequence_class: string; evidence_basis: string; title: string; explanation: string; affected_entity_ids: string[]; assumptions: string[] }
interface Alignment { rows: { assessment: { id: string; key: string; title: string; format?: string; weight_percent?: number | null; timing_week?: number | null }; course: { id: string; key: string }; year: number | null; term: string | null; outcomes: { outcome: { id: string; key: string; title: string }; documented: boolean; program_outcomes: { key: string; level: string | null; documented: boolean }[] }[] }[] }

const LVL: Record<string, string> = { introduce: "I", reinforce: "R", assess: "A" };
const STATE_TEXT: Record<string, string> = { documented: "Documented", inferred: "Inferred", none_found: "—", insufficient: "?", explicitly_absent: "Absent" };

export function MatrixPage() {
  const { versionId, scenarioId, filters, select } = useUi();
  const [tab, setTab] = useState<"matrix" | "assessments" | "issues">("matrix");
  const pathway = filters.pathway;
  const base = scenarioId ? `/api/v1/scenarios/${scenarioId}` : `/api/v1/versions/${versionId}`;
  const matrix = useQuery({ queryKey: ["matrix", versionId, scenarioId, pathway], enabled: !!versionId, queryFn: () => getJson<Matrix>(`${base}/outcome-matrix${qs({ pathway })}`) });
  const align = useQuery({ queryKey: ["alignment", versionId], enabled: !!versionId && tab === "assessments", queryFn: () => getJson<Alignment>(`/api/v1/versions/${versionId}/assessment-alignment`) });
  const issues = useQuery({ queryKey: ["issues", versionId, pathway], enabled: !!versionId && tab === "issues", queryFn: () => getJson<{ issues: Issue[]; note: string }>(`/api/v1/versions/${versionId}/issues${qs({ pathway })}`) });
  const m = matrix.data;
  return (
    <div className="page">
      <div className="row wrap">
        <h2 className="grow">Program outcomes {scenarioId && <span className="badge modified">scenario projection</span>}</h2>
        <div role="tablist" className="row">
          {(["matrix", "assessments", "issues"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} className={tab === t ? "primary" : ""} onClick={() => setTab(t)}>
              {t === "matrix" ? "Course × outcome matrix" : t === "assessments" ? "Assessment alignment" : "Potential issues"}
            </button>
          ))}
        </div>
      </div>
      {tab === "matrix" && m && (
        <>
          <div className="row wrap" style={{ margin: "8px 0", alignItems: "stretch" }}>
            <div className="card" style={{ maxWidth: 520 }}>
              <strong>Documented coverage: {m.coverage.numerator}/{m.coverage.denominator} program outcomes</strong>
              <p className="small muted">{m.coverage.definition}</p>
              <strong>With a documented assessment: {m.coverage.assessed_value != null ? Math.round(m.coverage.assessed_value * m.coverage.denominator) : "?"}/{m.coverage.denominator}</strong>
              <p className="small muted">{m.coverage.assessed_definition}</p>
            </div>
            <div className="card" style={{ maxWidth: 420 }}>
              <strong>Documentation completeness: {m.documentation.courses_with_outcomes}/{m.documentation.courses_total} courses have documented outcomes</strong>
              <p className="small muted">{m.documentation.unknowns_treatment}</p>
              <p className="small">Pathway assumption: <strong>{m.pathway_assumption}</strong></p>
            </div>
            <div className="card" style={{ flex: 1, minWidth: 320, height: 190 }}>
              <ResponsiveContainer>
                <BarChart data={m.columns.map((c) => ({ plo: c.plo.key, documented: c.documented_courses.length, inferred: c.inferred_courses.length }))}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="plo" fontSize={11} />
                  <YAxis allowDecimals={false} fontSize={11} label={{ value: "courses", angle: -90, position: "insideLeft", fontSize: 11 }} />
                  <Tooltip />
                  <Legend />
                  <Bar dataKey="documented" stackId="a" fill="#2f7d4f" name="Documented contributions" />
                  <Bar dataKey="inferred" stackId="a" fill="#6b5ca5" name="Inferred only" />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
          <p className="small muted">Cell legend: I/R/A = introduced / reinforced / assessed, shown only where stated. ✓ = documented assessment aligned. “—” = no contribution found in documented outcomes. “?” = insufficient documentation (course has no documented outcomes).</p>
          <div style={{ overflow: "auto" }}>
            <table className="data matrix" aria-label="Course by program outcome matrix">
              <thead>
                <tr>
                  <th>Course</th><th>Year/term</th><th>Exposure</th>
                  {m.columns.map((c) => <th key={c.plo.id} title={c.plo.statement ?? c.plo.title}>{c.plo.key}</th>)}
                </tr>
              </thead>
              <tbody>
                {m.rows.map((r) => (
                  <tr key={r.course.id}>
                    <td style={{ textAlign: "left" }}><button className="link" onClick={() => select({ kind: "node", id: r.course.id })}>{r.course.key}</button> <span className="muted small">{r.course.title}</span></td>
                    <td>{r.year ?? "?"} {r.term ?? ""}</td>
                    <td className="small">{r.exposure.replace("_", " ")}</td>
                    {m.columns.map((c) => {
                      const cell = r.cells[c.plo.id];
                      const cls = cell.state === "documented" ? "cell-documented" : cell.state === "inferred" ? "cell-inferred" : cell.state === "insufficient" ? "cell-insufficient" : "";
                      return (
                        <td key={c.plo.id} className={cls} title={`${STATE_TEXT[cell.state]}${cell.levels.length ? ` (${cell.levels.join(", ")})` : ""}${cell.assessed ? ", assessed" : ""}`}>
                          <span className="sr-only">{STATE_TEXT[cell.state]}</span>
                          {cell.levels.map((l) => LVL[l]).join("") || (cell.state === "documented" || cell.state === "inferred" ? "•" : STATE_TEXT[cell.state])}
                          {cell.assessed ? " ✓" : ""}
                          {cell.state === "inferred" ? " (inf.)" : ""}
                        </td>
                      );
                    })}
                  </tr>
                ))}
                <tr>
                  <th colSpan={3} style={{ textAlign: "left" }}>Documented assessment of outcome</th>
                  {m.columns.map((c) => <td key={c.plo.id}>{c.assessed ? "yes" : <span className="error">none found</span>}</td>)}
                </tr>
                <tr>
                  <th colSpan={3} style={{ textAlign: "left" }}>Required-path coverage</th>
                  {m.columns.map((c) => <td key={c.plo.id}>{c.required_path ? "yes" : <span className="error">no</span>}</td>)}
                </tr>
              </tbody>
            </table>
          </div>
        </>
      )}
      {tab === "assessments" && align.data && (
        <table className="data" aria-label="Assessment by outcome">
          <thead><tr><th>Course</th><th>Assessment</th><th>Format</th><th>Weight</th><th>Week</th><th>Assesses (course outcomes)</th><th>Program outcomes reached</th></tr></thead>
          <tbody>
            {align.data.rows.map((r) => (
              <tr key={r.assessment.id}>
                <td className="mono">{r.course.key}</td>
                <td>{r.assessment.title}</td>
                <td>{r.assessment.format ?? "?"}</td>
                <td>{r.assessment.weight_percent != null ? `${r.assessment.weight_percent}%` : "not documented"}</td>
                <td>{r.assessment.timing_week ?? "not documented"}</td>
                <td>{r.outcomes.map((o) => `${o.outcome.key}${o.documented ? "" : " (inferred)"}`).join(", ") || <span className="error">none documented</span>}</td>
                <td>{[...new Set(r.outcomes.flatMap((o) => o.program_outcomes.map((p) => `${p.key}${p.level ? `:${LVL[p.level]}` : ""}`)))].join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {tab === "issues" && issues.data && (
        <div>
          <p className="info small">{issues.data.note}</p>
          {issues.data.issues.map((i, n) => (
            <div key={n} className="card">
              <div className="row wrap">
                <span className={`badge ${i.severity === "high" ? "rejected" : i.severity === "medium" ? "proposed" : ""}`}>{i.severity}</span>
                <span className={`badge ${i.evidence_basis === "explicit_statement" ? "documented" : "inferred"}`}>{i.evidence_basis === "explicit_statement" ? "Documented basis" : "Inferred basis"}</span>
                <strong className="grow">{i.title}</strong>
              </div>
              <p className="small">{i.explanation}</p>
              {i.affected_entity_ids.length > 0 && (
                <button className="small" onClick={() => useUi.getState().setHighlight({ mode: "finding", nodes: i.affected_entity_ids, edges: [], label: i.title })}>Highlight on map</button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
