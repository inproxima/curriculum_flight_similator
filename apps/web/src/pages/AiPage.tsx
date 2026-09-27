import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { getJson } from "../api/client";
import { useAiStatus, useAiUsage } from "../api/aiHooks";

export function AiPage() {
  const st = useAiStatus();
  const usage = useAiUsage();
  const [runId, setRunId] = useState<string | null>(null);
  const total = usage.data?.by_route.reduce((a, r) => a + r.cost_usd, 0) ?? 0;
  return (
    <div className="page">
      <h2>AI routes, spend, and audit</h2>
      {st.data && <p className="info small">{st.data.message} Retrieval mode: <strong>{st.data.retrieval_mode}</strong>.</p>}
      {st.data && (
        <div className="row wrap" style={{ margin: "8px 0" }}>
          <span className="badge">This month: ${st.data.spend.month_usd.toFixed(4)} of ${st.data.limits.monthly_budget_usd.toFixed(2)} budget</span>
          <span className="badge">Per-job limit: ${st.data.limits.max_cost_per_job_usd.toFixed(2)}</span>
          {Object.entries(st.data.providers).map(([p, ok]) => (
            <span key={p} className={`badge ${ok && st.data!.allowlist[p] ? "documented" : "rejected"}`}>{p}: {ok ? "key configured" : "no key"}{st.data!.allowlist[p] ? "" : " · not allowlisted"}</span>
          ))}
        </div>
      )}
      <table className="data" aria-label="Model routes">
        <thead><tr><th>Route</th><th>Purpose</th><th>Primary</th><th>Fallback</th><th>Active</th><th>Status</th></tr></thead>
        <tbody>
          {st.data?.routes.map((r) => (
            <tr key={r.name}>
              <td className="mono">{r.name}</td><td>{r.purpose}</td><td>{r.provider} · {r.model}</td>
              <td>{r.fallback ? r.fallback.join(" · ") : "none (separate vector space)"}</td>
              <td>{r.active ? r.active.join(" · ") : "—"}</td>
              <td>{r.available ? <span className="badge documented">available</span> : <span className="badge rejected" title={r.unavailable_reasons.join("; ")}>unavailable</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="muted small">Fallback is used only when the primary provider fails or is not permitted, and only if every document included in the request permits the fallback provider.</p>
      <h3 style={{ marginTop: 16 }}>Usage by route (all time: ${total.toFixed(4)})</h3>
      <table className="data small">
        <thead><tr><th>Route</th><th>Model</th><th>Status</th><th>Runs</th><th>Input tokens</th><th>Output tokens</th><th>Cost</th></tr></thead>
        <tbody>{usage.data?.by_route.map((r, i) => (
          <tr key={i}><td>{r.route}</td><td>{r.provider} · {r.model}</td><td>{r.status}</td><td>{r.runs}</td><td>{r.input_tokens.toLocaleString()}</td><td>{r.output_tokens.toLocaleString()}</td><td>${r.cost_usd.toFixed(4)}</td></tr>
        ))}</tbody>
      </table>
      <h3 style={{ marginTop: 16 }}>Recent model runs</h3>
      <table className="data small">
        <thead><tr><th>When</th><th>Purpose</th><th>Model (returned)</th><th>Status</th><th>Tokens in/out</th><th>Cost</th><th>ms</th><th>Evidence</th><th></th></tr></thead>
        <tbody>{usage.data?.recent.map((r) => (
          <tr key={r.id}>
            <td>{new Date(r.created_at).toLocaleString()}</td><td>{r.purpose ?? r.route} <span className="muted">{r.prompt_version}</span></td>
            <td>{r.requested_model}{r.returned_model && r.returned_model !== r.requested_model ? ` → ${r.returned_model}` : ""}{r.fallback_from ? ` (fallback from ${r.fallback_from})` : ""}</td>
            <td><span className={`badge ${r.status === "ok" ? "documented" : r.status === "cache_hit" ? "" : "rejected"}`}>{r.status}</span>{r.error && <div className="error">{r.error.slice(0, 160)}</div>}</td>
            <td>{r.input_tokens ?? 0}/{r.output_tokens ?? 0}</td><td>{r.cost_usd != null ? `$${r.cost_usd.toFixed(4)}` : "—"}</td><td>{r.duration_ms ?? ""}</td><td>{r.evidence_ids}</td>
            <td><button className="small" onClick={() => setRunId(r.id)}>Audit</button></td>
          </tr>
        ))}</tbody>
      </table>
      {runId && <RunAudit id={runId} onClose={() => setRunId(null)} />}
    </div>
  );
}

function RunAudit({ id, onClose }: { id: string; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ["run", id], queryFn: () => getJson<Record<string, unknown> & { tool_calls: { tool: string; arguments: unknown; status: string }[] }>(`/api/v1/ai/runs/${id}`) });
  return (
    <div className="card" style={{ marginTop: 12 }}>
      <div className="row"><h3 className="grow">Model run audit</h3><button className="small" onClick={onClose}>Close</button></div>
      {data && (
        <>
          <pre className="small" style={{ whiteSpace: "pre-wrap" }}>{JSON.stringify({ ...data, tool_calls: undefined }, null, 1)}</pre>
          <h4>Tool calls ({data.tool_calls.length})</h4>
          <ol className="small">{data.tool_calls.map((t, i) => <li key={i}><span className="mono">{t.tool}</span> {JSON.stringify(t.arguments)} <span className="badge">{t.status}</span></li>)}</ol>
          <p className="muted small">Hidden model reasoning is never stored; the audit records the route, returned model, evidence ids, tool calls, tokens, and cost.</p>
        </>
      )}
    </div>
  );
}
