import { BaseEdge, EdgeLabelRenderer, getBezierPath, type Edge, type EdgeProps } from "@xyflow/react";
import { memo, useState } from "react";
import type { WebEdge, WebGap } from "../api/webHooks";
import { edgeLook } from "./webStyle";

export type StrengthEdgeData = { edge: WebEdge; diff: boolean; dim: boolean; hl: boolean; selected: boolean; pulse: boolean };
export type StrengthFlowEdge = Edge<StrengthEdgeData, "strength">;
export type GapFlowEdge = Edge<{ gap: WebGap; dim: boolean }, "gap">;

export const StrengthEdge = memo(function StrengthEdge(p: EdgeProps<StrengthFlowEdge>) {
  const d = p.data!;
  const [hover, setHover] = useState(false);
  const look = edgeLook(d.edge, { diff: d.diff, dim: d.dim, hl: d.hl || d.selected });
  const [path, lx, ly] = getBezierPath(p);
  const showChip = (hover || d.selected || d.hl || (d.diff && d.edge.status !== "unchanged")) && !d.dim && look.chip;
  return (
    <>
      <BaseEdge
        id={p.id}
        path={path}
        markerEnd={p.markerEnd}
        interactionWidth={14}
        className={d.pulse ? "pulse" : undefined}
        style={{ stroke: look.stroke, strokeWidth: look.width, strokeDasharray: look.dash, opacity: look.opacity }}
      />
      <path d={path} fill="none" stroke="transparent" strokeWidth={14} onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
        <title>{look.title}</title>
      </path>
      {showChip && (
        <EdgeLabelRenderer>
          <div className="edge-chip" style={{ transform: `translate(-50%, -50%) translate(${lx}px,${ly}px)`, borderColor: look.stroke, color: look.stroke }}>
            {look.chip}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});

export const GapEdge = memo(function GapEdge(p: EdgeProps<GapFlowEdge>) {
  const [path, lx, ly] = getBezierPath(p);
  const d = p.data!;
  return (
    <>
      <BaseEdge id={p.id} path={path} style={{ stroke: "#b03a3a", strokeWidth: 1.4, strokeDasharray: "2 4", opacity: d.dim ? 0.12 : 0.8 }} />
      {!d.dim && (
        <EdgeLabelRenderer>
          <div className="edge-chip gap" style={{ transform: `translate(-50%, -50%) translate(${lx}px,${ly}px)` }} title={d.gap.message}>
            ⚠ no link
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
});

export const webEdgeTypes = { strength: StrengthEdge, gap: GapEdge };
