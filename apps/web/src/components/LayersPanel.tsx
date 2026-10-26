import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { useAiStatus } from "../api/aiHooks";
import { ApiError } from "../api/client";
import { useAiJobs } from "../api/hooks";
import { useLinkDecisions, useMapPreview, useMapRun, type WebView } from "../api/webHooks";
import { useUi } from "../store/ui";
import { isActive, payloadOf } from "./jobCenterModel";
import {
  HORIZONTAL_KINDS,
  LINK_KIND_LABEL,
  NODE_TYPE_LABEL,
  VERTICAL_KINDS,
  linkKind,
  type LinkKind,
  type WebFilters,
} from "../web/webFilters";
import {
  CONNECTION_COLOR,
  CONNECTION_GLYPH,
  CONNECTION_HELP,
  CONNECTION_LEVELS,
  CONNECTION_TEXT,
  PROVENANCE_DASH,
  PROVENANCE_TEXT,
  STRENGTH_TEXT,
  STRENGTH_WIDTH,
  TYPE_COLOR,
} from "../web/webStyle";

function toggle<T>(arr: T[], v: T): T[] {
  return arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v];
}

const KIND_COLOR: Partial<Record<LinkKind, string>> = {
  clo_plo: TYPE_COLOR.contributes_to,
  assessment_clo: TYPE_COLOR.assesses,
  topic_clo: TYPE_COLOR.supports,
  clo_clo: TYPE_COLOR.prepares_for,
  assessment_next: TYPE_COLOR.prepares_for,
  topic_prep: TYPE_COLOR.prepares_for,
  prerequisite: TYPE_COLOR.formal_prerequisite,
  preparation: TYPE_COLOR.inferred_preparation,
  containment: "#b8c1cf",
  overlap: TYPE_COLOR.possible_overlap,
};

const Line = ({ color = "#56607a", width = 2, dash }: { color?: string; width?: number; dash?: string }) => (
  <svg width="26" height="10" aria-hidden style={{ flex: "none" }}>
    <line x1="1" y1="5" x2="25" y2="5" stroke={color} strokeWidth={width} strokeDasharray={dash} />
  </svg>
);

