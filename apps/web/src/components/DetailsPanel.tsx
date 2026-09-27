import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { getJson } from "../api/client";
import { useDownstream, useEntity, usePrior, useRelationship } from "../api/hooks";
import type { Downstream, EntityDetail, PriorLearning } from "../api/types";
import { CLASS_LABEL, TERM_LABEL, TYPE_LABEL } from "../lib/graphStyle";
import { perf } from "../lib/perf";
import { useUi } from "../store/ui";
import { EvidenceChip, FieldEvidence } from "./Evidence";
import { ScenarioEditor } from "./ScenarioEditor";

export function BasisBadge({ basis, review }: { basis: string; review?: string }) {
  const documented = basis === "explicit_statement" || basis === "documented";
  return (
    <>
      <span className={`badge ${documented ? "documented" : "inferred"}`}>
        {documented ? "Documented" : basis === "assumption" ? "Assumption" : basis === "mixed" ? "Mixed" : "Inferred"}
      </span>
      {review && review !== "accepted" && <span className={`badge ${review}`}>{review}</span>}
    </>
  );
}

const EXPOSURE_LABEL: Record<string, string> = {
  required_path: "Required path",
  pathway_dependent: "Pathway-dependent",
  elective: "Elective exposure",
  unknown: "Unknown exposure",
};
const RULE_STATUS: Record<string, string> = {
  satisfied_for_all: "Satisfied for all students",
  conditional: "Conditional (elective/pathway)",
  unknown: "Cannot be determined",
  unsatisfied: "Not satisfiable",
};

export function DetailsPanel() {
  const { versionId, scenarioId, selection, filters } = useUi();
  if (!selection) {
    return (
      <div className="muted">
        <p>Select a course, outcome, or relationship on the map.</p>
        <p>Shift-click to select several. Use <strong>+</strong> on a course card to reveal its outcomes, topics, and assessments.</p>
      </div>
    );
  }
  if (selection.kind === "edge") return <EdgePanel versionId={versionId} scenarioId={scenarioId} edgeKey={selection.key} />;
  return <NodePanel key={selection.id} versionId={versionId} scenarioId={scenarioId} entityId={selection.id} pathway={filters.pathway} />;
}

function NodePanel({ versionId, scenarioId, entityId, pathway }: { versionId: string | null; scenarioId: string | null; entityId: string; pathway: string | null }) {
  const t0 = performance.now();
  const { data, isLoading, error } = useEntity(versionId, entityId, scenarioId);
  useEffect(() => {
    if (data) perf.record("selection", performance.now() - t0, { entity: entityId });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data]);
  if (isLoading) return <p className="muted">Loading…</p>;
  if (error || !data) return <p className="error">{String(error ?? "Not found in this version")}</p>;
  const e = data.entity;
  return (
    <div>
      <div className="row wrap" style={{ marginBottom: 6 }}>
        <span className="badge">{e.type.replace("_", " ")}</span>
        {e.is_synthetic && <span className="badge synthetic">Synthetic fixture</span>}
        <span className="badge" title="Revision of this entity used by the selected version">rev {e.revision_number}</span>
        <span className="badge">origin: {e.origin.replace("_", " ")}</span>
      </div>
      <h2>{e.key !== e.title ? `${e.key} — ${e.title}` : e.title}</h2>
      <Tabs.Root className="tabs" defaultValue="details">
        <Tabs.List aria-label="Selected entity">
          <Tabs.Trigger value="details">Details</Tabs.Trigger>
          <Tabs.Trigger value="prior">Expected preparation</Tabs.Trigger>
          {e.type === "course" && <Tabs.Trigger value="contribution">Contribution</Tabs.Trigger>}
          <Tabs.Trigger value="downstream">Future use</Tabs.Trigger>
          {e.type === "course" && <Tabs.Trigger value="scenario">Scenario edit</Tabs.Trigger>}
        </Tabs.List>
        <Tabs.Content value="details"><Details d={data} /></Tabs.Content>
        <Tabs.Content value="prior"><PriorTab versionId={versionId} scenarioId={scenarioId} entityId={entityId} pathway={pathway} /></Tabs.Content>
        {e.type === "course" && <Tabs.Content value="contribution"><ContributionTab d={data} /></Tabs.Content>}
        <Tabs.Content value="downstream"><DownstreamTab versionId={versionId} scenarioId={scenarioId} entityId={entityId} /></Tabs.Content>
        {e.type === "course" && <Tabs.Content value="scenario"><ScenarioEditor detail={data} /></Tabs.Content>}
      </Tabs.Root>
    </div>
  );
}

