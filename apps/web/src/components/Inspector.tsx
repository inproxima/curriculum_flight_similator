import * as Tabs from "@radix-ui/react-tabs";
import { useMemo, useState } from "react";
import { useEntity } from "../api/hooks";
import { useLinkDecisions, type CoursePair, type PloThread, type WebEdge, type WebNode, type WebView } from "../api/webHooks";
import { useUi } from "../store/ui";
import { gapShort } from "../web/webNodes";
import { PROVENANCE_TEXT, STATUS_GLYPH, STATUS_TEXT, STRENGTH_TEXT, THREAD_TEXT } from "../web/webStyle";
import { FieldEvidence } from "./Evidence";
import { QuickEdit } from "./QuickEdit";

const REL_TEXT: Record<string, string> = {
  contributes_to: "contributes to",
  assesses: "assesses",
  supports: "supports",
  prepares_for: "prepares for",
  formal_prerequisite: "is a prerequisite for",
  inferred_preparation: "prepares for (course level)",
  corequisite: "is a co-requisite of",
  has_outcome: "has outcome",
  has_assessment: "has assessment",
  covers_topic: "covers topic",
  possible_overlap: "may overlap with",
};
const LEVEL_TEXT: Record<string, string> = { introduce: "Introduce", reinforce: "Reinforce", assess: "Assess" };

interface Ctx {
  view: WebView;
  byId: Map<string, WebNode>;
  out: Map<string, WebEdge[]>;
  inc: Map<string, WebEdge[]>;
}

function useCtx(view: WebView): Ctx {
  return useMemo(() => {
    const byId = new Map(view.nodes.map((n) => [n.id, n]));
    const out = new Map<string, WebEdge[]>();
    const inc = new Map<string, WebEdge[]>();
    for (const e of view.edges) {
      if (e.bundle) continue;
      out.set(e.source, [...(out.get(e.source) ?? []), e]);
      inc.set(e.target, [...(inc.get(e.target) ?? []), e]);
    }
    return { view, byId, out, inc };
  }, [view]);
}

export function Strength({ label, score }: { label?: string | null; score?: number | null }) {
  if (!label) return <span className="badge">—</span>;
  return (
    <span className={`badge strength-${label}`} title={score != null ? `effective ${score.toFixed(2)}` : undefined}>
      {STRENGTH_TEXT[label]}{score != null ? ` ${score.toFixed(2)}` : ""}
    </span>
  );
}

function Prov({ p }: { p: string }) {
  const cls = p === "documented" ? "documented" : p === "proposed" ? "proposed" : "inferred";
  return <span className={`badge ${cls}`} title={PROVENANCE_TEXT[p]}>{p === "accepted_inference" ? "accepted inference" : p}</span>;
}

function Status({ s }: { s?: string }) {
  if (!s || s === "unchanged") return null;
  return <span className={`badge status-${s}`}>{STATUS_GLYPH[s]} {STATUS_TEXT[s]}</span>;
}

function NodeLink({ id, ctx, full }: { id: string; ctx: Ctx; full?: boolean }) {
  const select = useUi((s) => s.select);
  const n = ctx.byId.get(id);
  if (!n) return <span className="muted">(hidden)</span>;
  return (
    <button className="link" onClick={() => select({ kind: "node", id })} title={n.title}>
      {n.key}{full && n.key !== n.title ? ` — ${n.title}` : ""}
    </button>
  );
}

function EdgeRow({ e, ctx, from }: { e: WebEdge; ctx: Ctx; from: string }) {
  const select = useUi((s) => s.select);
  const other = e.source === from ? e.target : e.source;
  const dir = e.source === from ? `${REL_TEXT[e.type] ?? e.type} ` : `← ${REL_TEXT[e.type] ?? e.type} by `;
  return (
    <li>
      <span className="muted small">{dir}</span>
      <NodeLink id={other} ctx={ctx} full />{" "}
      {e.level && <span className="badge">{LEVEL_TEXT[e.level]}</span>}
      <Strength label={e.strength?.label} score={e.strength?.effective} /> <Prov p={e.provenance} /> <Status s={e.status} />{" "}
      <button className="link small" onClick={() => select({ kind: "edge", id: e.id, key: e.key })}>why?</button>
    </li>
  );
}

