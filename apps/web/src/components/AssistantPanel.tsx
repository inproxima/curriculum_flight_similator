import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../api/client";
import { useAiMutations, useAiStatus, useConversations, useMessages, waitForJob, type ChatMessage, type Critique, type Envelope, type Proposal } from "../api/aiHooks";
import { useEntity } from "../api/hooks";
import { useScenario, useScenarioMutations } from "../api/scenarioHooks";
import { useUi } from "../store/ui";
import { EvidenceChip } from "./Evidence";

const MODES = [
  { id: "explore", label: "Explore", hint: "Explain the map" },
  { id: "investigate", label: "Investigate", hint: "Focused analysis" },
  { id: "simulate", label: "Simulate", hint: "Draft scenario changes" },
] as const;

const BASIS_LABEL: Record<string, [string, string]> = {
  source_supported: ["Source-supported", "documented"],
  graph_or_rule_derived: ["Graph/rule-derived", "documented"],
  ai_interpretation: ["AI interpretation", "inferred"],
};

export function AssistantPanel() {
  const { versionId, scenarioId, setAssistantOpen } = useUi();
  const status = useAiStatus();
  const convs = useConversations(versionId);
  const m = useAiMutations();
  const qc = useQueryClient();
  const [mode, setMode] = useState<(typeof MODES)[number]["id"]>("explore");
  const [text, setText] = useState("");
  const [convId, setConvId] = useState<string | null>(null);
  const [progress, setProgress] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const messages = useMessages(convId);
  const endRef = useRef<HTMLDivElement>(null);

  // Conversations are scoped to version + scenario; pick the latest matching one.
  useEffect(() => {
    const match = convs.data?.find((c) => (c.scenario_id ?? null) === (scenarioId ?? null));
    setConvId(match?.id ?? null);
  }, [convs.data, scenarioId, versionId]);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.data, progress]);

  const selectedKey = useSelectedKey();
  const suggestions = useMemo(() => {
    const s = selectedKey
      ? [`What are students expected to have encountered before ${selectedKey}?`, `Which later courses rely on ${selectedKey}?`, `Which program outcomes does ${selectedKey} support, and on what evidence?`]
      : ["Which program outcomes have no documented assessment?", "Where does research communication develop across the program?"];
    return [...s, "Which conclusions need more source documents?"];
  }, [selectedKey]);

  const unavailable = status.data && !status.data.routes.find((r) => r.name === (mode === "simulate" ? "pedagogy" : "extract"))?.available;

  const send = async (q: string) => {
    if (!q.trim() || !versionId) return;
    setErr(null);
    setBusy(true);
    setProgress([]);
    try {
      let cid = convId;
      if (!cid) {
        cid = (await m.createConversation.mutateAsync({ curriculum_version_id: versionId, scenario_id: scenarioId })).id;
        setConvId(cid);
      }
      const r = await m.send.mutateAsync({ conversationId: cid, text: q, mode });
      setText("");
      await qc.invalidateQueries({ queryKey: ["messages", cid] });
      await waitForJob(r.job_id, (msg, st) => {
        if (st === "progress" || st === "running") setProgress((p) => (p[p.length - 1] === msg ? p : [...p, msg]));
      });
      await qc.invalidateQueries({ queryKey: ["messages", cid] });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
      setProgress([]);
    }
  };

  const critique = async (messageId: string) => {
    setBusy(true);
    setProgress(["Asking a second model to critique the proposal…"]);
    try {
      const r = await m.critique.mutateAsync(messageId);
      await waitForJob(r.job_id);
      await qc.invalidateQueries({ queryKey: ["messages", convId] });
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
      setProgress([]);
    }
  };

  return (
    <aside className="panel right assistant" aria-label="Assistant">
      <div className="row">
        <h2 className="grow">Assistant</h2>
        <button className="small" onClick={() => { setConvId(null); }} title="Start a new conversation">New</button>
        <button className="small" onClick={() => setAssistantOpen(false)} aria-label="Close assistant">✕</button>
      </div>
      <div role="radiogroup" aria-label="Assistant mode" className="row" style={{ gap: 4, margin: "6px 0" }}>
        {MODES.map((x) => (
          <button key={x.id} role="radio" aria-checked={mode === x.id} className={`small ${mode === x.id ? "primary" : ""}`} title={x.hint} onClick={() => setMode(x.id)}>
            {x.label}
          </button>
        ))}
      </div>
      <p className="muted small" style={{ margin: "2px 0 8px" }}>
        {status.data?.routes.find((r) => r.name === (mode === "simulate" ? "pedagogy" : "extract"))?.active?.join(" · ") ?? "…"}
        {scenarioId ? " · scenario context" : " · baseline"} · answers are AI interpretation, checked against cited sources.
      </p>
      {unavailable && <div className="notice small">This mode's model route is unavailable: {status.data?.routes.find((r) => r.name === (mode === "simulate" ? "pedagogy" : "extract"))?.unavailable_reasons.join("; ")}</div>}

      <div className="chat" aria-live="polite">
        {!messages.data?.messages.length && (
          <div className="col">
            <p className="muted small">Try:</p>
            {suggestions.map((s) => <button key={s} className="small" style={{ textAlign: "left" }} disabled={busy || !!unavailable} onClick={() => send(s)}>{s}</button>)}
          </div>
        )}
        {messages.data?.messages.map((msg) => (
          <MessageView key={msg.id} msg={msg} all={messages.data!.messages} onCritique={critique} busy={busy} />
        ))}
        {busy && (
          <div className="card small">
            {(progress.length ? progress : ["Working…"]).map((p, i) => <div key={i} className="muted">• {p}</div>)}
          </div>
        )}
        <div ref={endRef} />
      </div>
      {err && <p className="error small">{err}</p>}
      <form className="col" onSubmit={(e) => { e.preventDefault(); send(text); }}>
        <textarea rows={3} aria-label="Ask the assistant" placeholder={mode === "simulate" ? "Describe a change to explore…" : "Ask about the curriculum…"} value={text}
          onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) send(text); }} disabled={busy} />
        <div className="row">
          <span className="muted small grow">⌘/Ctrl+Enter to send. Selected excerpts go to the model provider shown above.</span>
          <button className="primary" disabled={busy || !text.trim() || !!unavailable}>Send</button>
        </div>
      </form>
    </aside>
  );
}

