import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { NavLink } from "react-router-dom";
import { getJson } from "../api/client";
import { signOut } from "../lib/auth";
import { useJobs, usePrograms, useSearch, useVersions } from "../api/hooks";
import { useUi } from "../store/ui";
import { ScenarioSelector } from "./ScenarioSelector";

export function TopBar({ evidenceRatio }: { evidenceRatio: number | null }) {
  const { programId, versionId, setProgram, setVersion, select, setFocus, assistantOpen, setAssistantOpen } = useUi();
  const programs = usePrograms();
  const versions = useVersions(programId);
  const jobs = useJobs(true);

  useEffect(() => {
    if (!programId && programs.data?.length) setProgram(programs.data[0].id);
  }, [programs.data, programId, setProgram]);
  useEffect(() => {
    const vs = versions.data;
    if (vs?.length && (!versionId || !vs.some((v) => v.id === versionId))) {
      setVersion((vs.find((v) => v.status === "published") ?? vs[0]).id);
    }
  }, [versions.data, versionId, setVersion]);

  const running = jobs.data?.filter((j) => j.status === "running" || j.status === "queued").length ?? 0;
  return (
    <header className="topbar">
      <span className="brand">Curriculum Flight Simulator</span>
      <label className="row">
        <span className="sr-only">Program</span>
        <select aria-label="Program" value={programId ?? ""} onChange={(e) => setProgram(e.target.value || null)}>
          {!programs.data?.length && <option value="">No programs — import documents</option>}
          {programs.data?.map((p) => <option key={p.id} value={p.id}>{p.name}{p.is_synthetic ? " [SYNTHETIC]" : ""}</option>)}
        </select>
      </label>
      <label className="row">
        <span className="sr-only">Curriculum version</span>
        <select aria-label="Curriculum version and cohort" value={versionId ?? ""} onChange={(e) => setVersion(e.target.value || null)}>
          {versions.data?.map((v) => (
            <option key={v.id} value={v.id}>{v.label} · {v.status}{v.cohort ? ` · ${v.cohort}` : ""}</option>
          ))}
        </select>
      </label>
      <ScenarioSelector />
      <SearchBox versionId={versionId} onPick={(id) => { select({ kind: "node", id }); setFocus(null); }} />
      {evidenceRatio !== null && (
        <span className="badge" title="Share of documented course fields (title, description, credits, year, term, classification) backed by a source span">
          Evidence completeness {(evidenceRatio * 100).toFixed(0)}%
        </span>
      )}
      <NavLink to="/documents" className="badge" title="Document processing jobs">{running ? `⏳ ${running} processing` : "Documents idle"}</NavLink>
      <nav aria-label="Views">
        <NavLink to="/" end>Map</NavLink>
        <NavLink to="/table">Table</NavLink>
        <NavLink to="/matrix">Outcomes</NavLink>
        <NavLink to="/scenarios">Scenarios</NavLink>
        <NavLink to="/reviews">Review inbox</NavLink>
        <NavLink to="/documents">Documents</NavLink>
        <NavLink to="/ai">AI &amp; usage</NavLink>
      </nav>
      <UserBadge />
      <button className={assistantOpen ? "primary" : ""} aria-pressed={assistantOpen} onClick={() => setAssistantOpen(!assistantOpen)}
        title="Open the grounded assistant (map view)">Assistant</button>
    </header>
  );
}

function SearchBox({ versionId, onPick }: { versionId: string | null; onPick: (id: string) => void }) {
  const [q, setQ] = useState("");
  const [open, setOpen] = useState(false);
  const { data } = useSearch(versionId, q);
  const ref = useRef<HTMLDivElement>(null);
  return (
    <div ref={ref} style={{ position: "relative" }} onBlur={(e) => !ref.current?.contains(e.relatedTarget) && setOpen(false)}>
      <input
        type="search"
        placeholder="Search courses, outcomes, sources…"
        aria-label="Search"
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true); }}
        onFocus={() => setOpen(true)}
        style={{ width: 260 }}
      />
      {open && data && q.length >= 2 && (
        <div className="card" role="listbox" style={{ position: "absolute", top: 32, width: 420, zIndex: 20, maxHeight: 420, overflow: "auto" }}>
          <h4>Curriculum</h4>
          {data.entities.length === 0 && <div className="muted small">No matching entities</div>}
          {data.entities.map((e) => (
            <button key={e.id} role="option" aria-selected={false} className="link" style={{ display: "block", textAlign: "left", margin: "2px 0" }}
              onMouseDown={(ev) => { ev.preventDefault(); onPick(e.id); setOpen(false); }}
              onKeyDown={(ev) => { if (ev.key === "Enter") { onPick(e.id); setOpen(false); } }}>
              <span className="badge">{e.type.replace("_", " ")}</span> {e.key} {e.key !== e.title && `— ${e.title}`}
            </button>
          ))}
          <h4 style={{ marginTop: 8 }}>Sources <span className="muted small">({data.retrieval_mode})</span></h4>
          {data.documents.map((d) => (
            <div key={d.chunk_id} className="small" style={{ margin: "4px 0" }}>
              p.{d.page} · {d.section} — <span dangerouslySetInnerHTML={{ __html: sanitizeHeadline(d.headline) }} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** ts_headline only inserts <b></b>; escape everything else. */
function sanitizeHeadline(h: string) {
  const esc = h.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return esc.replace(/&lt;b&gt;/g, "<b>").replace(/&lt;\/b&gt;/g, "</b>");
}

function UserBadge() {
  const { data } = useQuery({ queryKey: ["me"], staleTime: 60_000, queryFn: () => getJson<{ display_name: string; email: string; role: string; auth_mode: string }>("/api/v1/me") });
  if (!data) return null;
  return (
    <span className="row small" style={{ marginLeft: "auto", gap: 6 }}>
      <span className="badge" title={data.email}>{data.display_name} · {data.role}</span>
      {data.auth_mode === "oidc" && <button className="small" onClick={() => signOut()}>Sign out</button>}
    </span>
  );
}
