import { useMemo, useState } from "react";
import type { WebEdge, WebNode, WebView } from "../api/webHooks";
import { useScenarioEdit } from "../web/useScenarioEdit";

type Props = { node?: WebNode; edge?: WebEdge; view: WebView };

const LEVELS = ["", "introduce", "reinforce", "assess"] as const;
const FORMATS = ["exam", "quiz", "assignment", "written assignment", "lab report", "group project", "oral presentation", "poster", "portfolio", "reflective journal", "thesis"];

/** Direct what-if edits for the selected element. Every edit goes into a scenario; the baseline never changes. */
export function QuickEdit({ node, edge, view }: Props) {
  const { apply, error, busy, scenario } = useScenarioEdit();
  const [done, setDone] = useState<string | null>(null);
  const run = async (change: Record<string, unknown>, label: string) => {
    const s = await apply(change);
    if (s) setDone(`${label} — applied to “${s.title}”. Press Simulate to see the ripple.`);
  };
  const body = edge ? <EdgeEdit edge={edge} run={run} /> : node ? <NodeEdit node={node} view={view} run={run} /> : null;
  if (!body) return null;
  return (
    <details className="card small quick-edit" style={{ marginTop: 10 }}>
      <summary><strong>Change this…</strong> <span className="muted">{scenario ? `into scenario “${scenario.title}”` : "creates a scenario"}</span></summary>
      <div className="col" style={{ marginTop: 6 }} aria-busy={busy}>{body}</div>
      {error && <div className="error">{error}</div>}
      {done && <div className="muted">{done}</div>}
    </details>
  );
}

type Run = (change: Record<string, unknown>, label: string) => void;

function EdgeEdit({ edge, run }: { edge: WebEdge; run: Run }) {
  const [s, setS] = useState(edge.strength?.asserted ?? edge.strength?.effective ?? 0.7);
  if (!edge.key) return <p className="muted">Bundled links are edited through their individual links.</p>;
  const carriesStrength = ["contributes_to", "assesses", "supports", "prepares_for"].includes(edge.type);
  return (
    <>
      {carriesStrength && (
        <div className="row wrap">
          <label className="row">Strength <input type="range" min={0} max={1} step={0.05} value={s} onChange={(e) => setS(Number(e.target.value))} aria-label="New strength" /></label>
          <span className="mono">{s.toFixed(2)}</span>
          <button className="small" onClick={() => run({ op: "set_relationship_strength", key: edge.key, strength: s }, `Strength set to ${s.toFixed(2)}`)}>Set strength</button>
        </div>
      )}
      <button className="small" onClick={() => run({ op: "remove_relationship", key: edge.key }, "Link removed")}>Remove this link</button>
    </>
  );
}

function members(view: WebView, courseIds: string[], type: string) {
  const ids = new Set(courseIds);
  return view.nodes.filter((n) => n.type === type && (n.course_ids ?? []).some((c) => ids.has(c)));
}

function NodeEdit({ node, view, run }: { node: WebNode; view: WebView; run: Run }) {
  const plos = useMemo(() => view.nodes.filter((n) => n.type === "program_outcome"), [view.nodes]);
  switch (node.type) {
    case "course_outcome":
      return <CloEdit node={node} view={view} plos={plos} run={run} />;
    case "assessment":
      return <AssessmentEdit node={node} view={view} run={run} />;
    case "topic":
      return <TopicEdit node={node} view={view} run={run} />;
    case "program_outcome":
      return <PloEdit node={node} run={run} />;
    case "course":
      return <CourseEdit node={node} view={view} plos={plos} run={run} />;
    default:
      return null;
  }
}

function CloEdit({ node, view, plos, run }: { node: WebNode; view: WebView; plos: WebNode[]; run: Run }) {
  const current = useMemo(() => {
    const m: Record<string, string> = {};
    for (const e of view.edges) if (e.type === "contributes_to" && e.source === node.id) m[e.target] = e.level ?? "reinforce";
    return m;
  }, [view.edges, node.id]);
  const [stmt, setStmt] = useState(node.title);
  const [levels, setLevels] = useState<Record<string, string>>(current);
  const contributes = Object.entries(levels).filter(([, v]) => v).map(([k, v]) => [k, v]);
  return (
    <>
      <label className="col">Statement<textarea rows={2} value={stmt} onChange={(e) => setStmt(e.target.value)} aria-label="Outcome statement" /></label>
      <div className="small muted">Program outcomes it contributes to (I / R / A):</div>
      <div className="plo-grid">
        {plos.map((p) => (
          <label key={p.id} className="row small">
            <select value={levels[p.id] ?? ""} onChange={(e) => setLevels({ ...levels, [p.id]: e.target.value })} aria-label={`Level for ${p.key}`}>
              {LEVELS.map((l) => <option key={l} value={l}>{l ? l[0].toUpperCase() : "—"}</option>)}
            </select>
            <span title={p.title}>{p.title}</span>
          </label>
        ))}
      </div>
      <div className="row wrap">
        <button className="small primary" onClick={() => run({ op: "modify_outcome", entity_id: node.id, statement: stmt !== node.title ? stmt : null, contributes }, "Outcome changed")}>Apply outcome change</button>
        <button className="small" onClick={() => run({ op: "remove_outcome", entity_id: node.id }, `${node.key} removed`)}>Remove outcome</button>
      </div>
    </>
  );
}

