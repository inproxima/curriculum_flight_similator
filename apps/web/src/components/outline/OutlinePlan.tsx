import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useAiStatus } from "../../api/aiHooks";
import { errorCode, errorText, useOutlineMutations, type LinkEdit, type OutlineImport, type PlanItem, type PlanSummary } from "../../api/outlineHooks";
import { EvidenceChip } from "../Evidence";
import { KIND_TEXT } from "./MissingPiecesCard";
import { ACTION_TEXT, applyLabel, blockedReason, changeCount, initialSelection, selectLinks, toggle } from "./planModel";

type Plan = NonNullable<OutlineImport["plan"]>;
const FIELD_TEXT: Record<string, string> = {
  statement: "wording",
  title: "name",
  format: "format",
  weight_percent: "weight",
  timing_week: "week",
  timing_text: "timing",
  description: "description",
  credits: "credits",
};
const REL_TEXT: Record<string, string> = { assesses: "assesses", supports: "supports", contributes_to: "contributes to" };

function show(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  return String(v);
}

function EditionBanner({ plan }: { plan: Plan }) {
  const { outline, version, fit } = plan.edition;
  const SOURCE_TEXT: Record<string, string> = {
    entered: "as entered",
    document: "the document's recorded year",
    extraction: "the outline's text",
    "page 1": "page 1 of the outline",
    title: "the document title",
    filename: "the file name",
  };
  const quoted = outline.quote && ["extraction", "page 1", "title", "filename"].includes(outline.source ?? "");
  const from = outline.source ? ` (${SOURCE_TEXT[outline.source] ?? outline.source}${quoted ? `: “${outline.quote}”` : ""})` : "";
  const text: Record<string, string> = {
    match: "Same academic year as this draft: newer outline content replaces older content by default.",
    older: "Older than this draft: it can only fill empty sections.",
    later: "Later than this draft: replacements are offered but not pre-selected.",
    unknown: "Academic year unknown: nothing is replaced by default. Re-run with the year to enable replacement.",
  };
  return (
    <div className={fit === "match" ? "info small" : "notice small"} role="status">
      <strong>Outline {outline.academic_year ?? "year unknown"}</strong>
      {from} · draft {version.label ?? version.academic_year} — {text[fit]}
    </div>
  );
}

function Diff({ item }: { item: PlanItem }) {
  return (
    <div className="plan-diff small">
      {item.diff.map((d) => (
        <div key={d.field}>
          <span className="muted">{FIELD_TEXT[d.field] ?? d.field}: </span>
          {d.before !== null && d.before !== undefined && <del>{show(d.before)}</del>} <ins>{show(d.after)}</ins>
        </div>
      ))}
    </div>
  );
}

