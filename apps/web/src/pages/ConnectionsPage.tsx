import { useMemo, useState } from "react";
import { useWeb, type WebEdge, type WebNode, type WebView } from "../api/webHooks";
import { Conn, LinkReview } from "../components/Inspector";
import { MapConnections } from "../components/LayersPanel";
import { useUi } from "../store/ui";
import {
  MAP_STATES,
  acrossCell,
  bestLevel,
  connIndex,
  linksBetween,
  mapState,
  membersOf,
  ownerIds,
  proposedOnly,
  summarize,
  type ConnIndex,
  type MapKind,
  type MapState,
} from "../web/connections";
import { CONNECTION_GLYPH, CONNECTION_HELP, CONNECTION_LEVELS, CONNECTION_TEXT, PROVENANCE_TEXT, type ConnectionLevel } from "../web/webStyle";

type Tab = "plo" | "assess" | "topics" | "across";
const TABS: { key: Tab; label: string }[] = [
  { key: "plo", label: "Outcomes → program outcomes" },
  { key: "assess", label: "Assessments → outcomes" },
  { key: "topics", label: "Topics → outcomes" },
  { key: "across", label: "Across courses (earlier ↔ later)" },
];
const STATE_TEXT: Record<MapState, string> = {
  direct: "Direct",
  strong: "Strong",
  partial: "Partial / surmised",
  none: "No connection found",
  unmapped: "Not mapped yet",
};

interface Picked {
  title: string;
  edges: WebEdge[]; // resolved against the live payload by id (a decision refetches the web)
  note?: string;
}

/** Every course outcome, assessment and topic against what it should serve, coloured by how directly it connects.
 *  Built from the same payload as the Web (full detail), so it always agrees with it. */
export function ConnectionsPage() {
  const { versionId, scenarioId, select } = useUi();
  const web = useWeb(versionId, { scenarioId, detail: "full" });
  const view = web.data;
  const ix = useMemo(() => (view ? connIndex(view) : null), [view]);
  const [tab, setTab] = useState<Tab>("plo");
  const [courseId, setCourseId] = useState<string>("");
  const [picked, setPicked] = useState<Picked | null>(null);
  if (!versionId) return <div className="page"><p className="muted">Select a program and a curriculum version above.</p></div>;
  if (!view || !ix) return <div className="page">{web.isError ? <p className="error">{(web.error as Error).message}</p> : <p className="muted">Loading…</p>}</div>;
  const courses = courseId ? ix.courses.filter((c) => c.id === courseId) : ix.courses;
  const reviewBlocked = scenarioId ? "Decisions change the draft curriculum; leave the scenario first." : view.curriculum_version.status !== "draft" ? "Published versions are read-only; open a draft to review." : null;
  return (
    <div className="page connections-page">
      <div className="row wrap" style={{ alignItems: "flex-start" }}>
        <div className="grow" style={{ minWidth: 280 }}>
          <h2>Connection map {scenarioId && <span className="badge modified">scenario projection</span>}</h2>
          <p className="small muted" style={{ maxWidth: 760 }}>
            How every course outcome, assessment and topic connects to what it should serve. Colour and dots show how direct each
            connection is; links surmised by the AI are marked <strong>AI?</strong> until someone accepts them. <span className="map-chip none">✗</span> means the
            AI examined the element and found no connection; <span className="map-chip unmapped">?</span> means it has not been mapped yet.
          </p>
        </div>
        <div className="card small" style={{ minWidth: 260 }}>
          <label className="row">
            Course{" "}
            <select value={courseId} onChange={(e) => { setCourseId(e.target.value); setPicked(null); }} aria-label="Course">
              <option value="">All courses</option>
              {ix.courses.map((c) => <option key={c.id} value={c.id}>{c.key} — {c.title}</option>)}
            </select>
          </label>
          <MapConnections view={view} courseId={courseId || null} />
        </div>
      </div>
      <Summary view={view} />
      <ConnLegend />
      <div role="tablist" className="row wrap" style={{ margin: "8px 0" }} aria-label="Connection views">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} className={tab === t.key ? "primary" : ""} onClick={() => { setTab(t.key); setPicked(null); }}>
            {t.label}
          </button>
        ))}
      </div>
      <div className="conn-layout">
        <div className="conn-main" role="tabpanel" aria-label={TABS.find((t) => t.key === tab)?.label}>
          {tab === "plo" && <PloMatrix view={view} ix={ix} courses={courses} onPick={setPicked} />}
          {tab === "assess" && courses.map((c) => <CourseMatrix key={c.id} view={view} ix={ix} course={c} type="assessment" onPick={setPicked} />)}
          {tab === "topics" && courses.map((c) => <CourseMatrix key={c.id} view={view} ix={ix} course={c} type="topic" onPick={setPicked} />)}
          {tab === "across" && courses.map((c) => <AcrossMatrix key={c.id} view={view} ix={ix} course={c} onPick={setPicked} />)}
        </div>
        {picked && (
          <aside className="conn-detail card small" aria-label="Selected connection">
            <div className="row">
              <strong className="grow">{picked.title}</strong>
              <button className="link small" onClick={() => setPicked(null)} aria-label="Close details">✕</button>
            </div>
            {picked.note && <p className="muted">{picked.note}</p>}
            {picked.edges.map((p) => view.edges.find((e) => e.id === p.id)).filter((e): e is WebEdge => !!e).map((e) => (
              <div key={e.id} className="conn-edge">
                <div className="row wrap">
                  <button className="link" onClick={() => select({ kind: "node", id: e.source })}>{ix.byId.get(e.source)?.key}</button>
                  <span className="muted">{e.type.replace(/_/g, " ")}</span>
                  <button className="link" onClick={() => select({ kind: "node", id: e.target })}>{ix.byId.get(e.target)?.key}</button>
                </div>
                <div className="row wrap" style={{ marginTop: 2 }}>
                  <Conn level={e.connection} />
                  <span className={`badge ${e.provenance === "documented" ? "documented" : e.provenance === "proposed" ? "proposed" : "inferred"}`} title={PROVENANCE_TEXT[e.provenance]}>
                    {e.provenance === "proposed" ? "AI? proposed" : e.provenance.replace("_", " ")}
                  </span>
                </div>
                {e.rationale && <p className="quote">{e.rationale}</p>}
                {e.provenance === "proposed" && e.key && <LinkReview edge={e} disabled={reviewBlocked} />}
              </div>
            ))}
          </aside>
        )}
      </div>
    </div>
  );
}

