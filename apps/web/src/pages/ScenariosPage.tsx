import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, getJson, qs } from "../api/client";
import { useVersions } from "../api/hooks";
import {
  useAnalyses,
  useAnalysis,
  useScenario,
  useScenarioChanges,
  useScenarioMutations,
  useScenarios,
  type AnalysisRun,
  type Finding,
} from "../api/scenarioHooks";
import { useAiMutations } from "../api/aiHooks";
import { EvidenceChip } from "../components/Evidence";
import { useUi } from "../store/ui";

const CLASS_TITLE: Record<string, string> = {
  direct_documented: "Direct documented consequences",
  indirect_potential: "Indirect potential consequences",
  uncertain_inferred: "Uncertain (relies on inferred links)",
  judgment_needed: "Needs judgment / recommendations",
  missing_evidence: "Missing evidence: impact cannot be established",
};

export function ScenariosPage() {
  const { versionId, scenarioId, setScenario } = useUi();
  const list = useScenarios(versionId);
  const m = useScenarioMutations(scenarioId);
  const [title, setTitle] = useState("");
  return (
    <div className="page" style={{ display: "grid", gridTemplateColumns: "300px 1fr", gap: 16 }}>
      <div>
        <h2>Scenarios on this version</h2>
        <form className="col card" onSubmit={(e) => { e.preventDefault(); m.create.mutate({ base_version_id: versionId!, title }, { onSuccess: (s) => { setScenario(s.id); setTitle(""); } }); }}>
          <input type="text" aria-label="New scenario title" placeholder="New scenario title" value={title} onChange={(e) => setTitle(e.target.value)} />
          <button className="primary" disabled={!title || !versionId}>Create scenario</button>
        </form>
        <ul className="list">
          {list.data?.map((s) => (
            <li key={s.id}>
              <button className={s.id === scenarioId ? "primary" : ""} style={{ width: "100%", textAlign: "left" }} onClick={() => setScenario(s.id)}>
                {s.title}<br /><span className="small">rev {s.revision} · {s.change_count} changes · {s.state}</span>
              </button>
            </li>
          ))}
        </ul>
        {scenarioId && <button onClick={() => setScenario(null)}>Back to baseline</button>}
      </div>
      <div>{scenarioId ? <ScenarioDetail id={scenarioId} /> : <p className="muted">Select or create a scenario. The published baseline is never modified.</p>}</div>
    </div>
  );
}

