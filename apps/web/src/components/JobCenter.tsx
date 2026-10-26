import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { getJson } from "../api/client";
import { useAiJobs, useVersions, type Job } from "../api/hooks";
import { useUi } from "../store/ui";
import { arrange, duration, isActive, jobScope, jobTitle, payloadOf, resultLines, titlePrefix, visibleJobs } from "./jobCenterModel";

const canNotify = () => typeof window !== "undefined" && "Notification" in window;

/** Bottom-right cards for long-running AI jobs, on every page: live progress while one runs (with Stop), then a
 *  notification when it ends, which stays until dismissed (also after a reload, for a day). The tab title shows
 *  progress, and a system notification is sent when the tab is in the background (if allowed). */
export function JobCenter() {
  const jobs = useAiJobs();
  const { programId, versionId, setVersion, dismissedJobs, dismissJob } = useUi();
  const versions = useVersions(programId);
  const qc = useQueryClient();
  const navigate = useNavigate();
  const visible = visibleJobs(jobs.data ?? [], dismissedJobs);
  const versionLabel = (id: string) => versions.data?.find((v) => v.id === id)?.label;

  // a job this page saw running has ended: refresh what it changed, and tell a background tab
  const seen = useRef(new Map<string, string>());
  useEffect(() => {
    for (const j of jobs.data ?? []) {
      const before = seen.current.get(j.id);
      if (before && before !== j.status && !isActive(j)) {
        for (const k of ["web", "graph", "missing", "map-preview", "reviews", "entity"]) qc.invalidateQueries({ queryKey: [k] });
        if (canNotify() && Notification.permission === "granted" && document.hidden) {
          new Notification(jobTitle(j), { body: [jobScope(j, versionLabel), ...resultLines(j)].join("\n"), tag: j.id });
        }
      }
      seen.current.set(j.id, j.status);
    }
  }, [jobs.data]); // eslint-disable-line react-hooks/exhaustive-deps

  // the tab title carries progress and "done" while the page is in another tab
  const base = useRef(document.title.replace(/^(⏳ \d+% · |✓ |⚠ )/, ""));
  const prefix = titlePrefix(visible);
  useEffect(() => {
    document.title = prefix + base.current;
  }, [prefix]);

  if (!visible.length) return null;
  const { active, done, more } = arrange(visible);
  const open = (j: Job, path: string) => {
    const v = payloadOf(j).curriculum_version_id;
    if (v && v !== versionId && versions.data?.some((x) => x.id === v)) setVersion(v);
    dismissJob(j.id);
    navigate(path);
  };
  return (
    <section className="job-center" aria-label="Background jobs">
      {active.map((j) => <RunningCard key={j.id} job={j} scope={jobScope(j, versionLabel)} />)}
      {done.map((j) => (
        <DoneCard key={j.id} job={j} scope={jobScope(j, versionLabel)} onOpen={(path) => open(j, path)} onDismiss={() => dismissJob(j.id)} />
      ))}
      {more.length > 0 && (
        <div className="job-card more row small" role="status">
          <span className="grow">+{more.length} more finished job{more.length === 1 ? "" : "s"}</span>
          <button className="small" onClick={() => [...done, ...more].forEach((j) => dismissJob(j.id))}>Dismiss all</button>
        </div>
      )}
    </section>
  );
}

function RunningCard({ job, scope }: { job: Job; scope: string }) {
  const qc = useQueryClient();
  const [now, setNow] = useState(Date.now());
  const [perm, setPerm] = useState(canNotify() ? Notification.permission : "denied");
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 15000);
    return () => window.clearInterval(t);
  }, []);
  const pct = Math.round(job.progress * 100);
  const stop = async () => {
    if (!window.confirm("Stop this job? Links it has already proposed are kept.")) return;
    await getJson(`/api/v1/jobs/${job.id}/cancel`, { method: "POST" });
    qc.invalidateQueries({ queryKey: ["ai-jobs"] });
  };
  return (
    <div className="job-card running" role="status">
      <div className="row">
        <span className="spinner" aria-hidden />
        <strong className="grow">{jobTitle(job)}</strong>
        <span className="mono">{pct}%</span>
      </div>
      <div className="small muted">{scope}</div>
      <div className="progress" role="progressbar" aria-label={`${jobTitle(job)} progress`} aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${Math.max(pct, 3)}%` }} />
      </div>
      <div className="small">{job.status === "queued" ? "Waiting for a worker…" : job.last_message ?? "Working…"}</div>
      <div className="row small">
        <span className="muted grow">{duration(job.created_at, now)} so far · you can keep working</span>
        {perm === "default" && (
          <button className="link small" onClick={() => void Notification.requestPermission().then(setPerm)}>Notify me</button>
        )}
        {job.cancel_requested ? <span className="muted">stopping…</span> : <button className="small" onClick={stop}>Stop</button>}
      </div>
    </div>
  );
}

function DoneCard({ job, scope, onOpen, onDismiss }: { job: Job; scope: string; onOpen: (path: string) => void; onDismiss: () => void }) {
  const ok = job.status === "succeeded" || job.status === "partial";
  const [first, ...rest] = resultLines(job);
  return (
    <div className={`job-card ${ok ? "done" : "failed"}`} role="status">
      <div className="row">
        <span className="job-icon" aria-hidden>{ok ? "✓" : "⚠"}</span>
        <strong className="grow">{jobTitle(job)}</strong>
        <button className="link small" onClick={onDismiss} aria-label={`Dismiss: ${jobTitle(job)}`}>✕</button>
      </div>
      <div className="small muted">{scope}</div>
      <div className="small">{first}</div>
      {rest.map((l) => <div key={l} className="small muted">{l}</div>)}
      <div className="row small" style={{ marginTop: 4 }}>
        {job.kind === "ai_map_alignment" && <button className="small primary" onClick={() => onOpen("/connections")}>Open Connections</button>}
        <button className="small" onClick={() => onOpen("/")}>{ok ? "Show on the web" : "Open the web"}</button>
        <button className="small" onClick={onDismiss}>Dismiss</button>
      </div>
    </div>
  );
}
