import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";
import { memo } from "react";
import type { GraphNode } from "../api/types";
import { CLASS_LABEL, TERM_LABEL } from "../lib/graphStyle";
import { useUi } from "../store/ui";

export type CourseNodeData = { node: GraphNode; dim: boolean; hl: boolean; selected: boolean };
export type CourseFlowNode = Node<CourseNodeData, "course">;
export type MiniFlowNode = Node<CourseNodeData, "mini">;
export type LaneFlowNode = Node<{ label: string; width: number; height: number }, "lane">;

const STATUS_TEXT: Record<string, string> = { added: "+ Added", removed: "− Removed", modified: "~ Modified" };

function EvidenceMeter({ n }: { n: GraphNode }) {
  const { fields, with_evidence } = n.evidence;
  if (!fields.length) return null;
  const full = with_evidence.length === fields.length;
  return (
    <span
      className={`badge ${full ? "documented" : "proposed"}`}
      title={`Fields with source evidence: ${with_evidence.join(", ") || "none"}. Missing: ${fields.filter((f) => !with_evidence.includes(f)).join(", ") || "none"}`}
    >
      {full ? "✓" : "◐"} {with_evidence.length}/{fields.length} sourced
    </span>
  );
}

export const CourseNode = memo(function CourseNode({ data }: NodeProps<CourseFlowNode>) {
  const { node: n, dim, hl, selected } = data;
  const toggle = useUi((s) => s.toggleExpanded);
  const cls = ["course-node", n.classification ?? "", dim ? "dim" : "", hl ? "hl" : "", selected ? "selected" : "",
    n.status !== "unchanged" ? n.status : ""].join(" ");
  return (
    <div className={cls} aria-label={`${n.key} ${n.title}`}>
      <Handle type="target" position={Position.Left} />
      <button
        className="expand"
        title={n.expanded ? "Collapse outcomes, topics, assessments" : "Expand outcomes, topics, assessments"}
        aria-label={n.expanded ? `Collapse ${n.key}` : `Expand ${n.key}`}
        onClick={(e) => {
          e.stopPropagation();
          toggle(n.id);
        }}
      >
        {n.expanded ? "−" : "+"}
      </button>
      <div className="code">{n.key}</div>
      <div className="title" title={n.title}>{n.title}</div>
      <div className="meta">
        <span className="badge">{CLASS_LABEL[n.classification ?? "unknown"] ?? n.classification}</span>
        {n.pathway && <span className="badge">{n.pathway}</span>}
        {(n.term === "unknown" || n.year == null) && <span className="badge unknown">{n.year == null ? "Year ?" : TERM_LABEL.unknown}</span>}
        <EvidenceMeter n={n} />
        {n.status !== "unchanged" && <span className={`badge ${n.status}`}>{STATUS_TEXT[n.status]}</span>}
        {n.is_synthetic && <span className="badge synthetic" title="Synthetic fixture — not real curriculum data">SYN</span>}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

const MINI_PREFIX: Record<string, string> = { program_outcome: "PLO", course_outcome: "Outcome", topic: "Topic", assessment: "Assessment" };

export const MiniNode = memo(function MiniNode({ data }: NodeProps<MiniFlowNode>) {
  const { node: n, dim, hl, selected } = data;
  const label = n.type === "program_outcome" ? n.title : n.type === "course_outcome" ? `${n.key}` : n.title;
  return (
    <div
      className={["mini-node", n.type, dim ? "dim" : "", hl ? "hl" : "", selected ? "selected" : ""].join(" ")}
      title={`${MINI_PREFIX[n.type] ?? n.type}: ${n.title}`}
    >
      <Handle type="target" position={Position.Left} />
      <span className="muted">{MINI_PREFIX[n.type]} </span>
      {label}
      {n.status !== "unchanged" && <span className={`badge ${n.status}`}> {STATUS_TEXT[n.status]}</span>}
      <Handle type="source" position={Position.Right} />
    </div>
  );
});

export const LaneNode = memo(function LaneNode({ data }: NodeProps<LaneFlowNode>) {
  return (
    <div className="lane-node" style={{ width: data.width, height: data.height }}>
      <div className="lane-label">{data.label}</div>
    </div>
  );
});

export const nodeTypes = { course: CourseNode, mini: MiniNode, lane: LaneNode };