function useSelectedKey(): string | null {
  const { versionId, scenarioId, selection } = useUi();
  const { data } = useEntity(versionId, selection?.kind === "node" ? selection.id : null, scenarioId);
  return data?.entity.key ?? null;
}

function MessageView({ msg, all, onCritique, busy }: { msg: ChatMessage; all: ChatMessage[]; onCritique: (id: string) => void; busy: boolean }) {
  if (msg.role === "user") return <div className="bubble user">{String(msg.content.text)}<span className="badge" style={{ marginLeft: 6 }}>{String(msg.content.mode)}</span></div>;
  if (msg.role === "critique") return <CritiqueView c={msg.content as unknown as Critique} />;
  const env = msg.content as unknown as Envelope;
  const critiqued = all.some((x) => x.role === "critique" && (x.content as { critique_of?: string }).critique_of === msg.id);
  return <EnvelopeView env={env} messageId={msg.id} onCritique={critiqued ? undefined : onCritique} busy={busy} />;
}

function EnvelopeView({ env, messageId, onCritique, busy }: { env: Envelope; messageId: string; onCritique?: (id: string) => void; busy: boolean }) {
  const { select, setHighlight } = useUi();
  if (env.error) return <div className="card small error">Assistant error: {env.error.message}</div>;
  const v = env.validation;
  const dropped = v.dropped_citations.length + v.dropped_entities.length + v.dropped_proposals.length + v.dropped_relationships.length;
  return (
    <div className="card small envelope">
      <div className="row wrap" style={{ marginBottom: 4 }}>
        <span className="badge inferred">AI interpretation</span>
        <span className="badge" title={`Route ${env.meta.route}; tools: ${env.meta.tool_calls.join(", ")}`}>{env.meta.model}</span>
        {env.meta.cost_usd != null && <span className="badge">${env.meta.cost_usd.toFixed(3)}</span>}
        {env.meta.fallback_from && <span className="badge unknown" title="Primary provider failed">fallback from {env.meta.fallback_from}</span>}
      </div>
      {env.insufficient_documentation && <div className="notice small">The assistant reports that the documentation is insufficient for a complete answer.</div>}
      {v.warnings.map((w) => <div key={w} className="notice small">⚠ {w}</div>)}
      <div className="answer">{env.answer}</div>
      {env.citations.length > 0 && (
        <div className="row wrap" style={{ marginTop: 6 }}>
          <span className="muted">Sources:</span>
          {env.citations.map((c) => <EvidenceChip key={c.span_id} spanId={c.span_id} label={`${c.document.slice(0, 28)} p.${c.page}`} />)}
        </div>
      )}
      {env.highlighted_entities.length > 0 && (
        <div className="row wrap" style={{ marginTop: 6 }}>
          <button className="small" onClick={() => setHighlight({ mode: "finding", nodes: env.highlighted_entities.map((e) => e.id), edges: env.highlighted_relationship_keys, label: "Assistant answer" })}>Highlight on map</button>
          {env.highlighted_entities.map((e) => <button key={e.id} className="link small" onClick={() => select({ kind: "node", id: e.id })}>{e.key}</button>)}
        </div>
      )}
      {env.findings.length > 0 && (
        <ul className="list" style={{ marginTop: 6 }}>
          {env.findings.map((f, i) => (
            <li key={i}><span className={`badge ${BASIS_LABEL[f.basis]?.[1] ?? ""}`}>{BASIS_LABEL[f.basis]?.[0] ?? f.basis}</span> <strong>{f.title}</strong> — {f.explanation}</li>
          ))}
        </ul>
      )}
      {env.assumptions.length > 0 && <div className="muted" style={{ marginTop: 4 }}><strong>Assumptions:</strong> {env.assumptions.join(" · ")}</div>}
      {env.unanswered_questions.length > 0 && <div style={{ marginTop: 4 }}><strong>Open questions:</strong><ul style={{ margin: 0, paddingLeft: 16 }}>{env.unanswered_questions.map((q) => <li key={q}>{q}</li>)}</ul></div>}
      {env.proposed_changes.length > 0 && (
        <div className="col" style={{ marginTop: 6 }}>
          <strong>Proposed scenario changes (not applied)</strong>
          {env.proposed_changes.map((p, i) => <ProposalCard key={i} p={p} />)}
          {onCritique && <button className="small" disabled={busy} onClick={() => onCritique(messageId)} title="Ask a second model to look for problems">Request critique</button>}
        </div>
      )}
      {dropped > 0 && (
        <details className="muted" style={{ marginTop: 4 }}>
          <summary>{dropped} item(s) removed by verification</summary>
          <pre style={{ whiteSpace: "pre-wrap", fontSize: 11 }}>{JSON.stringify(v, null, 1)}</pre>
        </details>
      )}
    </div>
  );
}