export function LayersPanel({ view, onReviewProposed }: { view: WebView | undefined; onReviewProposed?: () => void }) {
  const { webFilters: f, setWebFilters, resetWebFilters, webFocus, setWebFocus, highlight, clearHighlight, scenarioId } = useUi();
  const counts = useMemo(() => {
    const kinds: Record<string, number> = {};
    const labels: Record<string, number> = {};
    const prov: Record<string, number> = {};
    const types: Record<string, number> = {};
    const conn: Record<string, number> = {};
    const mapState = { none: 0, unmapped: 0 };
    if (!view) return { kinds, labels, prov, types, conn, mapState };
    const byId = new Map(view.nodes.map((n) => [n.id, n]));
    const primary: Record<string, string> = { course_outcome: "contributes_to", assessment: "assesses", topic: "supports" };
    for (const n of view.nodes) {
      types[n.type] = (types[n.type] ?? 0) + 1;
      const m = n.mapping?.[primary[n.type] ?? ""];
      if (m && !m.best) m.verdict === "none" ? (mapState.none += 1) : (mapState.unmapped += 1);
    }
    for (const e of view.edges) {
      if (e.bundle) continue;
      const k = linkKind(e, byId);
      kinds[k] = (kinds[k] ?? 0) + 1;
      if (e.strength?.label) labels[e.strength.label] = (labels[e.strength.label] ?? 0) + 1;
      if (e.connection) conn[e.connection] = (conn[e.connection] ?? 0) + 1;
      prov[e.provenance] = (prov[e.provenance] ?? 0) + 1;
    }
    return { kinds, labels, prov, types, conn, mapState };
  }, [view]);

  const set = (p: Partial<WebFilters>) => setWebFilters(p);
  const kindBox = (k: LinkKind) => (
    <label key={k} className="row layer-row">
      <input type="checkbox" checked={f.linkKinds.includes(k)} onChange={() => set({ linkKinds: toggle(f.linkKinds, k) })} />
      <Line color={KIND_COLOR[k]} dash={k === "containment" ? "2 3" : undefined} />
      <span className="grow">{LINK_KIND_LABEL[k]}</span>
      <span className="muted small">{counts.kinds[k] ?? 0}</span>
    </label>
  );

  return (
    <aside className="panel layers" aria-label="Layers">
      {highlight.mode && (
        <section className="info small">
          <div><strong>Highlighting:</strong> {highlight.label}</div>
          <button className="small" onClick={clearHighlight}>Clear highlight</button>
        </section>
      )}
      {webFocus && (
        <section className="info small">
          Focused on <strong>{view?.nodes.find((n) => n.id === webFocus)?.key ?? "selection"}</strong>; everything else is faded.{" "}
          <button className="small" onClick={() => setWebFocus(null)}>Show whole program (Esc)</button>
        </section>
      )}
      <section>
        <h4>Elements</h4>
        {Object.entries(NODE_TYPE_LABEL).map(([t, label]) => (
          <label key={t} className="row layer-row">
            <input type="checkbox" checked={f.nodeTypes.includes(t)} onChange={() => set({ nodeTypes: toggle(f.nodeTypes, t) })} />
            <span className="grow">{label}</span>
            <span className="muted small">{counts.types[t] ?? 0}</span>
          </label>
        ))}
      </section>
      <section>
        <h4>Vertical alignment ↕</h4>
        {VERTICAL_KINDS.map(kindBox)}
      </section>
      <section>
        <h4>Horizontal alignment ↔</h4>
        {HORIZONTAL_KINDS.map(kindBox)}
      </section>
      <section>
        <h4>Structure</h4>
        {(["containment", "overlap"] as LinkKind[]).map(kindBox)}
      </section>
      <section>
        <h4>Connection level</h4>
        {CONNECTION_LEVELS.map((c) => (
          <label key={c} className="row layer-row" title={CONNECTION_HELP[c]}>
            <input type="checkbox" checked={(f.connections ?? CONNECTION_LEVELS).includes(c)} onChange={() => set({ connections: toggle(f.connections ?? [...CONNECTION_LEVELS], c) })} />
            <Line color={CONNECTION_COLOR[c]} width={3} />
            <span className="grow">{CONNECTION_TEXT[c]} <span className="muted small" aria-hidden>{CONNECTION_GLYPH[c]}</span></span>
            <span className="muted small">{counts.conn[c] ?? 0}</span>
          </label>
        ))}
        <div className="row layer-row small" title="The AI mapping examined these elements and found no connection">
          <span className="map-chip none" aria-hidden>✗</span>
          <span className="grow">No connection found (AI)</span>
          <span className="muted small">{counts.mapState.none}</span>
        </div>
        <div className="row layer-row small" title="Not examined by the AI mapping yet">
          <span className="map-chip unmapped" aria-hidden>?</span>
          <span className="grow">Not mapped yet</span>
          <span className="muted small">{counts.mapState.unmapped}</span>
        </div>
        <fieldset className="row small" style={{ marginTop: 4 }}>
          <legend className="sr-only">Colour links by</legend>
          <span className="muted">Colour links by</span>
          <label className="row"><input type="radio" name="colorBy" checked={(f.colorBy ?? "connection") === "connection"} onChange={() => set({ colorBy: "connection" })} /> level</label>
          <label className="row"><input type="radio" name="colorBy" checked={f.colorBy === "type"} onChange={() => set({ colorBy: "type" })} /> type</label>
        </fieldset>
        {view && <MapConnections view={view} />}
      </section>
      <section>
        <h4>Strength</h4>
        {(["strong", "moderate", "weak"] as const).map((s) => (
          <label key={s} className="row layer-row">
            <input type="checkbox" checked={f.strengths.includes(s)} onChange={() => set({ strengths: toggle(f.strengths, s) })} />
            <Line width={STRENGTH_WIDTH[s]} />
            <span className="grow">{STRENGTH_TEXT[s]} <span className="muted small">{s === "strong" ? "≥ 0.70" : s === "moderate" ? "0.45–0.70" : "< 0.45"}</span></span>
            <span className="muted small">{counts.labels[s] ?? 0}</span>
          </label>
        ))}
      </section>
      <section>
        <h4>Source of the link</h4>
        {(["documented", "accepted_inference", "proposed", "scenario"] as const).map((p) => (
          <label key={p} className="row layer-row">
            <input type="checkbox" checked={f.provenance.includes(p)} onChange={() => set({ provenance: toggle(f.provenance, p) })} />
            <Line dash={PROVENANCE_DASH[p]} />
            <span className="grow">{PROVENANCE_TEXT[p]}</span>
            <span className="muted small">{counts.prov[p] ?? 0}</span>
          </label>
        ))}
        {(counts.prov.proposed ?? 0) > 0 && onReviewProposed && (
          <button className="small" style={{ marginTop: 4 }} onClick={onReviewProposed}>Review {counts.prov.proposed} proposed link(s)…</button>
        )}
        {view && <ProposeLinks view={view} />}
      </section>
      <section>
        <h4>Highlight</h4>
        <label className="row layer-row">
          <input type="checkbox" checked={f.showGaps} onChange={() => set({ showGaps: !f.showGaps })} />
          <span className="grow">Gaps and missing links</span>
          <span className="muted small">{view?.gaps.length ?? 0}</span>
        </label>
        <label className="row layer-row" title={scenarioId ? "" : "Select a scenario to compare against the baseline"}>
          <input type="checkbox" disabled={!scenarioId} checked={f.changedOnly} onChange={() => set({ changedOnly: !f.changedOnly })} />
          <span className="grow">Only what the scenario changed</span>
        </label>
      </section>
      <section>
        <h4>Years</h4>
        <div className="row wrap">
          {[1, 2, 3, 4].map((y) => (
            <label key={y} className="row"><input type="checkbox" checked={f.years.includes(y)} onChange={() => set({ years: toggle(f.years, y) })} /> Y{y}</label>
          ))}
          <label className="row"><input type="checkbox" checked={f.years.includes(0)} onChange={() => set({ years: toggle(f.years, 0) })} /> Unknown</label>
        </div>
      </section>
      <button className="small" onClick={resetWebFilters}>Reset layers</button>
    </aside>
  );
}

