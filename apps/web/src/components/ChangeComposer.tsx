import { useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { ApiError, getJson } from "../api/client";
import { useAiMutations, useAiStatus, waitForJob, type ChatMessage, type Envelope } from "../api/aiHooks";
import { useScenario, useScenarioChanges, useScenarioMutations } from "../api/scenarioHooks";
import type { WebView } from "../api/webHooks";
import { useUi } from "../store/ui";
import { ProposalCard } from "./AssistantPanel";
import { EvidenceChip } from "./Evidence";

/** "Here is the current state — what would you like to change?" → proposals → apply → Simulate. */
export function ChangeComposer({ view }: { view: WebView }) {
  const { versionId, scenarioId, selection, setSim, setScenario } = useUi();
  const scenario = useScenario(scenarioId);
  const changes = useScenarioChanges(scenarioId);
  const sm = useScenarioMutations(scenarioId);
  const ai = useAiMutations();
  const status = useAiStatus();
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState<string | null>(null);
  const [env, setEnv] = useState<Envelope | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [convId, setConvId] = useState<string | null>(null);
  const [showChanges, setShowChanges] = useState(false);

  const applied = changes.data?.filter((c) => c.applied) ?? [];
  const route = status.data?.routes.find((r) => r.name === "pedagogy");
  const aiOff = status.data && !route?.available;

  const selectedNode = selection?.kind === "node" ? view.nodes.find((n) => n.id === selection.id) : undefined;
  const chips = useMemo(() => {
    const weakest = [...view.plo_threads].sort((a, b) => (a.strength ?? 0) - (b.strength ?? 0))[0];
    const plo = weakest && view.nodes.find((n) => n.id === weakest.plo_id);
    const out: string[] = [];
    if (selectedNode) {
      const label = selectedNode.type === "course" ? selectedNode.key : `${selectedNode.key} (${selectedNode.type.replace("_", " ")})`;
      out.push(`What if we changed ${label}? Suggest one improvement.`);
    }
    if (plo) out.push(`Make "${plo.title.split(": ").slice(1).join(": ") || plo.title}" properly assessed by final year`);
    const orphan = view.gaps.find((g) => g.kind === "assessment_no_clo");
    if (orphan) out.push(`Align "${view.nodes.find((n) => n.id === orphan.entity_ids[0])?.title}" to a learning outcome`);
    const bridge = view.gaps.find((g) => g.kind === "course_pair_no_link");
    if (bridge) out.push(`Strengthen the link between ${view.nodes.find((n) => n.id === bridge.source)?.key} and ${view.nodes.find((n) => n.id === bridge.target)?.key}`);
    return out.slice(0, 4);
  }, [view, selectedNode]);

  const ask = async (q: string) => {
    if (!q.trim() || !versionId) return;
    setErr(null);
    setBusy(true);
    setEnv(null);
    setProgress("Sending to the drafting model…");
    try {
      let cid = convId;
      if (!cid) {
        cid = (await ai.createConversation.mutateAsync({ curriculum_version_id: versionId, scenario_id: scenarioId })).id;
        setConvId(cid);
      }
      const focus = selectedNode ? ` [Selected on the web: ${selectedNode.key} — ${selectedNode.title}]` : "";
      const r = await ai.send.mutateAsync({ conversationId: cid, text: q + focus, mode: "simulate" });
      setText("");
      await waitForJob(r.job_id, (msg) => msg && setProgress(msg));
      const msgs = await getJson<{ messages: ChatMessage[] }>(`/api/v1/conversations/${cid}/messages`);
      const last = [...msgs.messages].reverse().find((m) => m.role === "assistant");
      setEnv((last?.content as unknown as Envelope) ?? null);
      qc.invalidateQueries({ queryKey: ["messages", cid] });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
      setProgress(null);
    }
  };

  const simulate = async () => {
    setErr(null);
    try {
      const run = await sm.analyze.mutateAsync();
      setSim({ open: true, runId: run.id, mode: "diff", step: 0, playing: false });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    }
  };

  return (
    <div className="composer" role="region" aria-label="Change and simulate">
      {(env || err || busy) && (
        <div className="composer-tray card">
          <div className="row">
            <strong className="grow">{busy ? "Drafting changes…" : err ? "Something went wrong" : "Proposed changes"}</strong>
            {!busy && <button className="small" onClick={() => { setEnv(null); setErr(null); }} aria-label="Close proposals">✕</button>}
          </div>
          {busy && <div className="muted small">• {progress}</div>}
          {err && <div className="error small">{err}</div>}
          {env && (
            <div className="col">
              {env.error && <div className="error small">{env.error.message}</div>}
              <p className="answer small">{env.answer}</p>
              {env.citations.length > 0 && <div className="row wrap">{env.citations.map((c) => <EvidenceChip key={c.span_id} spanId={c.span_id} />)}</div>}
              {env.proposed_changes.map((p, i) => <ProposalCard key={i} p={p} />)}
              {!env.proposed_changes.length && !env.error && <div className="muted small">No concrete change was proposed. Try naming the element to change, or select it on the web.</div>}
              {env.validation?.dropped_proposals?.length > 0 && (
                <div className="muted small">{env.validation.dropped_proposals.length} proposal(s) failed validation and were removed.</div>
              )}
            </div>
          )}
        </div>
      )}
      <form className="row composer-bar" onSubmit={(e) => { e.preventDefault(); ask(text); }}>
        <input
          type="text"
          className="grow"
          aria-label="What would you like to change?"
          placeholder={selectedNode ? `What would you like to change about ${selectedNode.key}?` : "What would you like to change? e.g. “Replace the SYN 420 portfolio with a group presentation”"}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={busy}
        />
        <button type="submit" disabled={busy || !text.trim() || !!aiOff} title={aiOff ? route?.unavailable_reasons.join("; ") : `Drafted by ${route?.active?.join(" · ") ?? "the drafting model"}`}>
          Ask AI
        </button>
        <button type="button" className="primary simulate" disabled={!scenarioId || !applied.length || sm.analyze.isPending} onClick={simulate}
          title={applied.length ? "Run the deterministic impact analysis and show the ripple" : "Apply at least one change first"}>
          {sm.analyze.isPending ? "Simulating…" : "▶ Simulate"}
        </button>
      </form>
      <div className="row wrap composer-meta">
        {!busy && chips.map((c) => (
          <button key={c} className="chip" onClick={() => (aiOff ? setText(c) : ask(c))} disabled={busy}>{c}</button>
        ))}
        <span className="grow" />
        {aiOff && <span className="muted small">AI drafting unavailable; use “Change this…” in the inspector.</span>}
        {scenarioId && (
          <button className="link small" aria-expanded={showChanges} onClick={() => setShowChanges(!showChanges)}>
            {applied.length} change{applied.length === 1 ? "" : "s"} in “{scenario.data?.title}”
          </button>
        )}
        {scenarioId && <button className="link small" onClick={() => setScenario(null)} title="Return to the unchanged baseline">Back to baseline</button>}
      </div>
      {showChanges && scenarioId && (
        <div className="card small composer-changes">
          <ol style={{ margin: 0, paddingLeft: 18 }}>
            {changes.data?.map((c) => <li key={c.id} className={c.applied ? "" : "muted"}>{c.summary}{c.applied ? "" : " (undone)"}</li>)}
          </ol>
          <div className="row" style={{ marginTop: 4 }}>
            <button className="small" disabled={!applied.length || !scenario.data} onClick={() => sm.undo.mutate(scenario.data!.revision)}>Undo last</button>
            <button className="small" disabled={!scenario.data || applied.length === (changes.data?.length ?? 0)} onClick={() => sm.redo.mutate(scenario.data!.revision)}>Redo</button>
          </div>
        </div>
      )}
    </div>
  );
}