function AssessmentEdit({ node, view, run }: { node: WebNode; view: WebView; run: Run }) {
  const d = node.details as { format?: string; weight_percent?: number; timing_week?: number; documented_hours?: number };
  const clos = members(view, node.course_ids ?? [], "course_outcome");
  const assessed = new Set(view.edges.filter((e) => e.type === "assesses" && e.source === node.id).map((e) => e.target));
  const [f, setF] = useState({ title: node.title, format: d.format ?? "", weight: d.weight_percent ?? "", week: d.timing_week ?? "", hours: d.documented_hours ?? "" });
  const [sel, setSel] = useState<Set<string>>(assessed);
  const num = (v: unknown) => (v === "" || v == null ? null : Number(v));
  return (
    <>
      <label className="row">Title <input type="text" className="grow" value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} aria-label="Assessment title" /></label>
      <div className="row wrap">
        <select value={f.format} onChange={(e) => setF({ ...f, format: e.target.value })} aria-label="Format">
          <option value="">format…</option>
          {[...new Set([f.format, ...FORMATS])].filter(Boolean).map((x) => <option key={x}>{x}</option>)}
        </select>
        <label className="row">Weight % <input type="number" min={0} max={100} style={{ width: 60 }} value={f.weight} onChange={(e) => setF({ ...f, weight: e.target.value })} aria-label="Weight percent" /></label>
        <label className="row">Week <input type="number" min={1} max={16} style={{ width: 52 }} value={f.week} onChange={(e) => setF({ ...f, week: e.target.value })} aria-label="Week" /></label>
        <label className="row">Hours <input type="number" min={0} style={{ width: 56 }} value={f.hours} onChange={(e) => setF({ ...f, hours: e.target.value })} aria-label="Documented hours" /></label>
      </div>
      <div className="small muted">Assesses:</div>
      {clos.map((c) => (
        <label key={c.id} className="row small">
          <input type="checkbox" checked={sel.has(c.id)} onChange={() => { const n = new Set(sel); if (n.has(c.id)) n.delete(c.id); else n.add(c.id); setSel(n); }} />
          {c.key} <span className="muted">{c.title}</span>
        </label>
      ))}
      <div className="row wrap">
        <button className="small primary" onClick={() => run({
          op: "change_assessment", entity_id: node.id,
          title: f.title !== node.title ? f.title : null,
          format: f.format && f.format !== d.format ? f.format : null,
          weight_percent: num(f.weight) !== (d.weight_percent ?? null) ? num(f.weight) : null,
          timing_week: num(f.week) !== (d.timing_week ?? null) ? num(f.week) : null,
          documented_hours: num(f.hours) !== (d.documented_hours ?? null) ? num(f.hours) : null,
          assesses: [...sel].sort().join() !== [...assessed].sort().join() ? [...sel] : null,
        }, "Assessment changed")}>Apply assessment change</button>
        <button className="small" onClick={() => run({ op: "remove_assessment", entity_id: node.id }, `${node.title} removed`)}>Remove assessment</button>
      </div>
    </>
  );
}

function TopicEdit({ node, view, run }: { node: WebNode; view: WebView; run: Run }) {
  const clos = members(view, node.course_ids ?? [], "course_outcome");
  const current = new Set(view.edges.filter((e) => e.type === "supports" && e.source === node.id).map((e) => e.target));
  const [sel, setSel] = useState<Set<string>>(current);
  return (
    <>
      <div className="small muted">Course outcomes this topic helps students reach:</div>
      {clos.map((c) => (
        <label key={c.id} className="row small">
          <input type="checkbox" checked={sel.has(c.id)} onChange={() => { const n = new Set(sel); if (n.has(c.id)) n.delete(c.id); else n.add(c.id); setSel(n); }} />
          {c.key} <span className="muted">{c.title}</span>
        </label>
      ))}
      <div className="row wrap">
        <button className="small primary" onClick={() => run({ op: "modify_topic", entity_id: node.id, supports: [...sel] }, "Topic links changed")}>Apply</button>
        {(node.course_ids ?? []).map((c) => (
          <button key={c} className="small" onClick={() => run({ op: "remove_topic", entity_id: node.id, course_id: c }, `Topic removed from ${view.nodes.find((n) => n.id === c)?.key}`)}>
            Remove from {view.nodes.find((n) => n.id === c)?.key}
          </button>
        ))}
      </div>
    </>
  );
}

