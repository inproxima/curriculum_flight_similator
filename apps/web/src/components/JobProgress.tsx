import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { getJson } from "../api/client";
import { openJobStream } from "../lib/auth";

/** Live progress of a durable job over SSE (progress persists server-side, so reconnecting is safe). */
export function JobProgress({ jobId, title = "Processing", onEnd }: { jobId: string; title?: string; onEnd?: (status: string) => void }) {
  const [events, setEvents] = useState<{ id: number; stage: string | null; status: string; message: string }[]>([]);
  const [job, setJob] = useState<{ status: string; progress: number; stage: string | null } | null>(null);
  const qc = useQueryClient();
  const endRef = useRef(onEnd);
  endRef.current = onEnd;
  useEffect(() => {
    setEvents([]);
    setJob(null);
    let es: EventSource | null = null;
    let closed = false;
    let status = "queued";
    openJobStream(jobId).then((stream) => {
      if (closed) {
        stream.close();
        return;
      }
      es = stream;
      es.addEventListener("job", (ev) => {
        const d = JSON.parse((ev as MessageEvent).data);
        status = d.job?.status ?? status;
        setEvents((xs) => (xs.some((x) => x.id === d.id) ? xs : [...xs, d]));
        setJob(d.job);
      });
      es.addEventListener("end", () => {
        es?.close();
        qc.invalidateQueries({ queryKey: ["documents"] });
        qc.invalidateQueries({ queryKey: ["reviews"] });
        qc.invalidateQueries({ queryKey: ["jobs"] });
        endRef.current?.(status);
      });
    });
    return () => {
      closed = true;
      es?.close();
    };
  }, [jobId, qc]);
  const running = !job || job.status === "queued" || job.status === "running";
  return (
    <div className="card" aria-live="polite">
      <div className="row">
        <strong className="grow">{title}</strong>
        <span className="badge">{job?.status ?? "queued"}</span>
      </div>
      <div className="progress" role="progressbar" aria-label={title} aria-valuenow={Math.round((job?.progress ?? 0) * 100)} aria-valuemin={0} aria-valuemax={100}>
        <div style={{ width: `${(job?.progress ?? 0) * 100}%` }} />
      </div>
      <ul className="list small">
        {events.map((e) => (
          <li key={e.id}>
            <span className="badge">{e.stage ?? "job"}</span> {e.message}
          </li>
        ))}
      </ul>
      {running && (
        <div className="row">
          <button className="small" onClick={() => getJson(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" }).catch(() => undefined)}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
