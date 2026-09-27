import { useState } from "react";
import { LEGEND } from "../lib/graphStyle";

export function Legend() {
  const [open, setOpen] = useState(true);
  return (
    <div className="legend" role="region" aria-label="Map legend">
      <div className="row">
        <strong className="grow">Legend</strong>
        <button className="small" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? "Hide" : "Show"}</button>
      </div>
      {open && (
        <>
          {LEGEND.map((l) => (
            <div className="item" key={l.label}>
              <svg width="34" height="8" aria-hidden>
                <line x1="0" y1="4" x2="34" y2="4" stroke={l.stroke} strokeWidth="2" strokeDasharray={l.dash} />
              </svg>
              {l.label}
            </div>
          ))}
          <div className="item muted" style={{ marginTop: 4 }}>Dashed card border = elective. “proposed” = awaiting review. “n/m sourced” = course fields backed by source evidence.</div>
          <div className="item muted">Position and line length carry no meaning; only lanes (year · term) do.</div>
        </>
      )}
    </div>
  );
}