function ProposalCard({ p }: { p: Proposal }) {
  const { versionId, scenarioId, setScenario } = useUi();
  const scenario = useScenario(scenarioId);
  const sm = useScenarioMutations(scenarioId);
  const [state, setState] = useState<"idle" | "applied" | "dismissed" | "error">("idle");
  const [msg, setMsg] = useState<string | null>(null);
  const apply = async () => {
    try {
      let sid = scenarioId;
      let rev = scenario.data?.revision;
      if (!sid) {
        const s = await sm.create.mutateAsync({ base_version_id: versionId!, title: `From assistant: ${p.summary.slice(0, 60)}` });
        sid = s.id;
        rev = s.revision;
        setScenario(s.id);
      }
      const res = await fetch(`/api/v1/scenarios/${sid}/changes`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "If-Match": String(rev) },
        body: JSON.stringify(p.change),
      });
      if (!res.ok) throw new Error((await res.json()).error?.message ?? "failed");
      setState("applied");
      setMsg(scenarioId ? "Applied to the selected scenario." : "Created a new scenario and applied the change.");
    } catch (e) {
      setState("error");
      setMsg(String((e as Error).message));
    }
  };
  if (state === "dismissed") return null;
  return (
    <div className="card small proposal">
      <div><strong>{p.summary}</strong></div>
      {p.rationale && <div className="muted">Why: {p.rationale}</div>}
      {p.assumptions.length > 0 && <div className="muted">Assumes: {p.assumptions.join(" · ")}</div>}
      <div className="row" style={{ marginTop: 4 }}>
        <button className="small primary" disabled={state === "applied"} onClick={apply}>{state === "applied" ? "Applied ✓" : scenarioId ? "Apply to scenario" : "Apply in new scenario"}</button>
        <button className="small" onClick={() => setState("dismissed")}>Dismiss</button>
      </div>
      {msg && <div className={state === "error" ? "error" : "muted"}>{msg}</div>}
    </div>
  );
}

function CritiqueView({ c }: { c: Critique }) {
  const cls = c.overall === "major_concerns" ? "rejected" : c.overall === "minor_concerns" ? "proposed" : "documented";
  return (
    <div className="card small critique">
      <div className="row wrap"><span className="badge inferred">Second-model critique</span><span className={`badge ${cls}`}>{c.overall.replace("_", " ")}</span><span className="badge">{c.meta.model}</span></div>
      <p className="muted" style={{ margin: "4px 0" }}>{c.meta.label}</p>
      <ul className="list">
        {c.concerns.map((x, i) => (
          <li key={i}><span className={`badge ${x.severity === "high" ? "rejected" : x.severity === "medium" ? "proposed" : ""}`}>{x.severity}</span>{x.proposal_index != null && <span className="badge">proposal {x.proposal_index + 1}</span>} {x.concern}<div className="muted">→ {x.suggestion}</div></li>
        ))}
      </ul>
      {c.questions_for_faculty.length > 0 && <div><strong>Questions for faculty:</strong><ul style={{ margin: 0, paddingLeft: 16 }}>{c.questions_for_faculty.map((q) => <li key={q}>{q}</li>)}</ul></div>}
    </div>
  );
}
