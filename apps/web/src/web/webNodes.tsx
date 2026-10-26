import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import type { WebGap, WebNode } from "../api/webHooks";
import {
  CONNECTION_COLOR,
  CONNECTION_GLYPH,
  CONNECTION_TEXT,
  STATUS_GLYPH,
  STATUS_TEXT,
  STRENGTH_TEXT,
  THREAD_TEXT,
  type ConnectionLevel,
} from "./webStyle";

export type WebNodeData = {
  node: WebNode;
  width: number;
  dim: boolean;
  hl: boolean;
  selected: boolean;
  diff: boolean;
  compact: boolean;
  gaps: WebGap[];
};
export type WebFlowNode = Node<WebNodeData>;
export type BandFlowNode = Node<{ label: string; width: number; height: number }, "band">;

const SIDES: [Position, string][] = [
  [Position.Top, "t"],
  [Position.Bottom, "b"],
  [Position.Left, "l"],
  [Position.Right, "r"],
];

function Handles() {
  return (
    <>
      {SIDES.map(([pos, id]) => (
        <span key={id}>
          <Handle className="web-handle" type="target" position={pos} id={`${id}-t`} isConnectable={false} />
          <Handle className="web-handle" type="source" position={pos} id={`${id}-s`} isConnectable={false} />
        </span>
      ))}
    </>
  );
}

function StrengthBadge({ label, score }: { label?: string | null; score?: number | null }) {
  if (!label) return null;
  return (
    <span className={`badge strength-${label}`} title={`Alignment ${STRENGTH_TEXT[label]}${score != null ? ` (${score.toFixed(2)})` : ""}`}>
      {label === "missing" ? "○" : label === "weak" ? "◔" : label === "moderate" ? "◑" : "●"} {STRENGTH_TEXT[label]}
    </span>
  );
}

function StatusBadge({ status, diff }: { status: string; diff: boolean }) {
  if (!status || status === "unchanged") return null;
  return (
    <span className={`badge status-${status} ${diff ? "" : "quiet"}`} title={STATUS_TEXT[status]}>
      {STATUS_GLYPH[status]} {STATUS_TEXT[status]}
    </span>
  );
}

function cls(d: WebNodeData, base: string) {
  const n = d.node;
  return [
    "web-node",
    base,
    d.dim ? "dim" : "",
    d.hl ? "hl" : "",
    d.selected ? "selected" : "",
    d.diff && n.status !== "unchanged" ? `st-${n.status}` : "",
    d.diff && n.status === "unchanged" ? "faded" : "",
    d.gaps.length ? "has-gap" : "",
    d.compact ? "compact" : "",
  ].join(" ");
}

/** Which link each element type is examined for, and how its states read on the node. */
const MAP_FOR: Record<string, { kind: string; none: string; linked: string; gap: string }> = {
  course_outcome: { kind: "contributes_to", none: "no program outcome", linked: "program outcome", gap: "clo_no_plo" },
  assessment: { kind: "assesses", none: "tests no outcome", linked: "tests an outcome", gap: "assessment_no_clo" },
  topic: { kind: "supports", none: "supports no outcome", linked: "supports an outcome", gap: "topic_no_clo" },
};
const MAPPED_GAPS = new Set(Object.values(MAP_FOR).map((m) => m.gap));

type Mapping = Record<string, { best?: ConnectionLevel | null; count?: number; verdict?: string | null; reason?: string | null }>;

/** The element's own connection state: best level found (colour + dots), "✗ none found" (the AI looked and found
 *  nothing), or "? not mapped" (not examined yet). */
export function MapFlag({ node }: { node: WebNode }) {
  const cfg = MAP_FOR[node.type];
  const m = (node.mapping as Mapping | null | undefined)?.[cfg?.kind ?? ""];
  if (!cfg) return null;
  if (m?.best) {
    const c = CONNECTION_COLOR[m.best];
    return (
      <span className="map-chip" style={{ color: c, borderColor: c }} title={`${CONNECTION_TEXT[m.best]} connection to ${cfg.linked} (${m.count} link(s))`}>
        {CONNECTION_GLYPH[m.best]} {CONNECTION_TEXT[m.best].split(" ")[0]}
      </span>
    );
  }
  if (m?.verdict === "none")
    return (
      <span className="map-chip none" title={`The AI found no connection: ${m.reason ?? ""}`}>
        ✗ {cfg.none}
      </span>
    );
  return (
    <span className="map-chip unmapped" title="Not examined by the AI mapping yet">
      ? not mapped
    </span>
  );
}

const HORIZONTAL = {
  prepared_by: { arrow: "←", linked: "Builds on earlier / prerequisite courses", none: "The AI found nothing earlier that prepares for it" },
  prepares_for: { arrow: "→", linked: "Prepares for later courses", none: "The AI found nothing later that builds on it" },
} as const;

