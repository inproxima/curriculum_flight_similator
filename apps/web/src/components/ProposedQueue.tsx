import { useMemo } from "react";
import { useLinkDecisions, type WebView } from "../api/webHooks";
import { useUi } from "../store/ui";
import { Strength } from "./Inspector";

/** Every proposed (AI or unreviewed) link, strongest first, with inline and bulk decisions. */
export function ProposedQueue({ view, onClose }: { view: WebView; onClose: () => void }) {
  const { versionId, scenarioId, select } = useUi();
  const dec = useLinkDecisions(versionId);
  const byId = useMemo(() => new Map(view.nodes.map((n) => [n.id, n])), [view.nodes]);
  const items = useMemo(
    () => view.edges.filter((e) => e.provenance === "proposed" && e.key).sort((a, b) => (b.strength?.effective ?? 0) - (a.strength?.effective ?? 0)),
    [view.edges],
  );
  const published = view.curriculum_version.status === "published";
  const disabled = scenarioId
    ? "Decisions change the draft curriculum; leave the scenario to review links."
    : published
      ? "This version is published and cannot change. Create a draft version to review links."
      : null;
  const strong = items.filter((e) => e.strength?.label === "strong" || (e.strength?.asserted ?? 0) >= 0.7);
  const err = (dec.decide.error ?? dec.bulk.error) as Error | null;
  return (
    <div>
      <div className="row"><h2 className="grow">Proposed links ({items.length})</h2><button className="small" onClick={onClose}>Close</button></div>
      <p className="muted small">
        Proposed links are interpretations (often from AI) that no source states. They are drawn dashed and count at reduced trust
        until someone accepts them. Accepting never relabels them as documented.
      </p>
      {disabled && <p className="notice small">{disabled}</p>}
      {strong.length > 0 && (
        <button className="small" disabled={!!disabled || dec.bulk.isPending} onClick={() => dec.bulk.mutate({ keys: strong.map((e) => e.key!), decision: "accept" })}>
          Accept all {strong.length} with asserted strength ≥ 0.70
        </button>
      )}
      {err && <div className="error small">{err.message}</div>}
      <ul className="list" style={{ marginTop: 8 }}>
        {items.map((e) => (
          <li key={e.id}>
            <div className="row wrap">
              <button className="link" onClick={() => select({ kind: "edge", id: e.id, key: e.key })}>
                {byId.get(e.source)?.key} → {byId.get(e.target)?.key}
              </button>
              <span className="muted small">{e.type.replace(/_/g, " ")}</span>
              <Strength label={e.strength?.label} score={e.strength?.effective} />
              {e.strength?.asserted != null && <span className="badge" title={e.strength.asserted_source ?? ""}>asserted {e.strength.asserted}</span>}
            </div>
            {e.rationale && <div className="muted small">{e.rationale}</div>}
            <div className="row" style={{ marginTop: 2 }}>
              <button className="small" disabled={!!disabled || dec.decide.isPending} onClick={() => dec.decide.mutate({ key: e.key!, decision: "accept" })}>Accept</button>
              <button className="small" disabled={!!disabled || dec.decide.isPending} onClick={() => dec.decide.mutate({ key: e.key!, decision: "reject" })}>Reject</button>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
