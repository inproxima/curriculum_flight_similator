import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { ApiError, getJson, qs } from "../api/client";
import { useScenario, useScenarioMutations } from "../api/scenarioHooks";
import type { EntityDetail } from "../api/types";
import { useUi } from "../store/ui";

type Change = Record<string, unknown> & { op: string };

function useEntities(versionId: string | null, type: string) {
  return useQuery({
    queryKey: ["entities", versionId, type],
    enabled: !!versionId,
    queryFn: () => getJson<{ id: string; key: string; title: string }[]>(`/api/v1/versions/${versionId}/entities${qs({ type })}`),
  });
}

/** Semantic edits for the selected course. Every edit is previewed and applied to the scenario, never the baseline. */
export function ScenarioEditor({ detail }: { detail: EntityDetail }) {
  const { versionId, scenarioId, setScenario } = useUi();
  const scenario = useScenario(scenarioId);
  const m = useScenarioMutations(scenarioId);
  const [pending, setPending] = useState<{ change: Change; summary: string } | null>(null);
  const [assumption, setAssumption] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const e = detail.entity;
  const pl = detail.placements[0];
  const plos = useEntities(versionId, "program_outcome");
  const topics = useEntities(versionId, "topic");

  if (!scenarioId) {
    return (
      <div className="col">
        <p className="muted">Edits go into a scenario over the published baseline, which is never modified.</p>
        <input type="text" placeholder="Scenario title" aria-label="Scenario title" value={title} onChange={(ev) => setTitle(ev.target.value)} />
        <button
          className="primary"
          disabled={!title || !versionId}
          onClick={() => m.create.mutate({ base_version_id: versionId!, title }, { onSuccess: (s) => setScenario(s.id), onError: (x) => setErr(String(x)) })}
        >
          Create scenario from this version
        </button>
        {err && <p className="error small">{err}</p>}
      </div>
    );
  }
  const propose = (change: Change, summary: string) => {
    setErr(null);
    setPending({ change, summary });
  };
  const apply = () => {
    if (!pending || !scenario.data) return;
    const change = { ...pending.change, assumptions: assumption ? [assumption] : [] };
    m.addChange.mutate(
      { revision: scenario.data.revision, change },
      {
        onSuccess: () => { setPending(null); setAssumption(""); },
        onError: (x) => setErr(x instanceof ApiError ? `${x.message}${x.code === "stale_revision" ? " (reloaded)" : ""}` : String(x)),
      },
    );
  };
  return (
    <div className="col">
      <div className="info small">
        Editing scenario <strong>{scenario.data?.title}</strong> (rev {scenario.data?.revision}). Changes apply to the projection only.
      </div>
      {pending && (
        <div className="card" role="dialog" aria-label="Preview change">
          <h3>Preview</h3>
          <p>{pending.summary}</p>
          <input type="text" placeholder="Assumption behind this change (optional)" aria-label="Assumption" value={assumption} onChange={(ev) => setAssumption(ev.target.value)} />
          <div className="row" style={{ marginTop: 6 }}>
            <button className="primary" onClick={apply} disabled={m.addChange.isPending}>Apply to scenario</button>
            <button onClick={() => setPending(null)}>Cancel</button>
          </div>
        </div>
      )}
      {err && <p className="error small">{err}</p>}

      {e.type === "course" && (
        <>
          <MoveForm current={{ year: pl?.year ?? null, term: pl?.term ?? "unknown" }} onPropose={(year, term) =>
            propose({ op: "move_course", entity_id: e.id, year, term }, `Move ${e.key} from Y${pl?.year ?? "?"} ${pl?.term ?? "?"} to Y${year ?? "?"} ${term}. Card position on the map is not semantic; this changes the curriculum placement in the scenario.`)} />
          <section>
            <h4>Classification</h4>
            <div className="row wrap">
              {["required", "elective", "pathway_required"].map((c) => (
                <button key={c} className="small" disabled={pl?.classification === c}
                  onClick={() => propose({ op: "set_classification", entity_id: e.id, classification: c }, `Make ${e.key} ${c.replace("_", " ")}`)}>{c.replace("_", " ")}</button>
              ))}
            </div>
          </section>
          <RequirementForm current={detail.requirement_rules?.find((r) => r.kind === "prerequisite")?.source_text ?? ""} onPropose={(text) =>
            propose({ op: "modify_requirement", course_id: e.id, rule_kind: "prerequisite", text }, text ? `Set ${e.key} prerequisite to “${text}”` : `Remove ${e.key} prerequisite`)} />
          <section>
            <h4>Outcomes</h4>
            <ul className="list">
              {detail.contribution?.outcomes.map((o) => (
                <li key={o.outcome.id} className="small">
                  {o.outcome.key}: {o.outcome.statement_verbatim ?? o.outcome.title}
                  <div className="row">
                    <button className="small" onClick={() => propose({ op: "remove_outcome", entity_id: o.outcome.id }, `Remove outcome ${o.outcome.key} from ${e.key}`)}>Remove</button>
                    <ModifyOutcome onPropose={(statement) => propose({ op: "modify_outcome", entity_id: o.outcome.id, statement }, `Reword ${o.outcome.key} to “${statement}”`)} />
                  </div>
                </li>
              ))}
            </ul>
            <AddOutcomeForm plos={plos.data ?? []} onPropose={(statement, contributes) =>
              propose({ op: "add_outcome", course_id: e.id, statement, contributes }, `Add outcome to ${e.key}: “${statement}” contributing to ${contributes.map((c) => plos.data?.find((p) => p.id === c[0])?.key + " " + c[1]).join(", ") || "no program outcome"}`)} />
          </section>
          <section>
            <h4>Topics</h4>
            <div className="row wrap">
              {detail.contribution?.topics.map((t) => (
                <button key={t.topic.id} className="small" title="Remove this topic from the course"
                  onClick={() => propose({ op: "remove_topic", entity_id: t.topic.id, course_id: e.id }, `Remove topic '${t.topic.title}' from ${e.key}`)}>✕ {t.topic.title}</button>
              ))}
            </div>
            <AddTopicForm topics={topics.data ?? []} onPropose={(title, existing) =>
              propose({ op: "add_topic", course_id: e.id, title, existing_topic_id: existing || null }, `Add topic '${title}' to ${e.key}`)} />
          </section>
          <section>
            <h4>Assessments</h4>
            <ul className="list">
              {detail.contribution?.assessments.map((a) => (
                <li key={a.assessment.id} className="small">
                  {a.assessment.title} — week {String(a.details.timing_week ?? "?")}, {String(a.details.weight_percent ?? "?")}%
                  <div className="row wrap">
                    <button className="small" disabled={a.details.timing_week == null} title={a.details.timing_week == null ? "Week not documented: set an explicit week instead" : ""}
                      onClick={() => propose({ op: "change_assessment", entity_id: a.assessment.id, shift_weeks: 2 }, `Move ${a.assessment.title} two weeks later (week ${a.details.timing_week} → ${Number(a.details.timing_week) + 2})`)}>+2 weeks</button>
                    <WeekForm onPropose={(w) => propose({ op: "change_assessment", entity_id: a.assessment.id, timing_week: w }, `Set ${a.assessment.title} to week ${w}`)} />
                    <button className="small" onClick={() => propose({ op: "remove_assessment", entity_id: a.assessment.id }, `Remove assessment ${a.assessment.title}`)}>Remove</button>
                  </div>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h4>Delivery and workload</h4>
            <DeliveryForm onPropose={(f) => propose({ op: "change_delivery", course_id: e.id, delivery_format: f }, `Deliver ${e.key} as ${f}`)} />
            <WorkloadForm onPropose={(component, low, typical, high) =>
              propose({ op: "set_workload", entity_id: e.id, component, low, typical, high }, `Assume ${component} workload for ${e.key}: ${low}–${high} h per term (typical ${typical}). This is an assumption, not documented data.`)} />
          </section>
          <section>
            <h4>Remove course</h4>
            <button onClick={() => propose({ op: "remove_course", entity_id: e.id }, `Remove ${e.key} from the curriculum, including its outcomes and assessments. Dependent requisites will be re-evaluated.`)}>Remove {e.key}…</button>
          </section>
        </>
      )}
    </div>
  );
}

function MoveForm({ current, onPropose }: { current: { year: number | null; term: string }; onPropose: (y: number | null, t: string) => void }) {
  const [year, setYear] = useState<number | null>(current.year);
  const [term, setTerm] = useState(current.term);
  return (
    <section>
      <h4>Move to another term</h4>
      <div className="row wrap">
        <select aria-label="Year" value={year ?? ""} onChange={(e) => setYear(e.target.value ? Number(e.target.value) : null)}>
          {[1, 2, 3, 4].map((y) => <option key={y} value={y}>Year {y}</option>)}
        </select>
        <select aria-label="Term" value={term} onChange={(e) => setTerm(e.target.value)}>
          {["fall", "winter", "spring", "summer", "unknown"].map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <button className="small" disabled={year === current.year && term === current.term} onClick={() => onPropose(year, term)}>Preview move</button>
      </div>
    </section>
  );
}

function RequirementForm({ current, onPropose }: { current: string; onPropose: (t: string) => void }) {
  const [text, setText] = useState(current);
  return (
    <section>
      <h4>Prerequisite rule</h4>
      <div className="row"><input type="text" className="grow" aria-label="Prerequisite text" value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. SYN 210 and (SYN 211 or SYN 212)" />
        <button className="small" disabled={text === current} onClick={() => onPropose(text)}>Preview</button></div>
      <p className="muted small">AND/OR, “one of”, credit minimums, and “may be taken concurrently” are parsed explicitly; OR is never flattened.</p>
    </section>
  );
}

function ModifyOutcome({ onPropose }: { onPropose: (s: string) => void }) {
  const [open, setOpen] = useState(false);
  const [s, setS] = useState("");
  if (!open) return <button className="small" onClick={() => setOpen(true)}>Reword</button>;
  return <span className="row"><input type="text" aria-label="New statement" value={s} onChange={(e) => setS(e.target.value)} /><button className="small" disabled={!s} onClick={() => onPropose(s)}>Preview</button></span>;
}

function AddOutcomeForm({ plos, onPropose }: { plos: { id: string; key: string; title: string }[]; onPropose: (s: string, c: [string, string][]) => void }) {
  const [s, setS] = useState("");
  const [sel, setSel] = useState<Record<string, string>>({});
  return (
    <details>
      <summary>Add outcome</summary>
      <div className="col">
        <input type="text" aria-label="Outcome statement" placeholder="Outcome statement" value={s} onChange={(e) => setS(e.target.value)} />
        {plos.map((p) => (
          <label key={p.id} className="row small">
            <select aria-label={`Level for ${p.key}`} value={sel[p.id] ?? ""} onChange={(e) => setSel({ ...sel, [p.id]: e.target.value })}>
              <option value="">—</option><option value="introduce">I</option><option value="reinforce">R</option><option value="assess">A</option>
            </select>
            {p.title}
          </label>
        ))}
        <button className="small" disabled={!s} onClick={() => onPropose(s, Object.entries(sel).filter(([, v]) => v) as [string, string][])}>Preview</button>
      </div>
    </details>
  );
}

function AddTopicForm({ topics, onPropose }: { topics: { id: string; title: string }[]; onPropose: (title: string, existing: string | null) => void }) {
  const [existing, setExisting] = useState("");
  const [title, setTitle] = useState("");
  return (
    <div className="row wrap" style={{ marginTop: 4 }}>
      <select aria-label="Existing topic" value={existing} onChange={(e) => { setExisting(e.target.value); setTitle(topics.find((t) => t.id === e.target.value)?.title ?? ""); }}>
        <option value="">New topic…</option>
        {topics.map((t) => <option key={t.id} value={t.id}>{t.title}</option>)}
      </select>
      {!existing && <input type="text" aria-label="New topic title" placeholder="Topic title" value={title} onChange={(e) => setTitle(e.target.value)} />}
      <button className="small" disabled={!title} onClick={() => onPropose(title, existing || null)}>Preview add</button>
    </div>
  );
}

function WeekForm({ onPropose }: { onPropose: (w: number) => void }) {
  const [w, setW] = useState("");
  return <span className="row"><input type="number" min={1} max={16} aria-label="Week" style={{ width: 60 }} value={w} onChange={(e) => setW(e.target.value)} /><button className="small" disabled={!w} onClick={() => onPropose(Number(w))}>Set week</button></span>;
}

function DeliveryForm({ onPropose }: { onPropose: (f: string) => void }) {
  const [f, setF] = useState("");
  return (
    <div className="row"><select aria-label="Delivery format" value={f} onChange={(e) => setF(e.target.value)}>
      <option value="">Delivery format…</option>{["in-person", "blended", "online", "flipped", "lab-intensive"].map((x) => <option key={x}>{x}</option>)}
    </select><button className="small" disabled={!f} onClick={() => onPropose(f)}>Preview</button></div>
  );
}

function WorkloadForm({ onPropose }: { onPropose: (c: string, l: number, t: number, h: number) => void }) {
  const [c, setC] = useState("reading");
  const [v, setV] = useState({ low: "", typical: "", high: "" });
  const ok = v.low !== "" && v.typical !== "" && v.high !== "" && Number(v.low) <= Number(v.typical) && Number(v.typical) <= Number(v.high);
  return (
    <div className="row wrap" style={{ marginTop: 4 }}>
      <select aria-label="Workload component" value={c} onChange={(e) => setC(e.target.value)}>
        {["contact", "reading", "practice", "assignment_prep", "assessment", "faculty_prep", "marking", "development"].map((x) => <option key={x}>{x}</option>)}
      </select>
      {(["low", "typical", "high"] as const).map((k) => (
        <input key={k} type="number" min={0} aria-label={`${k} hours`} placeholder={k} style={{ width: 64 }} value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} />
      ))}
      <button className="small" disabled={!ok} onClick={() => onPropose(c, Number(v.low), Number(v.typical), Number(v.high))}>Preview</button>
    </div>
  );
}