function ScenarioDetail({ id }: { id: string }) {
  const s = useScenario(id);
  const changes = useScenarioChanges(id);
  const runs = useAnalyses(id);
  const m = useScenarioMutations(id);
  const nav = useNavigate();
  const [err, setErr] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const latest = runs.data?.[0];
  useEffect(() => setRunId(latest?.id ?? null), [latest?.id]);
  const run = useAnalysis(runId);
  const onErr = (e: unknown) => setErr(e instanceof ApiError ? e.message : String(e));
  if (!s.data) return <p className="muted">Loading…</p>;
  const sc = s.data as typeof s.data & { can_undo: boolean; can_redo: boolean; base_version_status: string };
  return (
    <div className="col">
      <div className="row wrap">
        <h2 className="grow">{sc.title}</h2>
        <span className="badge">rev {sc.revision}</span>
        <span className="badge">base: {sc.base_version_label}</span>
        <span className="badge">{sc.state}</span>
      </div>
      {err && <p className="error">{err}</p>}
      <div className="row wrap">
        <button onClick={() => nav("/")}>Open on map</button>
        <button disabled={!sc.can_undo} onClick={() => m.undo.mutate(sc.revision, { onError: onErr })}>Undo</button>
        <button disabled={!sc.can_redo} onClick={() => m.redo.mutate(sc.revision, { onError: onErr })}>Redo</button>
        <button className="primary" onClick={() => m.analyze.mutate(undefined, { onSuccess: (r) => setRunId(r.id), onError: onErr })}>
          {m.analyze.isPending ? "Analysing…" : "Run deterministic analysis"}
        </button>
        <a href={`/api/v1/scenarios/${id}/export?format=html`} target="_blank" rel="noreferrer"><button>Export report (HTML)</button></a>
        <a href={`/api/v1/scenarios/${id}/export?format=md`}><button>Export Markdown</button></a>
      </div>
      <section className="card">
        <h3>Changes</h3>
        {changes.data?.length === 0 && <p className="muted small">No changes yet. Select a course on the map and use the “Scenario edit” tab.</p>}
        <ol style={{ margin: 0, paddingLeft: 20 }}>
          {changes.data?.map((c) => (
            <li key={c.id} style={{ opacity: c.applied ? 1 : 0.5 }}>
              {c.summary} {!c.applied && <span className="badge">undone</span>}
              {c.assumptions.map((a) => <div key={a} className="small muted">Assumption: {a}</div>)}
            </li>
          ))}
        </ol>
      </section>
      <section className="card">
        <div className="row wrap">
          <h3 className="grow">Analysis</h3>
          {runs.data && runs.data.length > 1 && (
            <select aria-label="Analysis run" value={runId ?? ""} onChange={(e) => setRunId(e.target.value)}>
              {runs.data.map((r) => <option key={r.id} value={r.id}>rev {r.scenario_revision} · {new Date(r.started_at).toLocaleTimeString()}{r.stale ? " (stale)" : ""}</option>)}
            </select>
          )}
        </div>
        {!run.data && <p className="muted small">Not analysed yet.</p>}
        {run.data && <RunView run={run.data} />}
      </section>
      <CompareSection id={id} />
      <RebasePublish id={id} revision={sc.revision} baseVersionId={sc.base_version_id} onErr={onErr} />
    </div>
  );
}