/** Horizontal: whether a topic or outcome builds on an earlier (prerequisite) course (←), and whether something in a
 *  later course builds on it (→). Nothing is shown until it has been examined. */
export function HorizontalFlag({ node, kind }: { node: WebNode; kind: keyof typeof HORIZONTAL }) {
  const m = (node.mapping as Mapping | null | undefined)?.[kind];
  const h = HORIZONTAL[kind];
  if (!m) return null;
  if (m.best) {
    const c = CONNECTION_COLOR[m.best];
    return (
      <span className="map-chip" style={{ color: c, borderColor: c }} title={`${h.linked} (${m.count} link(s)); strongest: ${CONNECTION_TEXT[m.best]}`}>
        {h.arrow} {CONNECTION_GLYPH[m.best]}
      </span>
    );
  }
  if (m.verdict === "none")
    return (
      <span className="map-chip unmapped" title={`${h.none}: ${m.reason ?? ""}`}>
        {h.arrow} none
      </span>
    );
  return null;
}

/** Gaps the map flag already shows are not repeated. */
const otherGaps = (n: WebNode, gaps: WebGap[]) => (n.mapping ? gaps.filter((g) => !MAPPED_GAPS.has(g.kind)) : gaps);

const GapFlag = ({ gaps }: { gaps: WebGap[] }) =>
  gaps.length ? (
    <span className="badge gap" title={gaps.map((g) => g.message).join("\n")}>
      ⚠ {gaps.length === 1 ? gapShort(gaps[0].kind) : `${gaps.length} gaps`}
    </span>
  ) : null;

export function gapShort(kind: string): string {
  return (
    {
      clo_no_plo: "no program outcome",
      clo_unassessed: "not assessed",
      assessment_no_clo: "assesses nothing",
      topic_no_clo: "supports nothing",
      plo_no_clo: "no contributions",
      plo_never_assessed: "never assessed",
      course_pair_no_link: "no bridge",
    }[kind] ?? kind.replace(/_/g, " ")
  );
}

const Pip = ({ on, letter, title }: { on: boolean; letter: string; title: string }) => (
  <span className={`pip ${on ? "on" : "off"}`} title={title} aria-label={title}>{letter}</span>
);

export const PloNode = memo(function PloNode({ data }: NodeProps<WebFlowNode>) {
  const n = data.node;
  const m = n.metrics as { label?: string; score?: number | null; thread_status?: string; stages?: Record<string, string | null> };
  const st = m.stages ?? {};
  const [code, ...rest] = n.title.split(": ");
  return (
    <div className={cls(data, "plo")} style={{ width: data.width }}
      aria-label={`Program outcome ${n.title}. ${THREAD_TEXT[m.thread_status ?? ""] ?? ""}. Alignment ${m.label ?? "unknown"}.`}>
      <Handles />
      <div className="row" style={{ gap: 4 }}>
        <span className="code">{code}</span>
        <span className="pips">
          <Pip on={!!st.introduce} letter="I" title={st.introduce ? `Introduced in ${st.introduce}` : "Never introduced"} />
          <Pip on={!!st.reinforce} letter="R" title={st.reinforce ? `Reinforced in ${st.reinforce}` : "Never reinforced"} />
          <Pip on={!!st.assess} letter="A" title={st.assess ? `Assessed in ${st.assess}` : "Never assessed"} />
        </span>
      </div>
      <div className="title">{rest.join(": ") || n.title}</div>
      {!data.compact && (
        <div className="meta">
          <StrengthBadge label={m.label} score={m.score} />
          <StatusBadge status={n.status} diff={data.diff} />
          <GapFlag gaps={data.gaps} />
        </div>
      )}
    </div>
  );
});

export const CourseHeaderNode = memo(function CourseHeaderNode({ data }: NodeProps<WebFlowNode>) {
  const n = data.node;
  const m = n.metrics as { label?: string; score?: number | null };
  const a = n.aggregate;
  return (
    <div className={cls(data, `course ${n.classification ?? ""}`)} style={{ width: data.width }} aria-label={`Course ${n.key} ${n.title}`}>
      <Handles />
      <div className="row" style={{ gap: 6 }}>
        <span className="code">{n.key}</span>
        <span className="muted small">{n.year ? `Y${n.year}` : "Y?"} · {n.term ?? "?"}</span>
        {n.classification && n.classification !== "required" && <span className="badge">{n.classification.replace("_", " ")}</span>}
      </div>
      <div className="title" title={n.title}>{n.title}</div>
      {!data.compact && (
        <div className="meta">
          {a && <span className="muted small">{a.clos} outcomes · {a.assessments} assessments · {a.topics} topics</span>}
          <StrengthBadge label={m.label} score={m.score} />
          <StatusBadge status={n.status} diff={data.diff} />
        </div>
      )}
    </div>
  );
});