function PloEdit({ node, run }: { node: WebNode; run: Run }) {
  const d = node.details as { label?: string; statement_verbatim?: string };
  const [label, setLabel] = useState(d.label ?? node.title.split(": ").slice(1).join(": "));
  const [stmt, setStmt] = useState(d.statement_verbatim ?? "");
  return (
    <>
      <label className="row">Label <input type="text" className="grow" value={label} onChange={(e) => setLabel(e.target.value)} aria-label="Program outcome label" /></label>
      <label className="col">Statement<textarea rows={2} value={stmt} onChange={(e) => setStmt(e.target.value)} aria-label="Program outcome statement" /></label>
      <div className="row wrap">
        <button className="small primary" onClick={() => run({ op: "modify_program_outcome", entity_id: node.id, label, statement: stmt }, "Program outcome changed")}>Apply</button>
        <button className="small" onClick={() => run({ op: "remove_program_outcome", entity_id: node.id }, `${node.key} removed`)}>Remove program outcome</button>
      </div>
    </>
  );
}

function CourseEdit({ node, view, plos, run }: { node: WebNode; view: WebView; plos: WebNode[]; run: Run }) {
  const [title, setTitle] = useState(node.title);
  const [a, setA] = useState({ title: "", format: "group project", weight: "20", week: "", assesses: "" });
  const [o, setO] = useState({ statement: "", plo: "", level: "reinforce" });
  const clos = members(view, [node.id], "course_outcome");
  return (
    <>
      <label className="row">Title <input type="text" className="grow" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Course title" />
        <button className="small" disabled={title === node.title} onClick={() => run({ op: "modify_course", entity_id: node.id, title }, "Course renamed")}>Rename</button></label>
      <fieldset className="col">
        <legend className="small">Add a learning outcome</legend>
        <input type="text" placeholder="Outcome statement" value={o.statement} onChange={(e) => setO({ ...o, statement: e.target.value })} aria-label="New outcome statement" />
        <div className="row wrap">
          <select value={o.plo} onChange={(e) => setO({ ...o, plo: e.target.value })} aria-label="Program outcome for new outcome">
            <option value="">program outcome…</option>{plos.map((p) => <option key={p.id} value={p.id}>{p.title}</option>)}
          </select>
          <select value={o.level} onChange={(e) => setO({ ...o, level: e.target.value })} aria-label="Level for new outcome">
            {LEVELS.slice(1).map((l) => <option key={l}>{l}</option>)}
          </select>
          <button className="small" disabled={!o.statement} onClick={() => run({ op: "add_outcome", course_id: node.id, statement: o.statement, contributes: o.plo ? [[o.plo, o.level]] : [] }, "Outcome added")}>Add outcome</button>
        </div>
      </fieldset>
      <fieldset className="col">
        <legend className="small">Add an assessment or assignment</legend>
        <input type="text" placeholder="Title, e.g. Team presentation" value={a.title} onChange={(e) => setA({ ...a, title: e.target.value })} aria-label="New assessment title" />
        <div className="row wrap">
          <select value={a.format} onChange={(e) => setA({ ...a, format: e.target.value })} aria-label="New assessment format">{FORMATS.map((x) => <option key={x}>{x}</option>)}</select>
          <label className="row">% <input type="number" style={{ width: 52 }} value={a.weight} onChange={(e) => setA({ ...a, weight: e.target.value })} aria-label="New assessment weight" /></label>
          <label className="row">wk <input type="number" style={{ width: 48 }} value={a.week} onChange={(e) => setA({ ...a, week: e.target.value })} aria-label="New assessment week" /></label>
          <select value={a.assesses} onChange={(e) => setA({ ...a, assesses: e.target.value })} aria-label="Outcome the new assessment assesses">
            <option value="">assesses…</option>{clos.map((c) => <option key={c.id} value={c.id}>{c.key}</option>)}
          </select>
          <button className="small" disabled={!a.title} onClick={() => run({
            op: "add_assessment", course_id: node.id, title: a.title, format: a.format,
            weight_percent: a.weight ? Number(a.weight) : null, timing_week: a.week ? Number(a.week) : null,
            assesses: a.assesses ? [a.assesses] : [],
          }, "Assessment added")}>Add assessment</button>
        </div>
      </fieldset>
      <button className="small" onClick={() => run({ op: "remove_course", entity_id: node.id }, `${node.key} removed`)}>Remove course</button>
      <p className="muted">More course edits (move term, prerequisites, workload) are in Sequence → Scenario edit.</p>
    </>
  );
}
