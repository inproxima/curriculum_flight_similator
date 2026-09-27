import type { Scorecard } from "../api/webHooks";

type Tile = { label: string; value: (s: Scorecard) => number | null; fmt: (v: number) => string; better: "up" | "down"; title: string };

const pct = (v: number) => `${Math.round(v * 100)}%`;

export const TILES: Tile[] = [
  { label: "PLO coverage", value: (s) => s.plo_documented_coverage, fmt: pct, better: "up", title: "Program outcomes with a documented contribution on the required path" },
  { label: "PLOs assessed", value: (s) => s.plo_assessed_coverage, fmt: pct, better: "up", title: "Program outcomes with a documented assessment of a contributing outcome" },
  { label: "Complete threads", value: (s) => s.threads_complete.n, fmt: (v) => `${v}`, better: "up", title: "Program outcomes introduced, reinforced, and assessed in order" },
  { label: "Avg strength", value: (s) => s.avg_alignment_strength, fmt: (v) => v.toFixed(2), better: "up", title: "Mean effective strength of all alignment links" },
  { label: "Weak links", value: (s) => s.links.weak, fmt: (v) => `${v}`, better: "down", title: "Links rated weak (< 0.45)" },
  { label: "Unbridged prerequisites", value: (s) => s.broken_chains, fmt: (v) => `${v}`, better: "down", title: "Prerequisite pairs with no outcome- or assessment-level link" },
  { label: "Orphans", value: (s) => s.orphans.clo + s.orphans.assessment + s.orphans.topic, fmt: (v) => `${v}`, better: "down", title: "Outcomes with no program outcome, assessments assessing nothing, topics supporting nothing" },
  { label: "Proposed", value: (s) => s.links.proposed, fmt: (v) => `${v}`, better: "down", title: "Links awaiting review" },
];

export function delta(before: number | null, after: number | null, better: "up" | "down") {
  if (before == null || after == null) return null;
  const d = Math.round((after - before) * 1000) / 1000;
  if (d === 0) return { d, dir: "same" as const, good: null };
  const good = better === "up" ? d > 0 : d < 0;
  return { d, dir: d > 0 ? ("up" as const) : ("down" as const), good };
}

export function ScorecardStrip({ score, baseline }: { score: Scorecard; baseline?: Scorecard | null }) {
  return (
    <div className="scorecard" role="group" aria-label="Alignment scorecard">
      {TILES.map((t) => {
        const v = t.value(score);
        const b = baseline ? t.value(baseline) : null;
        const dd = baseline ? delta(b, v, t.better) : null;
        const of = t.label === "Complete threads" ? ` / ${score.threads_complete.of}` : "";
        return (
          <div key={t.label} className="tile" title={t.title}>
            <div className="tile-label">{t.label}</div>
            <div className="tile-value">
              {v == null ? "—" : t.fmt(v)}{of}
              {dd && dd.dir !== "same" && (
                <span className={`tile-delta ${dd.good ? "good" : "bad"}`}>
                  {dd.dir === "up" ? "▲" : "▼"} {t.fmt === pct ? pct(Math.abs(dd.d)) : Math.abs(dd.d)}
                  <span className="sr-only"> {dd.good ? "better" : "worse"} than baseline</span>
                </span>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