export const CloNode = memo(function CloNode({ data }: NodeProps<WebFlowNode>) {
  const n = data.node;
  const m = n.metrics as { label?: string; score?: number | null; assessed?: boolean; topic_support?: number; proposed_links?: number };
  return (
    <div className={cls(data, "clo")} style={{ width: data.width }} aria-label={`Course outcome ${n.key}: ${n.title}. Alignment ${m.label}.`}>
      <Handles />
      <div className="row" style={{ gap: 4 }}>
        <span className="code">{n.key}</span>
        {m.assessed ? <span className="tick" title="Assessed by at least one assessment">✓A</span> : <span className="tick warn" title="No linked assessment">✗A</span>}
        {(m.topic_support ?? 0) > 0 && <span className="tick" title={`${m.topic_support} topic(s) support this outcome`}>T{m.topic_support}</span>}
        {(m.proposed_links ?? 0) > 0 && <span className="tick ai" title={`${m.proposed_links} proposed link(s)`}>AI?{m.proposed_links}</span>}
      </div>
      {!data.compact && <div className="stmt">{n.title}</div>}
      <div className="meta">
        <MapFlag node={n} />
        <HorizontalFlag node={n} kind="prepared_by" />
        <HorizontalFlag node={n} kind="prepares_for" />
        <StrengthBadge label={m.label} score={m.score} />
        <StatusBadge status={n.status} diff={data.diff} />
        <GapFlag gaps={otherGaps(n, data.gaps).filter((g) => g.kind !== "clo_unassessed")} />
      </div>
    </div>
  );
});

const FORMAT_ICON: Record<string, string> = {
  exam: "📝",
  quiz: "❓",
  assignment: "📄",
  "written assignment": "📄",
  "lab report": "🧪",
  "group project": "👥",
  "oral presentation": "🎤",
  poster: "🖼",
  portfolio: "🗂",
  thesis: "📚",
  participation: "🙋",
  "reflective journal": "📓",
};

export const AssessmentNode = memo(function AssessmentNode({ data }: NodeProps<WebFlowNode>) {
  const n = data.node;
  const d = n.details as { format?: string; weight_percent?: number | null; timing_week?: number | null; documented_hours?: number | null };
  const m = n.metrics as { label?: string; score?: number | null };
  return (
    <div className={cls(data, "assessment")} style={{ width: data.width }}
      aria-label={`Assessment ${n.title}, ${d.format ?? "format unknown"}, ${d.weight_percent ?? "?"} percent. Alignment ${m.label}.`}>
      <Handles />
      <div className="row" style={{ gap: 4 }}>
        <span aria-hidden>{FORMAT_ICON[(d.format ?? "").toLowerCase()] ?? "📋"}</span>
        <span className="title strong" title={n.title}>{n.title}</span>
      </div>
      <div className="meta">
        <span className="muted small">
          {d.format ?? "format ?"} · {d.weight_percent != null ? `${d.weight_percent}%` : "weight ?"} · {d.timing_week ? `wk ${d.timing_week}` : "wk ?"}
          {d.documented_hours ? ` · ${d.documented_hours}h` : ""}
        </span>
        <MapFlag node={n} />
        {!data.compact && <StrengthBadge label={m.label} score={m.score} />}
        <StatusBadge status={n.status} diff={data.diff} />
        <GapFlag gaps={otherGaps(n, data.gaps)} />
      </div>
    </div>
  );
});

export const TopicNode = memo(function TopicNode({ data }: NodeProps<WebFlowNode>) {
  const n = data.node;
  return (
    <div className={cls(data, "topic")} style={{ width: data.width }} aria-label={`Topic ${n.title}`}>
      <Handles />
      <span className="muted small">Topic</span> <span className="title">{n.title}</span>
      {!data.compact && (
        <span className="topic-flags">
          <MapFlag node={n} />
          <HorizontalFlag node={n} kind="prepared_by" />
          <HorizontalFlag node={n} kind="prepares_for" />
        </span>
      )}
      <StatusBadge status={n.status} diff={data.diff} />
      <GapFlag gaps={otherGaps(n, data.gaps)} />
    </div>
  );
});

export const BandNode = memo(function BandNode({ data }: NodeProps<BandFlowNode>) {
  return (
    <div className="web-band" style={{ width: data.width, height: data.height }}>
      <div className="band-label">{data.label}</div>
    </div>
  );
});

export const webNodeTypes = {
  program_outcome: PloNode,
  course: CourseHeaderNode,
  course_outcome: CloNode,
  assessment: AssessmentNode,
  topic: TopicNode,
  band: BandNode,
};