function StateBar({ counts, label }: { counts: Record<MapState, number> & { total: number }; label: string }) {
  if (!counts.total) return null;
  return (
    <div className="conn-summary-row">
      <div className="small"><strong>{label}</strong> <span className="muted">({counts.total})</span></div>
      <div className="conn-bar" role="img" aria-label={`${label}: ${MAP_STATES.map((s) => `${counts[s]} ${STATE_TEXT[s].toLowerCase()}`).join(", ")}`}>
        {MAP_STATES.map((s) => counts[s] > 0 && <span key={s} className={`seg seg-${s}`} style={{ flexGrow: counts[s] }} title={`${STATE_TEXT[s]}: ${counts[s]}`} />)}
      </div>
      <div className="small muted">
        {MAP_STATES.filter((s) => counts[s] > 0).map((s) => `${counts[s]} ${s === "none" ? "none found" : s === "unmapped" ? "not mapped" : s}`).join(" · ")}
      </div>
    </div>
  );
}

function Summary({ view }: { view: WebView }) {
  const rows: [string, string, MapKind][] = [
    ["Course outcomes → program outcomes", "course_outcome", "contributes_to"],
    ["Assessments → course outcomes", "assessment", "assesses"],
    ["Topics → course outcomes", "topic", "supports"],
    ["Topics ← earlier / prerequisite courses", "topic", "prepared_by"],
    ["Topics → later courses", "topic", "prepares_for"],
  ];
  return (
    <div className="conn-summary">
      {rows.map(([label, type, kind]) => <StateBar key={label} label={label} counts={summarize(view.nodes, type, kind)} />)}
    </div>
  );
}

function ConnLegend() {
  return (
    <div className="row wrap small conn-legend" aria-label="Legend">
      {CONNECTION_LEVELS.map((l) => (
        <span key={l} className="row" title={CONNECTION_HELP[l]}>
          <span className={`legend-cell conn-cell-${l}`} aria-hidden>{CONNECTION_GLYPH[l]}</span> {CONNECTION_TEXT[l]}
        </span>
      ))}
      <span className="row"><span className="legend-cell conn-cell-partial" aria-hidden>●○○<sup>AI?</sup></span> surmised by the AI, not yet reviewed</span>
      <span className="row"><span className="map-chip none">✗</span> AI found no connection</span>
      <span className="row"><span className="map-chip unmapped">?</span> not mapped</span>
    </div>
  );
}