function alignmentEdges(ctx: Ctx, id: string, types: string[]) {
  return [...(ctx.out.get(id) ?? []), ...(ctx.inc.get(id) ?? [])].filter((e) => types.includes(e.type));
}

// ─────────────────────────── program outcome thread ───────────────────────────

function PloInspector({ n, ctx }: { n: WebNode; ctx: Ctx }) {
  const thread = ctx.view.plo_threads.find((t) => t.plo_id === n.id);
  const select = useUi((s) => s.select);
  if (!thread) return null;
  const stmt = (n.details as { statement_verbatim?: string }).statement_verbatim;
  const years = [...new Set(thread.stages.map((s) => s.year ?? 0))].sort();
  const cloIds = thread.stages.map((s) => s.clo_id).filter(Boolean) as string[];
  const topics = [...new Set(cloIds.flatMap((c) => (ctx.inc.get(c) ?? []).filter((e) => e.type === "supports").map((e) => e.source)))];
  return (
    <div>
      <h2>{n.title}</h2>
      {stmt && <p className="quote">{stmt}</p>}
      <div className="row wrap" style={{ margin: "6px 0" }}>
        <span className={`badge thread-${thread.status}`}>{THREAD_TEXT[thread.status]}</span>
        <Strength label={thread.label} score={thread.strength} />
        <Status s={n.status} />
      </div>
      <h4>Where it is taught and assessed</h4>
      <div className="thread-strip" role="list" aria-label={`Thread for ${n.key}`}>
        {years.map((y) => (
          <div key={y} className="thread-year" role="listitem">
            <div className="muted small">{y ? `Year ${y}` : "Timing unknown"}</div>
            {thread.stages.filter((s) => (s.year ?? 0) === y).map((s) => (
              <button key={s.relationship_key} className={`stage-card lvl-${s.level ?? "none"}`} onClick={() => s.clo_id && select({ kind: "node", id: s.clo_id })}>
                <div className="row" style={{ gap: 4 }}>
                  <strong>{s.course_key}</strong>
                  {s.level && <span className="badge">{LEVEL_TEXT[s.level]}</span>}
                </div>
                <div className="small">{s.clo_key ?? "course-level"}</div>
                <div className="row wrap" style={{ gap: 4 }}>
                  <Strength label={s.label} score={s.effective} />
                  {s.assessed ? <span className="badge documented">✓ assessed</span> : <span className="badge proposed">✗ not assessed</span>}
                </div>
                {s.assessment_ids.length > 0 && (
                  <div className="muted small">{s.assessment_ids.map((a) => ctx.byId.get(a)?.title ?? "…").join(", ")}</div>
                )}
              </button>
            ))}
          </div>
        ))}
        {thread.missing.map((lv) => (
          <div key={lv} className="thread-year">
            <div className="muted small">missing</div>
            <div className="stage-card missing">No course outcome {lv === "assess" ? "assesses" : `${lv}s`} this program outcome</div>
          </div>
        ))}
        {!thread.stages.length && <div className="stage-card missing">No course outcome contributes to this program outcome.</div>}
      </div>
      {topics.length > 0 && (
        <>
          <h4 style={{ marginTop: 10 }}>Topics behind it</h4>
          <div className="row wrap">{topics.map((t) => <NodeLink key={t} id={t} ctx={ctx} />)}</div>
        </>
      )}
      <QuickEdit key={`${n.id}:${ctx.view.scenario?.revision ?? "base"}`} node={n} view={ctx.view} />
    </div>
  );
}

// ─────────────────────────── course alignment ───────────────────────────