function Details({ d }: { d: EntityDetail }) {
  const e = d.entity;
  const pl = d.placements[0];
  const fe = d.field_evidence;
  const det = e.details as Record<string, unknown>;
  return (
    <div className="col">
      {e.type === "course" && (
        <table className="data">
          <tbody>
            <tr><th>Title</th><td>{e.title} <FieldEvidence spans={fe.title} /></td></tr>
            <tr><th>Credits</th><td>{det.credits != null ? String(det.credits) : <span className="muted">not documented</span>} <FieldEvidence spans={fe.credits} /></td></tr>
            <tr><th>Year</th><td>{pl?.year ?? <span className="muted">not documented</span>} <FieldEvidence spans={fe.year} /></td></tr>
            <tr><th>Term</th><td>{pl ? TERM_LABEL[pl.term] ?? pl.term : "—"} <FieldEvidence spans={fe.term} /></td></tr>
            <tr><th>Classification</th><td>{pl ? CLASS_LABEL[pl.classification] ?? pl.classification : "—"}{pl?.pathway ? ` (${pl.pathway})` : ""} <FieldEvidence spans={fe.classification} /></td></tr>
          </tbody>
        </table>
      )}
      {e.description && (
        <div>
          <h4>Documented description</h4>
          <p style={{ margin: "2px 0" }}>{e.description}</p>
          <FieldEvidence spans={fe.description} />
        </div>
      )}
      {"statement_verbatim" in det && det.statement_verbatim ? (
        <div>
          <h4>Verbatim statement</h4>
          <blockquote className="quote">{String(det.statement_verbatim)}</blockquote>
          <FieldEvidence spans={fe.statement} />
        </div>
      ) : null}
      {e.type === "assessment" && (
        <table className="data"><tbody>
          <tr><th>Format</th><td>{String(det.format ?? "not documented")} <FieldEvidence spans={fe.format} /></td></tr>
          <tr><th>Weight</th><td>{det.weight_percent != null ? `${det.weight_percent}%` : "not documented"} <FieldEvidence spans={fe.weight_percent} /></td></tr>
          <tr><th>Timing</th><td>{det.timing_week != null ? `week ${det.timing_week}` : "not documented"} <FieldEvidence spans={fe.timing_week} /></td></tr>
        </tbody></table>
      )}
      {d.requirement_rules && d.requirement_rules.length > 0 && (
        <div>
          <h4>Documented requisites</h4>
          {d.requirement_rules.map((r) => (
            <div key={r.rule_id} className="card">
              <div className="row wrap"><strong>{r.kind}</strong> <span className="mono">{r.rendered}</span></div>
              <div className="row wrap" style={{ marginTop: 4 }}>
                <span className={`badge ${r.status}`}>{RULE_STATUS[r.status]}</span>
                <BasisBadge basis={r.basis} review={r.review_state} />
                {r.evidence_span_id ? <EvidenceChip spanId={r.evidence_span_id} /> : <span className="no-evidence">no source</span>}
              </div>
              {r.source_text && <div className="muted small">Source text: “{r.source_text}”</div>}
            </div>
          ))}
        </div>
      )}
      {d.unknowns && d.unknowns.length > 0 && (
        <div className="notice">
          <strong>Unknown or undocumented</strong>
          <ul style={{ margin: "4px 0 0", paddingLeft: 18 }}>{d.unknowns.map((u) => <li key={u}>{u}</li>)}</ul>
        </div>
      )}
      <details>
        <summary className="muted">Revision history ({d.revisions.length})</summary>
        <ul className="list">{d.revisions.map((r) => <li key={r.id}>rev {r.revision_number} · {r.origin} · {new Date(r.created_at).toLocaleString()}</li>)}</ul>
      </details>
    </div>
  );
}

