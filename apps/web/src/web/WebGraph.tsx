import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
  type Edge,
  type Node,
  type Viewport,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo } from "react";
import type { WebEdge, WebNode, WebView } from "../api/webHooks";
import { useUi } from "../store/ui";
import { gapEdges, gapNodes } from "./webFilters";
import { SIZES, type WebLayout } from "./webLayout";
import { webEdgeTypes } from "./webEdges";
import { webNodeTypes } from "./webNodes";
import { edgeLook } from "./webStyle";

export interface WebGraphProps {
  view: WebView;
  layout: WebLayout;
  visible: { nodes: Set<string>; edges: Set<string> };
  emphasis: Set<string> | null;
  diff: boolean;
  pulse?: Set<string>;
  showGaps: boolean;
  selectedId: string | null;
  onNodeClick?: (n: WebNode) => void;
  onEdgeClick?: (e: WebEdge) => void;
  onPaneClick?: () => void;
  viewport?: Viewport;
  onViewportChange?: (v: Viewport) => void;
  ariaLabel?: string;
  minimap?: boolean;
}

/** Which side of each node an edge leaves from / arrives at, so vertical links read upward and horizontal ones left→right. */
export function handlesFor(e: WebEdge, src: WebNode | undefined, tgt: WebNode | undefined, layout: WebLayout): [string, string] {
  const ps = layout.positions[e.source];
  const pt = layout.positions[e.target];
  if (!ps || !pt || !src || !tgt) return ["r-s", "l-t"];
  if (tgt.type === "program_outcome") return ["t-s", "b-t"];
  if (e.category === "containment") return ["b-s", "t-t"];
  const sameColumn = Math.abs(ps.x - pt.x) < 1;
  if (sameColumn) return ps.y > pt.y ? ["r-s", "r-t"] : ["l-s", "l-t"];
  if (src.type === "topic" && !src.course_id) return ["t-s", "b-t"];
  return ps.x < pt.x ? ["r-s", "l-t"] : ["l-s", "r-t"];
}

function FitController() {
  const rf = useReactFlow();
  const req = useUi((s) => s.fitRequest);
  useEffect(() => {
    if (!req) return;
    const ids = new Set(req.ids);
    const nodes = rf.getNodes().filter((n) => ids.has(n.id));
    if (nodes.length) rf.fitView({ nodes, padding: 0.3, duration: 450, maxZoom: 1.2 });
  }, [req, rf]);
  return null;
}

function Inner(p: WebGraphProps) {
  const colorBy = useUi((s) => s.webFilters.colorBy ?? "connection");
  const zoom = useStore((s) => s.transform[2]);
  const compact = zoom < 0.45;
  const byId = useMemo(() => new Map(p.view.nodes.map((n) => [n.id, n])), [p.view.nodes]);
  const flagged = useMemo(() => (p.showGaps ? gapNodes(p.view.gaps) : new Map()), [p.view.gaps, p.showGaps]);

  const nodes = useMemo<Node[]>(() => {
    const out: Node[] = p.layout.bands.map((b, i) => ({
      id: `band:${i}`,
      type: "band",
      position: { x: b.x0 - 10, y: 120 },
      data: { label: b.label, width: b.x1 - b.x0 + 20, height: Math.max(200, p.layout.height - 120) },
      draggable: false,
      selectable: false,
      focusable: false,
      zIndex: -1,
    }));
    for (const n of p.view.nodes) {
      if (!p.visible.nodes.has(n.id)) continue;
      const pos = p.layout.positions[n.id];
      if (!pos) continue;
      out.push({
        id: n.id,
        type: n.type,
        position: pos,
        data: {
          node: n,
          width: p.layout.widths[n.id] ?? 200,
          dim: p.emphasis ? !p.emphasis.has(n.id) : false,
          hl: p.emphasis ? p.emphasis.has(n.id) : false,
          selected: p.selectedId === n.id,
          diff: p.diff,
          compact,
          gaps: flagged.get(n.id) ?? [],
        },
        style: { minHeight: SIZES[n.type] - 6 },
        draggable: false,
        ariaLabel: `${n.type.replace("_", " ")} ${n.key}`,
      });
    }
    return out;
  }, [p.view.nodes, p.visible.nodes, p.layout, p.emphasis, p.selectedId, p.diff, compact, flagged]);

  const edges = useMemo<Edge[]>(() => {
    const out: Edge[] = [];
    for (const e of p.view.edges) {
      if (!p.visible.edges.has(e.id)) continue;
      const src = byId.get(e.source);
      const tgt = byId.get(e.target);
      const [sh, th] = handlesFor(e, src, tgt, p.layout);
      const dim = p.emphasis ? !(p.emphasis.has(e.source) && p.emphasis.has(e.target)) : false;
      const hl = !!p.emphasis && !dim;
      const look = edgeLook(e, { diff: p.diff, dim, hl, colorBy });
      out.push({
        id: e.id,
        source: e.source,
        target: e.target,
        sourceHandle: sh,
        targetHandle: th,
        type: "strength",
        data: { edge: e, diff: p.diff, dim, hl, selected: p.selectedId === e.id, pulse: !!p.pulse?.has(e.id), colorBy },
        markerEnd: e.category === "containment" ? undefined : { type: MarkerType.ArrowClosed, color: look.stroke, width: 12, height: 12 },
        zIndex: hl || (p.diff && e.status !== "unchanged") ? 10 : e.category === "containment" ? 0 : 1,
        ariaLabel: look.title,
      });
    }
    if (p.showGaps) {
      for (const g of gapEdges(p.view.gaps, p.visible.nodes)) {
        const dim = p.emphasis ? !(p.emphasis.has(g.source) && p.emphasis.has(g.target)) : false;
        out.push({ id: g.id, source: g.source, target: g.target, sourceHandle: "r-s", targetHandle: "l-t", type: "gap", data: { gap: g.gap, dim } });
      }
    }
    return out;
  }, [p.view.edges, p.view.gaps, p.visible, byId, p.layout, p.emphasis, p.diff, p.selectedId, p.pulse, p.showGaps, colorBy]);

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      nodeTypes={webNodeTypes}
      edgeTypes={webEdgeTypes}
      onlyRenderVisibleElements
      nodesDraggable={false}
      nodesConnectable={false}
      minZoom={0.08}
      maxZoom={2}
      fitView
      fitViewOptions={{ padding: 0.08 }}
      viewport={p.viewport}
      onViewportChange={p.onViewportChange}
      onNodeClick={(_, n) => {
        const w = byId.get(n.id);
        if (w) p.onNodeClick?.(w);
      }}
      onEdgeClick={(_, e) => {
        const w = p.view.edges.find((x) => x.id === e.id);
        if (w) p.onEdgeClick?.(w);
      }}
      onPaneClick={p.onPaneClick}
      proOptions={{ hideAttribution: true }}
      aria-label={p.ariaLabel ?? "Alignment web"}
    >
      <Background gap={24} size={1} color="#e3e7ee" />
      <Controls showInteractive={false} position="bottom-right" />
      {p.minimap && <MiniMap pannable zoomable position="top-right" nodeColor={(n) => (n.type === "band" ? "transparent" : "#9aa7b8")} />}
      <FitController />
    </ReactFlow>
  );
}

export function WebGraph(p: WebGraphProps) {
  return (
    <ReactFlowProvider>
      <Inner {...p} />
    </ReactFlowProvider>
  );
}