function PairList({ pairs, ctx, dir }: { pairs: CoursePair[]; ctx: Ctx; dir: "in" | "out" }) {
  const select = useUi((s) => s.select);
  if (!pairs.length) return <p className="muted small">None documented.</p>;
  return (
    <ul className="list">
      {pairs.map((p) => (
        <li key={`${p.source_course}:${p.target_course}`}>
          <div className="row wrap">
            <NodeLink id={dir === "in" ? p.source_course : p.target_course} ctx={ctx} />
            <span className="badge">{p.via === "formal_prerequisite" ? "prerequisite" : p.via === "inferred_preparation" ? "expected preparation" : "outcome link"}</span>
            <Strength label={p.label} score={p.strength} />
          </div>
          {p.edge_keys.length ? (
            <ul className="small" style={{ margin: "2px 0 0", paddingLeft: 16 }}>
              {p.edge_keys.map((k) => {
                const e = ctx.view.edges.find((x) => x.key === k);
                if (!e) return null;
                return (
                  <li key={k}>
                    <NodeLink id={e.source} ctx={ctx} /> → <NodeLink id={e.target} ctx={ctx} />{" "}
                    <Strength label={e.strength?.label} score={e.strength?.effective} /> <Prov p={e.provenance} />{" "}
                    <button className="link small" onClick={() => select({ kind: "edge", id: e.id, key: e.key })}>why?</button>
                  </li>
                );
              })}
            </ul>
          ) : (
            <div className="small error">⚠ No learning outcome or assessment is linked across these courses.</div>
          )}
        </li>
      ))}
    </ul>
  );
}