function useHighlighter() {
  const setHighlight = useUi((s) => s.setHighlight);
  return {
    prior(p: PriorLearning) {
      const nodes = new Set<string>([p.entity.id]);
      const edges = new Set<string>();
      for (const it of p.items) {
        it.path.forEach((n) => nodes.add(n));
        it.edges.forEach((e) => edges.add(e.key));
        it.opportunities?.forEach((o) => o.timing === "earlier" && nodes.add(o.course.id));
      }
      setHighlight({ mode: "prior", nodes: [...nodes], edges: [...edges], label: `Expected preparation for ${p.entity.key}` });
    },
    downstream(d: Downstream) {
      const nodes = new Set<string>([d.entity.id]);
      const edges = new Set<string>();
      for (const it of d.items) {
        it.path.forEach((n) => nodes.add(n));
        it.edges.forEach((e) => edges.add(e.key));
      }
      d.dependent_courses.forEach((c) => nodes.add(c.course.id));
      setHighlight({ mode: "downstream", nodes: [...nodes], edges: [...edges], label: `Future use of ${d.entity.key}` });
    },
  };
}

function PriorTab({ versionId, scenarioId, entityId, pathway }: { versionId: string | null; scenarioId: string | null; entityId: string; pathway: string | null }) {
  const { data, isLoading } = usePrior(versionId, entityId, scenarioId, pathway);
  const hl = useHighlighter();
  const select = useUi((s) => s.select);
  if (isLoading || !data) return <p className="muted">Loading…</p>;
  const groups = {
    formal_requirement: data.items.filter((i) => i.relation === "formal_requirement"),
    inferred_preparation: data.items.filter((i) => i.relation === "inferred_preparation"),
    topic_or_outcome_preparation: data.items.filter((i) => i.relation === "topic_or_outcome_preparation"),
  };
  return (
    <div className="col">
      <div className="info small">
        {data.assumptions.language} Pathway assumption: <strong>{data.assumptions.pathway}</strong>. {data.assumptions.elective_note}
      </div>
      <button onClick={() => hl.prior(data)}>Highlight on map</button>
      {data.requirement_rules.map((r) => (
        <div key={r.rule_id} className="card small">
          <strong>Formal {r.kind}:</strong> <span className="mono">{r.rendered}</span> <span className={`badge ${r.status}`}>{RULE_STATUS[r.status]}</span>
          <ul style={{ margin: "4px 0 0", paddingLeft: 16 }}>{r.reasons.map((x) => <li key={x}>{x}</li>)}</ul>
        </div>
      ))}
      {(
        [
          ["formal_requirement", "Through formal requisites"],
          ["inferred_preparation", "Inferred pedagogical preparation"],
          ["topic_or_outcome_preparation", "Topic and outcome preparation"],
        ] as const
      ).map(([k, title]) => (
        <section key={k}>
          <h4>{title} ({groups[k].length})</h4>
          {groups[k].length === 0 && <p className="muted small">None found in the reviewed documents.</p>}
          <ul className="list">
            {groups[k].map((it) => (
              <li key={it.entity.id}>
                <div className="row wrap">
                  <button className="link" onClick={() => select({ kind: "node", id: it.entity.id })}>{it.entity.key}</button>
                  <span className="grow">{it.entity.type !== "course" ? it.entity.title : it.entity.title}</span>
                  <BasisBadge basis={it.basis} />
                  {it.exposure && <span className="badge">{EXPOSURE_LABEL[it.exposure]}</span>}
                  {it.timing && it.timing !== "earlier" && <span className="badge unknown">timing: {it.timing}</span>}
                </div>
                {it.opportunities && (
                  <div className="small muted">
                    Expected encounter: {it.opportunities.length ? it.opportunities.map((o) => `${o.course.key} (${EXPOSURE_LABEL[o.exposure]}, ${o.timing})`).join("; ") : "no course found"}
                  </div>
                )}
                {it.gap && <div className="small notice">{it.gap}</div>}
                {it.depth > 1 && <div className="path">path: {it.edges.map((e) => TYPE_LABEL[e.type] ?? e.type).join(" → ")}</div>}
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function ContributionTab({ d }: { d: EntityDetail }) {
  const c = d.contribution!;
  const select = useUi((s) => s.select);
  const LVL: Record<string, string> = { introduce: "Introduces", reinforce: "Reinforces", assess: "Assesses" };
  return (
    <div className="col">
      <section>
        <h4>Outcomes ({c.outcomes.length})</h4>
        {c.outcomes.length === 0 && <p className="notice small">No formal learning outcomes documented. A course description is not treated as an outcome statement.</p>}
        {c.outcomes.map((o) => (
          <div key={o.outcome.id} className="card small">
            <div><button className="link" onClick={() => select({ kind: "node", id: o.outcome.id })}>{o.outcome.key}</button>: {o.outcome.statement_verbatim ?? o.outcome.title}</div>
            <div className="row wrap" style={{ marginTop: 4 }}>
              {o.program_outcomes.map((p) => (
                <span key={p.plo.id} className="row" style={{ gap: 3 }}>
                  <span className="badge">{p.level ? LVL[p.level] : "Contributes to"} {p.plo.key}</span>
                  <BasisBadge basis={p.basis} review={p.review_state} />
                </span>
              ))}
            </div>
            <div className="muted">Assessed by: {o.assessed_by.map((a) => a.title).join(", ") || "no documented assessment"}</div>
          </div>
        ))}
      </section>
      <section>
        <h4>Topics ({c.topics.length})</h4>
        <div className="row wrap">{c.topics.map((t) => <span key={t.topic.id} className="badge" title={t.has_evidence ? "Sourced" : "No source span"}>{t.topic.title}{t.has_evidence ? "" : " *"}</span>)}</div>
        {c.topics.some((t) => !t.has_evidence) && <p className="muted small">* topic assignment without a recorded source span</p>}
      </section>
      <section>
        <h4>Assessments ({c.assessments.length})</h4>
        {c.assessments.length === 0 && <p className="muted small">No assessment documentation imported (this does not mean the course has none).</p>}
        <ul className="list">
          {c.assessments.map((a) => (
            <li key={a.assessment.id}>
              <strong>{a.assessment.title}</strong> — {String(a.details.format ?? "format ?")}, {a.details.weight_percent != null ? `${a.details.weight_percent}%` : "weight ?"}, {a.details.timing_week != null ? `week ${a.details.timing_week}` : "timing not documented"}
              <div className="muted small">Assesses: {a.assesses.map((x) => x.key).join(", ") || "not documented"}</div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function DownstreamTab({ versionId, scenarioId, entityId }: { versionId: string | null; scenarioId: string | null; entityId: string }) {
  const { data, isLoading } = useDownstream(versionId, entityId, scenarioId);
  const hl = useHighlighter();
  const select = useUi((s) => s.select);
  if (isLoading || !data) return <p className="muted">Loading…</p>;
  return (
    <div className="col">
      <button onClick={() => hl.downstream(data)}>Highlight on map</button>
      <section>
        <h4>Later courses relying on this ({data.dependent_courses.length})</h4>
        {data.dependent_courses.length === 0 && <p className="muted small">No documented or inferred dependents.</p>}
        <ul className="list">
          {data.dependent_courses.map((c) => (
            <li key={c.course.id} className="row wrap">
              <button className="link" onClick={() => select({ kind: "node", id: c.course.id })}>{c.course.key}</button>
              <span className="grow">{c.course.title}</span>
              <BasisBadge basis={c.basis} />
            </li>
          ))}
        </ul>
      </section>
      {data.alternatives.length > 0 && (
        <section>
          <h4>Alternative preparation</h4>
          {data.alternatives.map((a) => (
            <div key={a.course.id} className="small">{a.course.key} also accepts {a.alternatives.join(" or ")} <span className="mono">({a.rule})</span></div>
          ))}
        </section>
      )}
      <section>
        <h4>Dependency paths</h4>
        <ul className="list">
          {data.items.filter((i) => i.entity.type !== "course").slice(0, 40).map((i) => (
            <li key={i.entity.id} className="small">
              <span className="badge">{i.entity.type.replace("_", " ")}</span> {i.entity.title} <BasisBadge basis={i.basis} />
              <div className="path">{i.edges.map((e) => TYPE_LABEL[e.type] ?? e.type).join(" → ")}</div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

function EdgePanel({ versionId, scenarioId, edgeKey }: { versionId: string | null; scenarioId: string | null; edgeKey: string | null }) {
  const { data, isLoading, error } = useRelationship(versionId, edgeKey);
  const select = useUi((s) => s.select);
  if (error && scenarioId && edgeKey) return <ScenarioEdgePanel scenarioId={scenarioId} edgeKey={edgeKey} />;
  if (!edgeKey) return <p className="muted">This is a derived edge summarizing relationships through hidden outcomes or topics. Expand the courses to see the underlying relationships.</p>;
  if (isLoading || !data) return <p className="muted">Loading…</p>;
  const r = data.relationship;
  const documented = r.basis === "explicit_statement";
  return (
    <div className="col">
      <h2>{TYPE_LABEL[r.type] ?? r.type}</h2>
      <div className="row wrap">
        <button className="link" onClick={() => select({ kind: "node", id: data.source.id })}>{data.source.key}</button>
        <span>→</span>
        <button className="link" onClick={() => select({ kind: "node", id: data.target.id })}>{data.target.key}</button>
      </div>
      <p>{data.meaning}</p>
      <div className="row wrap">
        <BasisBadge basis={r.basis} review={r.review_state} />
        <span className="badge">origin: {r.origin.replace("_", " ")}</span>
        {r.level && <span className="badge">level: {r.level}</span>}
        {r.is_synthetic && <span className="badge synthetic">Synthetic</span>}
      </div>
      <p className="small">
        {documented
          ? "Documented: stated in an assigned source."
          : "Inferred: an interpretation, not a statement in a source. Accepting it in review does not make it documented."}
      </p>
      {data.rule && <div className="card small">Rule: <span className="mono">{data.rule.rendered}</span>{data.rule.source_text && <div className="muted">Source text: “{data.rule.source_text}”</div>}</div>}
      {r.rationale && <div className="small"><strong>Rationale:</strong> {r.rationale}</div>}
      <section>
        <h4>Evidence</h4>
        {data.evidence.length === 0 ? <p className="no-evidence">No source span recorded.</p> : (
          <div className="row wrap">{data.evidence.map((e) => <EvidenceChip key={e.span_id} spanId={e.span_id} stance={e.stance} />)}</div>
        )}
      </section>
      <section>
        <h4>Applies to</h4>
        <p className="small">{data.curriculum_version.label} ({data.curriculum_version.status})</p>
      </section>
      <details><summary className="muted">Revision history ({data.history.length})</summary>
        <ul className="list">{data.history.map((h) => <li key={h.revision_id}>rev {h.revision_number}: {h.review_state}</li>)}</ul>
      </details>
    </div>
  );
}

function ScenarioEdgePanel({ scenarioId, edgeKey }: { scenarioId: string; edgeKey: string }) {
  const { data } = useQuery({
    queryKey: ["scenario-rel", scenarioId, edgeKey],
    queryFn: () => getJson<{ relationship: { type: string; basis: string; rationale: string | null; review_state: string }; meaning: string; status: string; source: { key: string }; target: { key: string } }>(`/api/v1/scenarios/${scenarioId}/relationships/${edgeKey}`),
  });
  if (!data) return <p className="muted">Loading…</p>;
  return (
    <div className="col">
      <h2>{TYPE_LABEL[data.relationship.type] ?? data.relationship.type}</h2>
      <div>{data.source.key} → {data.target.key}</div>
      <p>{data.meaning}</p>
      <div className="row wrap"><span className={`badge ${data.status}`}>scenario: {data.status}</span><BasisBadge basis={data.relationship.basis} /></div>
      <p className="small">This relationship exists only in the scenario. It is a scenario assumption, with no source evidence.</p>
      {data.relationship.rationale && <p className="small">Rationale: {data.relationship.rationale}</p>}
    </div>
  );
}
