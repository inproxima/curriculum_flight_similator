import type { Viewport } from "@xyflow/react";
import { useEffect, useMemo, useState } from "react";
import { useAnalysis, useScenario, useScenarioMutations, type Finding } from "../api/scenarioHooks";
import { useBaselineWeb, type Scorecard, type WebView } from "../api/webHooks";
import { useUi, type SimMode } from "../store/ui";
import { WebGraph } from "../web/WebGraph";
import { applyWebFilters } from "../web/webFilters";
import type { WebLayout } from "../web/webLayout";
import { applyRipple, captionFor, clampStep, type Ripple, type RippleImpact } from "../web/ripple";
import { STATUS_GLYPH, STATUS_TEXT } from "../web/webStyle";
import { TILES, delta } from "./ScorecardStrip";

const MODES: { id: SimMode; label: string; hint: string }[] = [
  { id: "diff", label: "What changed", hint: "Colour-coded differences from the baseline" },
  { id: "ripple", label: "Ripple", hint: "Replay the change spreading outward, one step at a time" },
  { id: "split", label: "Before / after", hint: "Baseline and scenario side by side with a scorecard" },
  { id: "list", label: "Impact list", hint: "Every consequence, most important first" },
];

const reducedMotion = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

export function SimulationPanel({ view, layout }: { view: WebView; layout: WebLayout }) {
  const { sim, setSim, scenarioId, webFilters, selection, select, highlight, setHighlight, clearHighlight, requestFit } = useUi();
  const run = useAnalysis(sim.runId);
  const scenario = useScenario(scenarioId);
  const sm = useScenarioMutations(scenarioId);
  const ripple = (run.data?.summary as { ripple?: Ripple } | null)?.ripple ?? null;
  const visible = useMemo(() => applyWebFilters(view, { ...webFilters, changedOnly: false }), [view, webFilters]);
  const stale = !!run.data && !!scenario.data && run.data.scenario_revision !== scenario.data.revision;

  // zoom to what changed when a run opens (the whole program is usually too wide to read)
  const runId = run.data?.id;
  useEffect(() => {
    if (!runId || !ripple) return;
    const ids = ripple.impacts.filter((i) => i.status !== "affected").map((i) => i.entity_id);
    if (ids.length) window.setTimeout(() => requestFit(ids), 150);
  }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps

  // autoplay the ripple
  useEffect(() => {
    if (!sim.playing || sim.mode !== "ripple" || !ripple) return;
    if (reducedMotion()) {
      setSim({ playing: false, step: ripple.max_hop });
      return;
    }
    const t = window.setTimeout(() => {
      if (sim.step >= ripple.max_hop) setSim({ playing: false });
      else setSim({ step: sim.step + 1 });
    }, 900);
    return () => window.clearTimeout(t);
  }, [sim.playing, sim.step, sim.mode, ripple, setSim]);

  const rerun = async () => {
    const r = await sm.analyze.mutateAsync();
    setSim({ runId: r.id, step: 0 });
  };

  const showMe = (i: RippleImpact) => {
    setHighlight({ mode: "finding", nodes: i.path, edges: i.edge_keys, label: i.explanation });
    setSim({ mode: "ripple", step: i.hop, playing: false });
    select({ kind: "node", id: i.entity_id });
    requestFit(i.path);
  };

  const emphasis = highlight.mode && highlight.nodes.length ? new Set(highlight.nodes) : null;
  const common = {
    layout,
    visible,
    emphasis,
    showGaps: false,
    selectedId: selection?.id ?? null,
    onNodeClick: (n: { id: string }) => select({ kind: "node", id: n.id }),
    onEdgeClick: (e: { id: string; key: string | null }) => select({ kind: "edge", id: e.id, key: e.key }),
    onPaneClick: () => clearHighlight(),
  };

  return (
    <div className="sim-panel">
      <div className="sim-head row wrap">
        <strong>Simulation</strong>
        <span className="muted small">
          {view.scenario?.title} · {run.data ? `engine ${run.data.algorithm_version} · revision ${run.data.scenario_revision}` : "running…"}
        </span>
        {stale && (
          <span className="badge stale">Stale — the scenario changed since this run <button className="link small" onClick={rerun}>Re-run</button></span>
        )}
        <div role="tablist" aria-label="Simulation views" className="row sim-tabs">
          {MODES.map((m) => (
            <button key={m.id} role="tab" aria-selected={sim.mode === m.id} className={`small ${sim.mode === m.id ? "primary" : ""}`} title={m.hint}
              onClick={() => setSim({ mode: m.id, playing: false })}>{m.label}</button>
          ))}
        </div>
        <button className="small" onClick={() => { setSim({ open: false, playing: false }); clearHighlight(); }} aria-label="Close simulation">Close</button>
      </div>
      {!run.data && <div className="page muted">Running the deterministic analysis…</div>}
      {run.data && sim.mode === "diff" && (
        <div className="sim-body">
          <DiffSummary view={view} />
          <WebGraph {...common} view={view} diff ariaLabel="Scenario differences" />
        </div>
      )}
      {run.data && sim.mode === "ripple" && ripple && <RippleView view={view} ripple={ripple} common={common} />}
      {run.data && sim.mode === "split" && <SplitView view={view} common={common} />}
      {run.data && sim.mode === "list" && ripple && <ImpactList ripple={ripple} findings={run.data.findings ?? []} onShow={showMe} view={view} />}
    </div>
  );
}

type Common = Omit<Parameters<typeof WebGraph>[0], "view" | "diff">;

function DiffSummary({ view }: { view: WebView }) {
  const counts: Record<string, number> = {};
  for (const n of view.nodes) if (n.status !== "unchanged") counts[n.status] = (counts[n.status] ?? 0) + 1;
  for (const e of view.edges) if (e.status !== "unchanged") counts[e.status] = (counts[e.status] ?? 0) + 1;
  return (
    <div className="sim-caption small" role="status">
      {Object.keys(counts).length === 0 ? "No differences from the baseline." : Object.entries(counts).map(([s, n]) => (
        <span key={s} className={`badge status-${s}`}>{STATUS_GLYPH[s]} {n} {STATUS_TEXT[s].toLowerCase()}</span>
      ))}
      <span className="muted"> · unaffected elements are faded</span>
    </div>
  );
}

function RippleView({ view, ripple, common }: { view: WebView; ripple: Ripple; common: Common }) {
  const { sim, setSim } = useUi();
  const step = clampStep(ripple, sim.step);
  const { view: v, frame } = useMemo(() => applyRipple(view, ripple, step), [view, ripple, step]);
  return (
    <div className="sim-body">
      <div className="sim-caption row wrap">
        <button className="small" aria-label={sim.playing ? "Pause" : "Play the ripple"} onClick={() => setSim({ playing: !sim.playing, step: sim.playing ? step : step >= ripple.max_hop ? 0 : step })}>
          {sim.playing ? "❚❚ Pause" : "▶ Play"}
        </button>
        <label className="row small">
          Step
          <input type="range" min={0} max={ripple.max_hop} step={1} value={step} aria-label="Ripple step" aria-valuetext={`Step ${step} of ${ripple.max_hop}`}
            onChange={(e) => setSim({ step: Number(e.target.value), playing: false })} />
          <span className="mono">{step} / {ripple.max_hop}</span>
        </label>
        <span className="small grow" aria-live="polite">{captionFor(ripple, step)}</span>
      </div>
      <WebGraph {...common} view={v} diff pulse={new Set([...frame.pulseEdges])} ariaLabel={`Ripple step ${step}`} />
    </div>
  );
}

function SplitView({ view, common }: { view: WebView; common: Common }) {
  const versionId = useUi((s) => s.versionId);
  const base = useBaselineWeb(versionId, true);
  const [vp, setVp] = useState<Viewport | undefined>(undefined);
  const baseView = useMemo<WebView | null>(() => {
    if (!base.data) return null;
    // draw the baseline on the scenario's layout so every element sits in the same place on both sides
    return { ...base.data, nodes: base.data.nodes.map((n) => ({ ...n, status: "unchanged" })) };
  }, [base.data]);
  const baseVisible = useMemo(() => (baseView ? applyWebFilters(baseView, { ...useUi.getState().webFilters, changedOnly: false }) : null), [baseView]);
  return (
    <div className="sim-body">
      <ScorecardDelta before={view.scorecard_baseline ?? null} after={view.scorecard} />
      <div className="split">
        <div className="split-pane">
          <div className="split-label">Before (baseline)</div>
          {baseView && baseVisible && <WebGraph {...common} view={baseView} visible={baseVisible} diff={false} viewport={vp} onViewportChange={setVp} ariaLabel="Baseline" />}
        </div>
        <div className="split-pane">
          <div className="split-label">After (scenario)</div>
          <WebGraph {...common} view={view} diff viewport={vp} onViewportChange={setVp} ariaLabel="Scenario" />
        </div>
      </div>
    </div>
  );
}

export function ScorecardDelta({ before, after }: { before: Scorecard | null; after: Scorecard }) {
  return (
    <table className="data small scorecard-delta" aria-label="Scorecard before and after">
      <thead><tr><th>Measure</th><th>Before</th><th>After</th><th>Change</th></tr></thead>
      <tbody>
        {TILES.map((t) => {
          const b = before ? t.value(before) : null;
          const a = t.value(after);
          const d = delta(b, a, t.better);
          return (
            <tr key={t.label} title={t.title}>
              <td>{t.label}</td>
              <td>{b == null ? "—" : t.fmt(b)}</td>
              <td>{a == null ? "—" : t.fmt(a)}</td>
              <td className={d?.good === true ? "good" : d?.good === false ? "bad" : ""}>
                {!d || d.dir === "same" ? "no change" : `${d.dir === "up" ? "▲" : "▼"} ${Math.abs(d.d)} (${d.good ? "better" : "worse"})`}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

const TYPE_NOUN: Record<string, string> = {
  program_outcome: "Program outcome",
  course_outcome: "Course outcome",
  assessment: "Assessment",
  topic: "Topic",
  course: "Course",
};

function ImpactList({ ripple, findings, onShow, view }: { ripple: Ripple; findings: Finding[]; onShow: (i: RippleImpact) => void; view: WebView }) {
  const [showAffected, setShowAffected] = useState(false);
  const shown = ripple.impacts.filter((i) => showAffected || i.status !== "affected");
  const hidden = ripple.impacts.length - shown.length;
  const alignFindings = findings.filter((f) => f.category === "alignment" || f.consequence_class === "direct_documented");
  return (
    <div className="sim-body scroll">
      <div className="page">
        <h3>Impacts, most important first ({shown.length})</h3>
        <ol className="list impact-list">
          {shown.map((i) => (
            <li key={i.entity_id} className={`impact st-${i.status}`}>
              <div className="row wrap">
                <span className={`badge status-${i.status}`}>{STATUS_GLYPH[i.status]} {STATUS_TEXT[i.status]}</span>
                <strong>{i.key}</strong>
                <span className="muted small">{TYPE_NOUN[i.type] ?? i.type}{i.course_key ? ` · ${i.course_key}` : ""} · step {i.hop}</span>
                <button className="small" onClick={() => onShow(i)}>Show me</button>
              </div>
              <div className="small">{i.explanation}</div>
            </li>
          ))}
        </ol>
        {hidden > 0 && <button className="link small" onClick={() => setShowAffected(true)}>Show {hidden} element(s) reached without a measurable change</button>}
        <h3 style={{ marginTop: 14 }}>Findings from the deterministic analysis ({findings.length})</h3>
        <ul className="list">
          {(alignFindings.length ? alignFindings : findings).slice(0, 40).map((f) => (
            <li key={f.id}>
              <span className={`badge ${f.severity === "high" ? "rejected" : f.severity === "medium" ? "proposed" : ""}`}>{f.severity}</span>{" "}
              <strong>{f.title}</strong> <span className="muted small">{f.consequence_class.replace(/_/g, " ")}</span>
              <div className="small muted">{f.explanation}</div>
              {f.paths[0] && <div className="path">{f.paths[0].labels.join(" → ")}</div>}
              {f.affected_entity_ids.length > 0 && (
                <button className="link small" onClick={() => {
                  const ids = f.paths[0]?.entity_ids ?? f.affected_entity_ids;
                  onShow({ entity_id: f.affected_entity_ids[0], path: ids, edge_keys: [], hop: ids.length - 1, explanation: f.title } as unknown as RippleImpact);
                }}>Show on web</button>
              )}
            </li>
          ))}
        </ul>
        <p className="muted small">
          {view.nodes.length} elements · this analysis is deterministic: the same scenario always produces the same impacts. It does not predict
          learning gains or certify accreditation compliance.
        </p>
      </div>
    </div>
  );
}