function Evidence({ item }: { item: PlanItem }) {
  if (!item.evidence.length) return null;
  return (
    <div className="plan-evidence small">
      {item.evidence.map((e, i) => (
        <div key={i} className="row wrap" style={{ gap: 4 }}>
          {e.span_id ? <EvidenceChip spanId={e.span_id} label={e.page ? `p.${e.page}` : "source"} /> : <span className="badge">from the element's text</span>}
          {e.quote && <span className="quote-inline">“{e.quote.length > 220 ? `${e.quote.slice(0, 217)}…` : e.quote}”</span>}
        </div>
      ))}
    </div>
  );
}

function ActionBadge({ item }: { item: PlanItem }) {
  if (item.link) {
    return item.link.basis === "stated" ? (
      <span className="badge documented" title="The outline states this connection; it is recorded as documented.">Stated in outline</span>
    ) : (
      <span className="badge inferred" title="Not stated in the outline: an AI interpretation, recorded as one (long-dash line).">
        AI interpretation {item.link.model_strength != null ? item.link.model_strength.toFixed(2) : ""}
      </span>
    );
  }
  const cls = item.action === "add" ? "added" : item.action === "replace" ? "modified" : item.action === "retire" || item.action === "detach" ? "removed" : "";
  return <span className={`badge ${cls}`}>{ACTION_TEXT[item.action] ?? item.action}</span>;
}

function LinkControls({ item, edit, onEdit, disabled }: { item: PlanItem; edit?: LinkEdit; onEdit: (e: LinkEdit) => void; disabled: boolean }) {
  const L = item.link!;
  if (L.basis === "stated") return null;
  const strength = edit?.strength ?? L.strength ?? L.model_strength ?? 0.5;
  return (
    <div className="row wrap small" style={{ gap: 10 }}>
      <label className="row">
        Strength
        <input
          type="number"
          min={0}
          max={1}
          step={0.05}
          value={strength}
          disabled={disabled}
          aria-label={`Strength of ${item.title}`}
          onChange={(e) => onEdit({ ...edit, strength: Math.min(1, Math.max(0, Number(e.target.value))) })}
          style={{ width: 64 }}
        />
      </label>
      {L.rel_type === "contributes_to" && (
        <label className="row">
          Level
          <select
            value={edit?.level ?? L.level ?? ""}
            disabled={disabled}
            aria-label={`Level of ${item.title}`}
            onChange={(e) => onEdit({ ...edit, level: (e.target.value || "") as LinkEdit["level"] })}
          >
            <option value="">unstated</option>
            <option value="introduce">introduce</option>
            <option value="reinforce">reinforce</option>
            <option value="assess">assess</option>
          </select>
        </label>
      )}
    </div>
  );
}

function ItemRow({
  item,
  checked,
  onToggle,
  edit,
  onEdit,
}: {
  item: PlanItem;
  checked: boolean;
  onToggle: (id: string, on: boolean) => void;
  edit?: LinkEdit;
  onEdit: (e: LinkEdit) => void;
}) {
  const blocked = blockedReason(item);
  const id = `pi-${item.id.replace(/[^a-zA-Z0-9_-]/g, "_")}`;
  const L = item.link;
  return (
    <li className={`plan-item act-${item.action}${checked ? " on" : ""}`}>
      <input type="checkbox" id={id} checked={checked} disabled={!!blocked} onChange={(e) => onToggle(item.id, e.target.checked)} aria-describedby={`${id}-why`} />
      <div className="grow">
        <label htmlFor={id} className="row wrap plan-head">
          <ActionBadge item={item} />
          <span className="plan-title">{L ? `${L.source.key ?? L.source.label} ${REL_TEXT[L.rel_type]} ${L.target.key ?? L.target.label}` : item.title}</span>
          {item.fills.length > 0 && <span className="badge documented" title="Missing pieces this fills">fills {item.fills.length}</span>}
          {item.carries_links > 0 && <span className="badge quiet" title="Links of this element are kept">keeps {item.carries_links} link{item.carries_links > 1 ? "s" : ""}</span>}
          {item.drops_links > 0 && <span className="badge rejected">removes {item.drops_links} link{item.drops_links > 1 ? "s" : ""}</span>}
        </label>
        {L && (
          <div className="small plan-link">
            <span title={L.source.label ?? undefined}>{L.source.label}</span> <span className="muted">→ {REL_TEXT[L.rel_type]} →</span>{" "}
            <span title={L.target.label ?? undefined}>{L.target.label}</span>
          </div>
        )}
        {item.action === "add" && item.proposed && (
          <div className="small">
            {item.key_preview && <span className="mono">{item.key_preview}</span>} {String(item.proposed.title ?? "")}
          </div>
        )}
        {item.action === "retire" || item.action === "detach" ? <div className="small">{item.target?.label}</div> : null}
        <div id={`${id}-why`} className="muted small">
          {blocked ?? item.reason}
          {item.agent?.reason && item.agent.decided_by === "agent" && !L ? ` · Agent: ${item.agent.reason}` : ""}
          {L?.rationale ? ` · ${L.rationale}` : ""}
        </div>
        {item.diff.length > 0 && <Diff item={item} />}
        {L && <LinkControls item={item} edit={edit} onEdit={onEdit} disabled={!checked} />}
        <Evidence item={item} />
      </div>
    </li>
  );
}

function Block({
  title,
  note,
  items,
  sel,
  edits,
  onToggle,
  onEdit,
  actions,
}: {
  title: string;
  note?: string;
  items: PlanItem[];
  sel: Set<string>;
  edits: Record<string, LinkEdit>;
  onToggle: (id: string, on: boolean) => void;
  onEdit: (id: string, e: LinkEdit) => void;
  actions?: ReactNode;
}) {
  const live = items.filter((i) => !i.noop);
  const quiet = items.filter((i) => i.noop);
  if (!items.length && !note) return null;
  return (
    <fieldset className="plan-block">
      <legend>
        {title} <span className="muted small">{live.filter((i) => sel.has(i.id)).length} of {live.length} selected</span>
      </legend>
      {note && <div className="muted small">{note}</div>}
      {actions}
      <ul className="list">
        {live.map((i) => (
          <ItemRow key={i.id} item={i} checked={sel.has(i.id)} onToggle={onToggle} edit={edits[i.id]} onEdit={(e) => onEdit(i.id, e)} />
        ))}
      </ul>
      {quiet.length > 0 && (
        <details className="small">
          <summary>Unchanged ({quiet.length})</summary>
          <ul className="list">
            {quiet.map((i) => (
              <li key={i.id}>
                {i.title} <span className="muted">— {i.reason}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </fieldset>
  );
}

export function OutlinePlan({
  imp,
  onApplied,
  onRerun,
  onDiscarded,
}: {
  imp: OutlineImport;
  onApplied: (r: OutlineImport) => void;
  onRerun: (jobId: string) => void;
  onDiscarded: () => void;
}) {
  const plan = imp.plan!;
  const m = useOutlineMutations();
  const [sel, setSel] = useState<Set<string>>(() => initialSelection(plan));
  const [edits, setEdits] = useState<Record<string, LinkEdit>>({});
  const [summary, setSummary] = useState<PlanSummary>(plan.summary);
  const [err, setErr] = useState<{ code: string | null; text: string } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const t = setTimeout(() => {
      m.preview.mutate({ id: imp.id, selected: [...sel], edits }, { onSuccess: setSummary });
    }, 300);
    return () => clearTimeout(t);
  }, [sel, edits]); // eslint-disable-line react-hooks/exhaustive-deps

  const onToggle = (id: string, on: boolean) => {
    const r = toggle(plan, sel, id, on);
    setSel(r.sel);
    const name = (x: string) => plan.items.find((i) => i.id === x)?.title ?? x;
    const list = (xs: string[]) => `${xs.slice(0, 3).map(name).join("; ")}${xs.length > 3 ? ` and ${xs.length - 3} more` : ""}`;
    const added = r.also.filter((x) => r.sel.has(x));
    const removed = r.also.filter((x) => !r.sel.has(x));
    const parts = [
      added.length ? `Also selected (needed): ${list(added)}.` : "",
      removed.length ? `${on ? "Deselected (conflicts)" : "Also deselected (depends on it)"}: ${list(removed)}.` : "",
    ].filter(Boolean);
    setNotice(parts.length ? parts.join(" ") : null);
  };
  const onEdit = (id: string, e: LinkEdit) => setEdits((x) => ({ ...x, [id]: e }));

  const by = useMemo(() => {
    const g: Record<string, PlanItem[]> = {};
    for (const i of plan.items) {
      const k = i.section === "links" ? `links:${i.link?.basis}` : i.section;
      (g[k] ??= []).push(i);
    }
    return g;
  }, [plan]);
  const inferredIds = (by["links:inferred"] ?? []).map((i) => i.id);
  const changes = changeCount(plan, sel);
  const busy = m.apply.isPending || m.rerun.isPending || m.discard.isPending;
  const synth = useAiStatus().data?.routes.find((r) => r.name === "synthesize");
  const canMap = !synth || synth.available;
  const [mapAfter, setMapAfter] = useState(true);

  const apply = () => {
    setErr(null);
    m.apply.mutate(
      { id: imp.id, etag: imp.etag!, selected: [...sel], edits, mapAfter: mapAfter && canMap },
      { onSuccess: onApplied, onError: (e) => setErr({ code: errorCode(e), text: errorText(e) }) },
    );
  };
  const rerun = () =>
    m.rerun.mutate({ id: imp.id }, { onSuccess: (r) => onRerun(r.job_id), onError: (e) => setErr({ code: errorCode(e), text: errorText(e) }) });

  return (
    <div className="outline-plan">
      <EditionBanner plan={plan} />
      {plan.warnings.map((w) => (
        <div key={w} className="notice small">
          {w}
        </div>
      ))}
      <Block title="Course details" items={by.course ?? []} sel={sel} edits={edits} onToggle={onToggle} onEdit={onEdit} />
      {plan.sections.map((s) => (
        <Block
          key={s.key}
          title={s.label}
          note={`${s.reason}${s.existing_edition ? ` Current items come from ${s.existing_edition}.` : ""}`}
          items={by[s.key] ?? []}
          sel={sel}
          edits={edits}
          onToggle={onToggle}
          onEdit={onEdit}
        />
      ))}
      <Block
        title="Links stated in the outline"
        items={by["links:stated"] ?? []}
        sel={sel}
        edits={edits}
        onToggle={onToggle}
        onEdit={onEdit}
      />
      <Block
        title="Links the agent infers (AI interpretations)"
        note="Not stated in the outline. Applied links are kept labelled as interpretations (long-dash lines), never as documented."
        items={by["links:inferred"] ?? []}
        sel={sel}
        edits={edits}
        onToggle={onToggle}
        onEdit={onEdit}
        actions={
          inferredIds.length > 0 && (
            <div className="row small" style={{ margin: "4px 0" }}>
              <button
                className="small"
                onClick={() => {
                  const r = selectLinks(plan, sel, inferredIds, true, 0.6);
                  setSel(r.sel);
                  setNotice(r.skipped ? `${r.skipped} link(s) skipped: they need an element you are retiring or can't add.` : null);
                }}
              >
                Select strength ≥ 0.6
              </button>
              <button className="small" onClick={() => setSel(selectLinks(plan, sel, inferredIds, false).sel)}>Clear</button>
            </div>
          )
        }
      />
      {plan.unfillable.length > 0 && (
        <details className="plan-block small">
          <summary>Not filled by this outline ({plan.unfillable.length})</summary>
          <ul className="list">
            {plan.unfillable.map((g) => (
              <li key={g.id}>
                {KIND_TEXT[g.kind] ?? g.kind}: {g.message} <span className="muted">— {g.why}</span>
              </li>
            ))}
          </ul>
        </details>
      )}
      {plan.notes.length > 0 && (
        <details className="plan-block small">
          <summary>Agent notes ({plan.notes.length})</summary>
          <ul className="list">{plan.notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </details>
      )}
      {plan.dropped.length > 0 && (
        <details className="plan-block small">
          <summary>Removed by the checks ({plan.dropped.length})</summary>
          <ul className="list">
            {plan.dropped.map((d, i) => (
              <li key={i}>
                <span className="mono">{d.what}</span> — {d.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
      <footer className="plan-footer">
        <div className="grow small" aria-live="polite">
          {notice && <div className="muted">{notice}</div>}
          {summary.introduces.length > 0 && (
            <div className="muted">Also opens {summary.introduces.length}: {summary.introduces.slice(0, 2).map((g) => g.message).join(" ")}</div>
          )}
          {err && (
            <div className="error">
              {err.text}{" "}
              {(err.code === "plan_stale" || err.code === "plan_changed") && (
                <button className="small" onClick={rerun} disabled={m.rerun.isPending}>
                  Re-run the plan
                </button>
              )}
            </div>
          )}
        </div>
        <label className="row small" title={canMap ? "After applying, the AI maps every outcome, assessment and topic of this course: its level of connection to program outcomes, course outcomes, and earlier and later courses, or that it found none. Results are proposals; the cost is shown once it starts." : synth?.unavailable_reasons.join("; ")}>
          <input type="checkbox" checked={mapAfter && canMap} disabled={!canMap || busy} onChange={(e) => setMapAfter(e.target.checked)} />
          Then map this course's connections (AI)
        </label>
        <button className="small" onClick={() => m.discard.mutate(imp.id, { onSuccess: onDiscarded })} disabled={busy}>
          Discard
        </button>
        <button className="small" onClick={rerun} disabled={busy} title="Plan again against the current version">
          Re-run
        </button>
        <button className="primary" onClick={apply} disabled={!changes || busy}>
          {m.apply.isPending ? "Applying…" : applyLabel(changes, summary)}
        </button>
      </footer>
    </div>
  );
}