/** Ask the synthesis model to propose missing links (draft versions only); results appear as dashed proposals. */
function ProposeLinks({ view }: { view: WebView }) {
  const { versionId, scenarioId, selection } = useUi();
  const dec = useLinkDecisions(versionId);
  const status = useAiStatus();
  const qc = useQueryClient();
  const [msg, setMsg] = useState<string | null>(null);
  const running = useRunning("ai_propose_alignment", versionId);
  const route = status.data?.routes.find((r) => r.name === "synthesize");
  const published = view.curriculum_version.status !== "draft";
  const course = selection?.kind === "node" ? view.nodes.find((n) => n.id === selection.id && n.type === "course") : undefined;
  const why = scenarioId
    ? "Leave the scenario to add proposals to the draft."
    : published
      ? "Proposals are added to draft versions only; select or create a draft version."
      : route && !route.available
        ? route.unavailable_reasons.join("; ")
        : null;
  const run = async () => {
    setMsg(null);
    try {
      await dec.propose.mutateAsync({ course_ids: course ? [course.id] : null });
      qc.invalidateQueries({ queryKey: ["ai-jobs"] });
      setMsg("Started. Progress and the result are shown at the bottom right.");
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    }
  };
  return (
    <div style={{ marginTop: 6 }}>
      <button className="small" disabled={!!why || !!running || dec.propose.isPending} onClick={run}
        title={why ?? `Sends outcome statements, assessment titles, and topic names to ${route?.active?.join(" · ") ?? "the synthesis model"}`}>
        {running ? `Proposing… ${Math.round(running.progress * 100)}%` : course ? `Propose missing links for ${course.key} (AI)` : "Propose missing links (AI)"}
      </button>
      {why && <div className="muted small">{why}</div>}
      {msg && <div className="muted small" aria-live="polite">{msg}</div>}
    </div>
  );
}

/** Map every outcome, assessment and topic (the selected course, or the whole program) with the AI: proposes each
 *  element's connections with a level, or records that it found none. Shows the cost before anything is sent. */
export function MapConnections({ view, courseId }: { view: WebView; courseId?: string | null }) {
  const { versionId, scenarioId, selection } = useUi();
  const status = useAiStatus();
  const qc = useQueryClient();
  const run = useMapRun(versionId);
  const [msg, setMsg] = useState<string | null>(null);
  const running = useRunning("ai_map_alignment", versionId);
  const route = status.data?.routes.find((r) => r.name === "synthesize");
  const published = view.curriculum_version.status !== "draft";
  const picked = courseId !== undefined ? courseId : selection?.kind === "node" ? selection.id : null;
  const course = picked ? view.nodes.find((n) => n.id === picked && n.type === "course") : undefined;
  const why = scenarioId
    ? "Leave the scenario to map the draft itself."
    : published
      ? "Mapping writes proposals to draft versions only; select or create a draft version."
      : route && !route.available
        ? route.unavailable_reasons.join("; ")
        : null;
  const preview = useMapPreview(versionId, course ? [course.id] : [], !why);
  const p = preview.data;
  const go = async () => {
    if (!p) return;
    const what = course ? course.key : `all ${p.courses} courses`;
    if (!window.confirm(`Map ${what} with the AI? ${p.calls} call(s) to ${p.model}, about $${p.estimate_usd.toFixed(2)} (limit $${p.cost_cap_usd.toFixed(2)}). Element texts are sent to the provider; results are proposals.`)) return;
    setMsg(null);
    try {
      await run.mutateAsync({ course_ids: course ? [course.id] : null });
      qc.invalidateQueries({ queryKey: ["ai-jobs"] });
      setMsg("Started. Progress and the result are shown at the bottom right; you can keep working.");
    } catch (e) {
      setMsg(e instanceof ApiError ? e.message : String(e));
    }
  };
  return (
    <div style={{ marginTop: 6 }}>
      <button className="small primary" disabled={!!why || !!running || run.isPending || !p || !p.calls} onClick={go}
        title={why ?? `Every outcome, assessment and topic is checked against its possible targets by ${route?.active?.join(" · ") ?? "the synthesis model"}`}>
        {running ? `Mapping… ${Math.round(running.progress * 100)}%` : course ? `Map ${course.key}'s connections (AI)` : "Map all connections (AI)"}
      </button>
      {!why && p && <div className="muted small">{p.calls} call(s) · about ${p.estimate_usd.toFixed(2)} · {p.elements} element(s)</div>}
      {why && <div className="muted small">{why}</div>}
      {msg && <div className="muted small" aria-live="polite">{msg}</div>}
    </div>
  );
}

/** The running AI job of a kind for this version, if any (shared with the job center's query). */
function useRunning(kind: string, versionId: string | null) {
  const jobs = useAiJobs();
  return jobs.data?.find((j) => j.kind === kind && isActive(j) && payloadOf(j).curriculum_version_id === versionId);
}