function CourseInspector({ n, ctx }: { n: WebNode; ctx: Ctx }) {
  const members = (ctx.out.get(n.id) ?? []).filter((e) => e.category === "containment").map((e) => ctx.byId.get(e.target)).filter(Boolean) as WebNode[];
  const clos = members.filter((m) => m.type === "course_outcome");
  const assessments = members.filter((m) => m.type === "assessment");
  const topics = members.filter((m) => m.type === "topic");
  const incoming = ctx.view.course_pairs.filter((p) => p.target_course === n.id);
  const outgoing = ctx.view.course_pairs.filter((p) => p.source_course === n.id);
  return (
    <div>
      <h2>{n.key} — {n.title}</h2>
      <div className="row wrap" style={{ marginBottom: 6 }}>
        <span className="badge">{n.year ? `Year ${n.year}` : "Year ?"} · {n.term ?? "term ?"}</span>
        {n.classification && <span className="badge">{n.classification.replace("_", " ")}</span>}
        <Strength label={(n.metrics as { label?: string }).label} score={(n.metrics as { score?: number }).score} />
        <Status s={n.status} />
      </div>
      {n.description && <p className="muted small">{n.description}</p>}
      <Tabs.Root defaultValue="vertical" className="tabs">
        <Tabs.List aria-label="Course alignment views">
          <Tabs.Trigger value="vertical">Outcomes ↕ program</Tabs.Trigger>
          <Tabs.Trigger value="assess">Assessments</Tabs.Trigger>
          <Tabs.Trigger value="horizontal">Before ↔ after</Tabs.Trigger>
        </Tabs.List>
        <Tabs.Content value="vertical">
          {!clos.length && <p className="muted">No documented learning outcomes. Contribution to program outcomes cannot be judged.</p>}
          {clos.map((c) => (
            <div key={c.id} className="card small">
              <div><NodeLink id={c.id} ctx={ctx} /> {c.title}</div>
              <ul className="list">
                {alignmentEdges(ctx, c.id, ["contributes_to", "assesses", "supports"]).map((e) => <EdgeRow key={e.id} e={e} ctx={ctx} from={c.id} />)}
              </ul>
              {!alignmentEdges(ctx, c.id, ["contributes_to"]).length && <div className="error small">⚠ Not linked to any program outcome.</div>}
            </div>
          ))}
          {topics.length > 0 && (
            <>
              <h4 style={{ marginTop: 8 }}>Topics covered</h4>
              <ul className="list small">
                {topics.map((t) => {
                  const sup = (ctx.out.get(t.id) ?? []).filter((e) => e.type === "supports");
                  return (
                    <li key={t.id}>
                      <NodeLink id={t.id} ctx={ctx} full /> {sup.length ? `→ supports ${sup.map((e) => ctx.byId.get(e.target)?.key).join(", ")}` : <span className="error">supports no outcome</span>}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </Tabs.Content>
        <Tabs.Content value="assess">
          {!assessments.length && <p className="muted">No assessments documented for this course.</p>}
          {assessments.length > 0 && (
            <table className="data small">
              <thead><tr><th>Assessment</th><th>Format</th><th>Weight</th><th>Week</th><th>Hours</th><th>Assesses</th></tr></thead>
              <tbody>
                {assessments.map((a) => {
                  const d = a.details as { format?: string; weight_percent?: number; timing_week?: number; documented_hours?: number };
                  const as = (ctx.out.get(a.id) ?? []).filter((e) => e.type === "assesses" || e.type === "prepares_for");
                  return (
                    <tr key={a.id}>
                      <td><NodeLink id={a.id} ctx={ctx} /> {a.title} <Status s={a.status} /></td>
                      <td>{d.format ?? "?"}</td>
                      <td>{d.weight_percent != null ? `${d.weight_percent}%` : "?"}</td>
                      <td>{d.timing_week ?? "?"}</td>
                      <td>{d.documented_hours ?? "—"}</td>
                      <td>
                        {as.length ? as.map((e) => (
                          <div key={e.id}>{e.type === "prepares_for" ? "prepares " : ""}<NodeLink id={e.target} ctx={ctx} /> <Strength label={e.strength?.label} /></div>
                        )) : <span className="error">nothing</span>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </Tabs.Content>
        <Tabs.Content value="horizontal">
          <h4>Prepared by (earlier courses)</h4>
          <PairList pairs={incoming} ctx={ctx} dir="in" />
          <h4 style={{ marginTop: 10 }}>Prepares for (later courses)</h4>
          <PairList pairs={outgoing} ctx={ctx} dir="out" />
        </Tabs.Content>
      </Tabs.Root>
      <QuickEdit key={`${n.id}:${ctx.view.scenario?.revision ?? "base"}`} node={n} view={ctx.view} />
      <EvidenceBlock id={n.id} />
    </div>
  );
}

function EvidenceBlock({ id }: { id: string }) {
  const { versionId, scenarioId } = useUi();
  const ent = useEntity(versionId, id, scenarioId);
  const fe = ent.data?.field_evidence ?? {};
  const keys = Object.keys(fe);
  if (!keys.length) return null;
  return (
    <details style={{ marginTop: 10 }}>
      <summary className="small">Source evidence ({keys.length} field{keys.length > 1 ? "s" : ""})</summary>
      <ul className="list small">{keys.map((k) => <li key={k}>{k}: <FieldEvidence spans={fe[k]} /></li>)}</ul>
    </details>
  );
}

// ─────────────────────────── single element ───────────────────────────

function ElementInspector({ n, ctx }: { n: WebNode; ctx: Ctx }) {
  const d = n.details as Record<string, unknown>;
  const typeText = { course_outcome: "Course learning outcome", assessment: "Assessment", topic: "Topic" }[n.type as string] ?? n.type;
  const edges = alignmentEdges(ctx, n.id, ["contributes_to", "assesses", "supports", "prepares_for"]);
  const up = edges.filter((e) => e.type !== "prepares_for");
  const across = edges.filter((e) => e.type === "prepares_for");
  const gaps = ctx.view.gaps.filter((g) => !g.source && g.entity_ids.includes(n.id));
  const m = n.metrics as { label?: string; score?: number };
  return (
    <div>
      <div className="muted small">{typeText}{n.course_ids?.length ? ` · ${n.course_ids.map((c) => ctx.byId.get(c)?.key).join(", ")}` : ""}</div>
      <h2>{n.key}</h2>
      <p>{n.title}</p>
      <div className="row wrap">
        <Strength label={m.label} score={m.score} />
        <Status s={n.status} />
        {gaps.map((g) => <span key={g.id} className="badge gap" title={g.message}>⚠ {gapShort(g.kind)}</span>)}
      </div>
      {n.type === "assessment" && (
        <table className="data small" style={{ marginTop: 6 }}>
          <tbody>
            <tr><th>Format</th><td>{String(d.format ?? "?")}</td></tr>
            <tr><th>Weight</th><td>{d.weight_percent != null ? `${d.weight_percent}%` : "not stated"}</td></tr>
            <tr><th>Timing</th><td>{d.timing_week ? `week ${d.timing_week}` : String(d.timing_text ?? "not stated")}</td></tr>
            <tr><th>Documented hours</th><td>{d.documented_hours != null ? String(d.documented_hours) : "not stated"}</td></tr>
          </tbody>
        </table>
      )}
      <h4 style={{ marginTop: 10 }}>Vertical alignment</h4>
      {up.length ? <ul className="list">{up.map((e) => <EdgeRow key={e.id} e={e} ctx={ctx} from={n.id} />)}</ul> : <p className="muted small">No vertical links.</p>}
      <h4 style={{ marginTop: 10 }}>Horizontal alignment</h4>
      {across.length ? <ul className="list">{across.map((e) => <EdgeRow key={e.id} e={e} ctx={ctx} from={n.id} />)}</ul> : <p className="muted small">No links to earlier or later courses.</p>}
      <QuickEdit key={`${n.id}:${ctx.view.scenario?.revision ?? "base"}`} node={n} view={ctx.view} />
      <EvidenceBlock id={n.id} />
    </div>
  );
}

// ─────────────────────────── edge: why this strength ───────────────────────────

const COMPONENT_TEXT: Record<string, string> = {
  trust: "How far the link is established: documented 1.00, accepted interpretation 0.85, scenario/manual 0.80, proposed 0.60",
  intensity: "How strongly the link is stated: the asserted strength, or the contribution level / assessment weight",
  support: "Whether the course outcome is actually assessed (+0.2) and taught through a topic (+0.1), from a base of 0.7",
};

function EdgeInspector({ e, ctx }: { e: WebEdge; ctx: Ctx }) {
  const { versionId, scenarioId } = useUi();
  const st = e.strength;
  return (
    <div>
      <div className="muted small">{e.category} link</div>
      <h2><NodeLink id={e.source} ctx={ctx} /> {REL_TEXT[e.type] ?? e.type} <NodeLink id={e.target} ctx={ctx} /></h2>
      <div className="row wrap">
        {st && <Strength label={st.label} score={st.effective} />}
        <Prov p={e.provenance} />
        {e.level && <span className="badge">{LEVEL_TEXT[e.level]}</span>}
        <Status s={e.status} />
        {e.strength_before != null && st?.effective != null && e.strength_before !== st.effective && (
          <span className="badge">was {e.strength_before.toFixed(2)}</span>
        )}
      </div>
      {e.rationale && <p className="quote">{e.rationale}</p>}
      {st?.components && (
        <>
          <h4 style={{ marginTop: 10 }}>Why this strength?</h4>
          <table className="data small">
            <tbody>
              {(["trust", "intensity", "support"] as const).map((k) => (
                <tr key={k}><th>{k}</th><td className="mono">{st.components![k].toFixed(2)}</td><td className="muted">{COMPONENT_TEXT[k]}</td></tr>
              ))}
              <tr><th>effective</th><td className="mono"><strong>{st.effective?.toFixed(2)}</strong></td><td className="muted">trust × intensity × support · strong ≥ 0.70, moderate ≥ 0.45</td></tr>
            </tbody>
          </table>
          {st.asserted != null && <p className="muted small">Asserted strength {st.asserted} ({st.asserted_source?.replace("_", " ")}).</p>}
        </>
      )}
      {e.bundle && <p className="muted small">Summarises {e.bundle.count} outcome-level links (min {e.bundle.min?.toFixed(2)}, max {e.bundle.max?.toFixed(2)}). Select the course to see them.</p>}
      <QuickEdit key={`${e.id}:${ctx.view.scenario?.revision ?? "base"}`} edge={e} view={ctx.view} />
      {e.provenance === "proposed" && e.key && <LinkReview edge={e} disabled={scenarioId ? "Decisions change the draft curriculum; leave the scenario first." : versionId ? null : "No version"} />}
    </div>
  );
}

export function LinkReview({ edge, disabled }: { edge: WebEdge; disabled: string | null }) {
  const versionId = useUi((s) => s.versionId);
  const dec = useLinkDecisions(versionId);
  const [editing, setEditing] = useState(false);
  const [strength, setStrength] = useState(edge.strength?.asserted ?? 0.7);
  const [level, setLevel] = useState(edge.level ?? "");
  const err = dec.decide.error as Error | null;
  const run = (decision: "accept" | "reject" | "edit") =>
    dec.decide.mutate({ key: edge.key!, decision, ...(decision === "edit" ? { strength, level: level || null } : {}) });
  return (
    <div className="card small" style={{ marginTop: 10 }}>
      <strong>Review this proposed link</strong>
      <p className="muted" style={{ margin: "4px 0" }}>Accepting keeps it labelled as an interpretation; it becomes solid on the web.</p>
      {disabled && <p className="notice">{disabled}</p>}
      <div className="row wrap">
        <button className="small primary" disabled={!!disabled || dec.decide.isPending} onClick={() => run("accept")}>Accept</button>
        <button className="small" disabled={!!disabled || dec.decide.isPending} onClick={() => setEditing(!editing)}>Edit…</button>
        <button className="small" disabled={!!disabled || dec.decide.isPending} onClick={() => run("reject")}>Reject</button>
      </div>
      {editing && (
        <div className="col" style={{ marginTop: 6 }}>
          <label className="row">Strength <input type="range" min={0} max={1} step={0.05} value={strength} onChange={(ev) => setStrength(Number(ev.target.value))} aria-label="Asserted strength" /> <span className="mono">{strength.toFixed(2)}</span></label>
          {edge.type === "contributes_to" && (
            <label className="row">Level
              <select value={level} onChange={(ev) => setLevel(ev.target.value)}>
                <option value="">not stated</option><option value="introduce">Introduce</option><option value="reinforce">Reinforce</option><option value="assess">Assess</option>
              </select>
            </label>
          )}
          <button className="small primary" disabled={!!disabled} onClick={() => run("edit")}>Save and accept</button>
        </div>
      )}
      {err && <div className="error">{err.message}</div>}
      {dec.decide.isSuccess && <div className="muted">Saved.</div>}
    </div>
  );
}

export function Inspector({ view }: { view: WebView }) {
  const selection = useUi((s) => s.selection);
  const ctx = useCtx(view);
  if (!selection) return <InspectorEmpty view={view} />;
  if (selection.kind === "edge") {
    const e = view.edges.find((x) => x.id === selection.id);
    return e ? <EdgeInspector e={e} ctx={ctx} /> : <InspectorEmpty view={view} />;
  }
  const n = ctx.byId.get(selection.id);
  if (!n) return <InspectorEmpty view={view} />;
  if (n.type === "program_outcome") return <PloInspector n={n} ctx={ctx} />;
  if (n.type === "course") return <CourseInspector n={n} ctx={ctx} />;
  return <ElementInspector n={n} ctx={ctx} />;
}

function InspectorEmpty({ view }: { view: WebView }) {
  const select = useUi((s) => s.select);
  const weakest = [...view.plo_threads].sort((a: PloThread, b: PloThread) => (a.strength ?? 0) - (b.strength ?? 0)).slice(0, 4);
  return (
    <div>
      <h2>Alignment web</h2>
      <p className="muted small">
        Select a program outcome to see where it is introduced, reinforced, and assessed; a course to see how its outcomes,
        assessments, and topics connect up to program outcomes and across to earlier and later courses; or a link to see why it
        is rated strong, moderate, or weak.
      </p>
      <h4>Weakest program outcomes</h4>
      <ul className="list">
        {weakest.map((t) => (
          <li key={t.plo_id}>
            <button className="link" onClick={() => select({ kind: "node", id: t.plo_id })}>{view.nodes.find((n) => n.id === t.plo_id)?.title ?? t.plo_key}</button>{" "}
            <Strength label={t.label} score={t.strength} /> <span className="muted small">{THREAD_TEXT[t.status]}</span>
          </li>
        ))}
      </ul>
      <h4 style={{ marginTop: 10 }}>Gaps ({view.gaps.length})</h4>
      <ul className="list small">
        {view.gaps.slice(0, 12).map((g) => (
          <li key={g.id}>
            <span className={`badge ${g.severity === "high" ? "rejected" : g.severity === "medium" ? "proposed" : ""}`}>{g.severity}</span>{" "}
            <button className="link" onClick={() => select({ kind: "node", id: g.source ?? g.entity_ids[0] })}>{g.message}</button>
          </li>
        ))}
        {view.gaps.length > 12 && <li className="muted">…and {view.gaps.length - 12} more (see Layers → gaps).</li>}
      </ul>
    </div>
  );
}