function RunView({ run }: { run: AnalysisRun }) {
  const setHighlight = useUi((s) => s.setHighlight);
  const nav = useNavigate();
  const sum = run.summary as { by_class?: Record<string, number>; coverage?: { baseline: { numerator: number; denominator: number }; scenario: { numerator: number; denominator: number } }; congestion?: { note: string }; workload?: { note: string } } | null;
  const highlight = (f: Finding) => {
    setHighlight({ mode: "finding", nodes: [...new Set([...f.affected_entity_ids, ...f.paths.flatMap((p) => p.entity_ids)])], edges: f.paths.flatMap((p) => p.edges.map((e) => e.key)), label: f.title });
    nav("/");
  };
  const groups = Object.keys(CLASS_TITLE).map((k) => [k, (run.findings ?? []).filter((f) => f.consequence_class === k)] as const);
  return (
    <div className="col">
      {run.stale && <div className="notice"><span className="badge stale">STALE</span> This analysis evaluated revision {run.scenario_revision}; the scenario has changed since. Re-run before relying on it.</div>}
      <div className="small muted">
        Engine {run.algorithm_version} · evaluated revision {run.scenario_revision} · input hash <span className="mono">{run.input_hash.slice(0, 12)}</span>{run.cached ? " · reproduced from cache (same inputs)" : ""}
      </div>
      {sum?.coverage && (
        <div className="small">Documented program-outcome coverage (required path): baseline {sum.coverage.baseline.numerator}/{sum.coverage.baseline.denominator} → scenario {sum.coverage.scenario.numerator}/{sum.coverage.scenario.denominator}</div>
      )}
      {sum?.congestion && <div className="small muted">Assessment congestion: {sum.congestion.note}</div>}
      <ExplainButton runId={run.id} />
      {groups.map(([k, fs]) => fs.length > 0 && (
        <section key={k}>
          <h4>{CLASS_TITLE[k]} ({fs.length})</h4>
          {fs.map((f) => (
            <div key={f.id} className="card small" aria-label={f.title}>
              <div className="row wrap">
                <span className={`badge ${f.severity === "high" ? "rejected" : f.severity === "medium" ? "proposed" : ""}`}>{f.severity}</span>
                <span className={`badge ${f.evidence_basis === "explicit_statement" ? "documented" : "inferred"}`}>{f.evidence_basis === "explicit_statement" ? "documented" : f.evidence_basis === "assumption" ? "assumption" : "inferred"}</span>
                <strong className="grow">{f.title}</strong>
                <button className="small" onClick={() => highlight(f)}>Show on map</button>
              </div>
              <div>{f.explanation}</div>
              {f.paths.map((p) => <div key={p.ordinal} className="path">Path: {p.labels.join(" → ")}</div>)}
              {f.assumptions.map((a) => <div key={a} className="muted">Assumption: {a}</div>)}
              {f.suggested_actions.map((a) => <div key={a}>Suggested: {a}</div>)}
              <div className="row wrap" style={{ marginTop: 4 }}>
                {f.evidence.filter((e) => e.evidence_span_id).map((e) => <EvidenceChip key={e.evidence_span_id!} spanId={e.evidence_span_id!} />)}
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

function ExplainButton({ runId }: { runId: string }) {
  const m = useAiMutations();
  const [err, setErr] = useState<string | null>(null);
  const d = m.explain.data;
  return (
    <div>
      <button className="small" disabled={m.explain.isPending} onClick={() => { setErr(null); m.explain.mutate(runId, { onError: (e) => setErr(e instanceof ApiError ? e.message : String(e)) }); }}
        title="Plain-language explanation by the synthesis model. The deterministic findings below remain authoritative.">
        {m.explain.isPending ? "Explaining…" : "Explain with AI"}
      </button>
      {err && <span className="error small"> {err}</span>}
      {d && (
        <div className="card small" style={{ marginTop: 6 }}>
          <div className="row wrap"><span className="badge inferred">AI explanation</span><span className="badge">{d.meta.model}</span>{d.stale && <span className="badge stale">explains a stale run</span>}</div>
          <p>{d.summary}</p>
          <ul>{d.key_points.map((k, i) => <li key={i}>{k.point}</li>)}</ul>
          {d.judgment_calls.length > 0 && <div><strong>Judgment calls:</strong> {d.judgment_calls.join(" · ")}</div>}
          {d.missing_information.length > 0 && <div><strong>Missing information:</strong> {d.missing_information.join(" · ")}</div>}
          <p className="muted">{d.meta.label}</p>
        </div>
      )}
    </div>
  );
}

interface CompareOut {
  left: { kind: string; label: string };
  right: { kind: string; label: string };
  diff: {
    entities: Record<"added" | "removed" | "modified", { id: string; key: string; type: string; title: string; placement_before: { year: number; term: string } | null; placement_after: { year: number; term: string } | null }[]>;
    relationships: Record<"added" | "removed", { key: string; type: string; source: string; target: string; basis: string }[]>;
  };
}

function CompareSection({ id }: { id: string }) {
  const { versionId } = useUi();
  const all = useScenarios(versionId);
  const [other, setOther] = useState("");
  const cmp = useQuery({ queryKey: ["compare", id, other], queryFn: () => getJson<CompareOut>(`/api/v1/scenarios/${id}/compare${qs({ other: other || undefined })}`) });
  const d = cmp.data?.diff;
  return (
    <section className="card">
      <div className="row wrap">
        <h3 className="grow">Compare</h3>
        <select aria-label="Compare against" value={other} onChange={(e) => setOther(e.target.value)}>
          <option value="">Baseline</option>
          {all.data?.filter((x) => x.id !== id).map((x) => <option key={x.id} value={x.id}>Scenario: {x.title}</option>)}
        </select>
      </div>
      {cmp.error && <p className="error small">{String((cmp.error as Error).message)}</p>}
      {d && (
        <div className="row wrap" style={{ alignItems: "flex-start", gap: 24 }}>
          <div>
            <h4>{cmp.data!.left.label} → {cmp.data!.right.label}</h4>
            {(["added", "removed", "modified"] as const).map((k) => (
              <div key={k} className="small"><span className={`badge ${k}`}>{k}</span> {d.entities[k].map((e) => `${e.key}${k === "modified" && e.placement_before && e.placement_after && (e.placement_before.year !== e.placement_after.year || e.placement_before.term !== e.placement_after.term) ? ` (Y${e.placement_before.year} ${e.placement_before.term} → Y${e.placement_after.year} ${e.placement_after.term})` : ""}`).join(", ") || "none"}</div>
            ))}
          </div>
          <div>
            <h4>Relationships</h4>
            {(["added", "removed"] as const).map((k) => (
              <div key={k} className="small"><span className={`badge ${k}`}>{k}</span> {d.relationships[k].map((r) => `${r.source} → ${r.target} (${r.type.replace("_", " ")})`).join("; ") || "none"}</div>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

function RebasePublish({ id, revision, baseVersionId, onErr }: { id: string; revision: number; baseVersionId: string; onErr: (e: unknown) => void }) {
  const { programId } = useUi();
  const versions = useVersions(programId);
  const m = useScenarioMutations(id);
  const [target, setTarget] = useState("");
  const [conflicts, setConflicts] = useState<{ seq: number; op_type: string; reason: string }[] | null>(null);
  const [label, setLabel] = useState("");
  const [published, setPublished] = useState<string | null>(null);
  const rebase = (resolution: "abort" | "drop_conflicting") =>
    fetch(`/api/v1/scenarios/${id}/rebase`, { method: "POST", headers: { "Content-Type": "application/json", "If-Match": String(revision) }, body: JSON.stringify({ new_base_version_id: target, resolution }) })
      .then(async (r) => {
        const b = await r.json();
        if (r.status === 409 && b.error?.code === "rebase_conflicts") setConflicts(b.error.details);
        else if (!r.ok) onErr(new ApiError(r.status, b.error?.code, b.error?.message));
        else { setConflicts(null); m.update.reset(); window.location.reload(); }
      });
  return (
    <section className="card">
      <h3>Rebase and publish</h3>
      <div className="row wrap">
        <select aria-label="Rebase onto version" value={target} onChange={(e) => setTarget(e.target.value)}>
          <option value="">Rebase onto…</option>
          {versions.data?.filter((v) => v.id !== baseVersionId).map((v) => <option key={v.id} value={v.id}>{v.label} ({v.status})</option>)}
        </select>
        <button disabled={!target} onClick={() => rebase("abort")}>Check and rebase</button>
      </div>
      {conflicts && (
        <div className="notice small">
          <strong>Rebase conflicts (nothing changed):</strong>
          <ul>{conflicts.map((c) => <li key={c.seq}>#{c.seq} {c.op_type}: {c.reason}</li>)}</ul>
          <button className="small" onClick={() => rebase("drop_conflicting")}>Rebase and drop inapplicable changes</button>
        </div>
      )}
      <div className="row wrap" style={{ marginTop: 8 }}>
        <input type="text" aria-label="New version label" placeholder="New version label" value={label} onChange={(e) => setLabel(e.target.value)} />
        <button disabled={!label} onClick={() => {
          if (!window.confirm(`Publish this scenario as a new curriculum version “${label}”? The base version stays unchanged.`)) return;
          m.publish.mutate({ revision, label }, { onSuccess: (r) => setPublished(r.version_id), onError: onErr });
        }}>Publish as new version…</button>
      </div>
      <p className="muted small">Publishing requires the editor role and creates a new immutable version. The original snapshot remains available.</p>
      {published && <p className="info small">Published. Select the new version in the top bar.</p>}
    </section>
  );
}