/** A heat-map cell: the best level among the links, with "AI?" when they are all unreviewed proposals. */
function Cell({ edges, label, onPick }: { edges: WebEdge[]; label: string; onPick: (p: Picked) => void }) {
  const lvl = bestLevel(edges);
  if (!lvl) return <td className="conn-cell" />;
  const ai = proposedOnly(edges);
  return (
    <td className={`conn-cell conn-cell-${lvl}`}>
      <button onClick={() => onPick({ title: label, edges })} aria-label={`${label}: ${CONNECTION_TEXT[lvl]}${ai ? ", proposed by the AI" : ""}`} title={`${CONNECTION_TEXT[lvl]}${ai ? " (AI? proposed)" : ""}`}>
        {CONNECTION_GLYPH[lvl]}
        {ai && <sup>AI?</sup>}
      </button>
    </td>
  );
}

/** The row's own verdict for the direction this matrix shows. */
function RowState({ n, kind, onPick }: { n: WebNode; kind: MapKind; onPick: (p: Picked) => void }) {
  const s = mapState(n, kind);
  const m = n.mapping?.[kind];
  if (!s) return <td className="muted small">—</td>;
  if (s === "none")
    return (
      <td className="small conn-state">
        <button className="link" onClick={() => onPick({ title: n.key, edges: [], note: `The AI found no connection: ${m?.reason ?? ""}` })}>
          <span className="map-chip none">✗ none found</span>
        </button>
      </td>
    );
  if (s === "unmapped") return <td className="small conn-state"><span className="map-chip unmapped" title="Not examined by the AI mapping yet">? not mapped</span></td>;
  return <td className="small conn-state"><Conn level={s as ConnectionLevel} short /></td>;
}

function rowClass(n: WebNode, kind: MapKind) {
  const s = mapState(n, kind);
  return s === "none" ? "conn-row-none" : s === "unmapped" ? "conn-row-unmapped" : "";
}

function PloMatrix({ view, ix, courses, onPick }: { view: WebView; ix: ConnIndex; courses: WebNode[]; onPick: (p: Picked) => void }) {
  const plos = view.nodes.filter((n) => n.type === "program_outcome").sort((a, b) => a.key.localeCompare(b.key, undefined, { numeric: true }));
  return (
    <div className="conn-scroll">
      <table className="data conn-matrix" aria-label="Course outcome by program outcome connections">
        <thead>
          <tr>
            <th scope="col">Course outcome</th>
            {plos.map((p) => <th key={p.id} scope="col" title={p.title}>{p.key}</th>)}
            <th scope="col">Status</th>
          </tr>
        </thead>
        <tbody>
          {courses.map((c) => {
            const clos = membersOf(view, c.id, "course_outcome");
            return [
              <tr key={c.id} className="conn-course-row"><th colSpan={plos.length + 2} scope="colgroup">{c.key} — {c.title} <span className="muted small">{c.year ? `Y${c.year}` : "Y?"} {c.term ?? ""}</span></th></tr>,
              clos.length === 0 ? (
                <tr key={`${c.id}-none`}><td colSpan={plos.length + 2} className="muted small">No course outcomes documented.</td></tr>
              ) : (
                clos.map((o) => (
                  <tr key={`${c.id}-${o.id}`} className={rowClass(o, "contributes_to")}>
                    <th scope="row" className="conn-rowhead" title={o.title}>{o.key}<div className="muted small clip">{o.title}</div></th>
                    {plos.map((p) => <Cell key={p.id} edges={linksBetween(ix, o.id, p.id, "contributes_to")} label={`${o.key} → ${p.key}`} onPick={onPick} />)}
                    <RowState n={o} kind="contributes_to" onPick={onPick} />
                  </tr>
                ))
              ),
            ];
          })}
        </tbody>
      </table>
    </div>
  );
}

function CourseMatrix({ view, ix, course, type, onPick }: { view: WebView; ix: ConnIndex; course: WebNode; type: "assessment" | "topic"; onPick: (p: Picked) => void }) {
  const clos = membersOf(view, course.id, "course_outcome");
  const rows = membersOf(view, course.id, type);
  const kind: MapKind = type === "assessment" ? "assesses" : "supports";
  const rel = kind;
  if (!rows.length) return null;
  return (
    <section className="conn-section">
      <h3>{course.key} — {course.title}</h3>
      {!clos.length ? (
        <p className="muted small">No course outcomes documented, so {type === "assessment" ? "assessments" : "topics"} cannot be connected.</p>
      ) : (
        <div className="conn-scroll">
          <table className="data conn-matrix" aria-label={`${course.key}: ${type === "assessment" ? "assessment" : "topic"} by course outcome`}>
            <thead>
              <tr>
                <th scope="col">{type === "assessment" ? "Assessment" : "Topic"}</th>
                {clos.map((o) => <th key={o.id} scope="col" title={o.title}>{o.key.replace(`${course.key.replace(" ", "")}-`, "")}</th>)}
                <th scope="col">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className={rowClass(r, kind)}>
                  <th scope="row" className="conn-rowhead" title={r.title}>
                    {r.title}
                    {type === "assessment" && <div className="muted small">{String((r.details as { format?: string }).format ?? "")} {(r.details as { weight_percent?: number }).weight_percent != null ? `· ${(r.details as { weight_percent?: number }).weight_percent}%` : ""}</div>}
                  </th>
                  {clos.map((o) => <Cell key={o.id} edges={linksBetween(ix, r.id, o.id, rel)} label={`${r.title} → ${o.key}`} onPick={onPick} />)}
                  <RowState n={r} kind={kind} onPick={onPick} />
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

/** A course's topics and outcomes against every other course: ← it builds on an earlier course's element,
 *  → an element of a later course builds on it. */
function AcrossMatrix({ view, ix, course, onPick }: { view: WebView; ix: ConnIndex; course: WebNode; onPick: (p: Picked) => void }) {
  const rows = [...membersOf(view, course.id, "topic"), ...membersOf(view, course.id, "course_outcome")];
  if (!rows.length) return null;
  const pos = ix.courses.findIndex((c) => c.id === course.id);
  return (
    <section className="conn-section">
      <h3>{course.key} — {course.title}</h3>
      <div className="conn-scroll">
        <table className="data conn-matrix conn-across" aria-label={`${course.key}: topics and outcomes across courses`}>
          <thead>
            <tr>
              <th scope="col">Topic / outcome</th>
              <th scope="col" title="Builds on an earlier or prerequisite course">← earlier</th>
              {ix.courses.map((c, i) => (
                <th key={c.id} scope="col" className={i === pos ? "own" : i < pos ? "before" : "after"} title={c.title}>
                  <span className="vertical">{c.key}</span>
                </th>
              ))}
              <th scope="col" title="A later course builds on it">later →</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <th scope="row" className="conn-rowhead" title={r.title}>
                  {r.type === "topic" ? r.title : r.key}
                  <div className="muted small">{r.type === "topic" ? "topic" : <span className="clip">{r.title}</span>}</div>
                </th>
                <RowState n={r} kind="prepared_by" onPick={onPick} />
                {ix.courses.map((c) => {
                  if (ownerIds(r).includes(c.id)) return <td key={c.id} className="own" aria-label="this course" />;
                  const { after, before } = acrossCell(ix, r.id, c.id);
                  const edges = [...before, ...after];
                  const lvl = bestLevel(edges);
                  if (!lvl) return <td key={c.id} className="conn-cell" />;
                  const arrow = before.length && after.length ? "↔" : before.length ? "←" : "→";
                  const ai = proposedOnly(edges);
                  const label = `${r.type === "topic" ? r.title : r.key} ${arrow} ${c.key}`;
                  return (
                    <td key={c.id} className={`conn-cell conn-cell-${lvl}`}>
                      <button onClick={() => onPick({ title: label, edges })} aria-label={`${label}: ${CONNECTION_TEXT[lvl]}${ai ? ", proposed by the AI" : ""}`} title={`${before.length ? "Builds on" : "Prepares for"} ${c.key}: ${CONNECTION_TEXT[lvl]}${ai ? " (AI? proposed)" : ""}`}>
                        {arrow}
                        <span className="dots">{CONNECTION_GLYPH[lvl]}</span>
                        {ai && <sup>AI?</sup>}
                      </button>
                    </td>
                  );
                })}
                <RowState n={r} kind="prepares_for" onPick={onPick} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}
