import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeChange,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { GraphEdge, GraphNode, GraphView } from "../api/types";
import { edgeStyle, laneLabel, partitionOf } from "../lib/graphStyle";
import { perf } from "../lib/perf";
import { useUi } from "../store/ui";
import { runLayout } from "./layout";
import { Legend } from "./Legend";
import { nodeTypes } from "./nodes";

const COURSE_W = 200;
const COURSE_H = 74;
const MINI_W = 180;
const MINI_H = 28;

type Pos = Record<string, { x: number; y: number }>;

export function applyClientFilters(view: GraphView, f: ReturnType<typeof useUi.getState>["filters"]) {
  const courseOk = (n: GraphNode) =>
    (!f.years.length || (n.year != null && f.years.includes(n.year)) || (n.year == null && f.years.includes(0))) &&
    (!f.classifications.length || f.classifications.includes(n.classification ?? "unknown")) &&
    (!f.pathway || !n.pathway || n.pathway === f.pathway);
  const courses = new Map(view.nodes.filter((n) => n.type === "course").map((n) => [n.id, n]));
  const nodes = view.nodes.filter((n) => {
    if (n.type === "course") return courseOk(n);
    if (n.owner_course && courses.has(n.owner_course)) return courseOk(courses.get(n.owner_course)!);
    return true;
  });
  const ids = new Set(nodes.map((n) => n.id));
  const edges = view.edges.filter(
    (e) =>
      ids.has(e.source) &&
      ids.has(e.target) &&
      (e.status === "removed" || !f.reviewStates.length || f.reviewStates.includes(e.review_state)) &&
      (!f.basis.length || f.basis.includes(e.basis === "mixed" ? "interpretation" : e.basis)),
  );
  return { nodes, edges };
}

function MapInner({ view, savedPositions, onPositionsChange }: {
  view: GraphView;
  savedPositions: Pos;
  onPositionsChange: (p: Pos) => void;
}) {
  const filters = useUi((s) => s.filters);
  const selection = useUi((s) => s.selection);
  const multi = useUi((s) => s.multi);
  const highlight = useUi((s) => s.highlight);
  const select = useUi((s) => s.select);
  const rf = useReactFlow();
  const [auto, setAuto] = useState<Pos>({});
  const [overrides, setOverrides] = useState<Pos>(savedPositions);
  const [layoutMs, setLayoutMs] = useState<number | null>(null);
  const [dragPos, setDragPos] = useState<Pos>({});

  useEffect(() => setOverrides(savedPositions), [savedPositions]);

  const { nodes: gNodes, edges: gEdges } = useMemo(() => applyClientFilters(view, filters), [view, filters]);
  const courseById = useMemo(() => new Map(view.nodes.filter((n) => n.type === "course").map((n) => [n.id, n])), [view]);

  // Layout signature: only structural changes trigger re-layout (stable positions otherwise).
  const signature = useMemo(
    () => gNodes.map((n) => `${n.id}:${partitionOf(n, courseById)}`).sort().join("|") + "#" +
      gEdges.map((e) => `${e.source}>${e.target}`).sort().join("|"),
    [gNodes, gEdges, courseById],
  );
  const firstFit = useRef(true);
  useEffect(() => {
    let cancelled = false;
    const req = {
      nodes: gNodes.map((n) => ({
        id: n.id,
        width: n.type === "course" ? COURSE_W : MINI_W,
        height: n.type === "course" ? COURSE_H : MINI_H,
        partition: partitionOf(n, courseById),
      })),
      edges: gEdges
        .filter((e) => e.type !== "possible_overlap" && e.type !== "corequisite")
        .map((e) => ({ id: e.id, source: e.source, target: e.target })),
    };
    runLayout(req).then((res) => {
      if (cancelled) return;
      setAuto(res.positions);
      setLayoutMs(res.ms);
      perf.record("layout", res.ms, { nodes: req.nodes.length, edges: req.edges.length });
      if (firstFit.current) {
        firstFit.current = false;
        requestAnimationFrame(() => rf.fitView({ padding: 0.12 }));
      }
    });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature]);

  const hlNodes = useMemo(() => new Set(highlight.nodes), [highlight]);
  const hlEdges = useMemo(() => new Set(highlight.edges), [highlight]);
  const hlActive = highlight.mode !== null && (highlight.nodes.length > 0 || highlight.edges.length > 0);

  const nodes: Node[] = useMemo(() => {
    const pos = (id: string) => dragPos[id] ?? overrides[id] ?? auto[id];
    const out: Node[] = [];
    // lanes
    const lanes = new Map<number, { minX: number; maxX: number; minY: number; maxY: number }>();
    for (const n of gNodes) {
      const p = auto[n.id];
      if (!p) continue;
      const part = partitionOf(n, courseById);
      const w = n.type === "course" ? COURSE_W : MINI_W;
      const h = n.type === "course" ? COURSE_H : MINI_H;
      const l = lanes.get(part) ?? { minX: Infinity, maxX: -Infinity, minY: Infinity, maxY: -Infinity };
      l.minX = Math.min(l.minX, p.x);
      l.maxX = Math.max(l.maxX, p.x + w);
      l.minY = Math.min(l.minY, p.y);
      l.maxY = Math.max(l.maxY, p.y + h);
      lanes.set(part, l);
    }
    const allMinY = Math.min(...[...lanes.values()].map((l) => l.minY));
    const allMaxY = Math.max(...[...lanes.values()].map((l) => l.maxY));
    for (const [part, l] of lanes) {
      out.push({
        id: `lane:${part}`,
        type: "lane",
        position: { x: l.minX - 14, y: allMinY - 34 },
        data: { label: laneLabel(part), width: l.maxX - l.minX + 28, height: allMaxY - allMinY + 60 },
        draggable: false,
        selectable: false,
        focusable: false,
        zIndex: -1,
      });
    }
    for (const n of gNodes) {
      const p = pos(n.id);
      if (!p) continue;
      const selected = (selection?.kind === "node" && selection.id === n.id) || multi.includes(n.id);
      out.push({
        id: n.id,
        type: n.type === "course" ? "course" : "mini",
        position: p,
        data: { node: n, dim: hlActive && !hlNodes.has(n.id) && !selected, hl: hlNodes.has(n.id), selected },
        ariaLabel: `${n.type.replace("_", " ")} ${n.key}: ${n.title}`,
      });
    }
    return out;
  }, [gNodes, auto, overrides, dragPos, selection, multi, hlNodes, hlActive, courseById]);

  const edges: Edge[] = useMemo(
    () =>
      gEdges.map((e: GraphEdge) => {
        const hl = hlEdges.has(e.key ?? e.id) || e.via.some((k) => hlEdges.has(k));
        const selected = selection?.kind === "edge" && selection.id === e.id;
        return {
          id: e.id,
          source: e.source,
          target: e.target,
          type: "default",
          data: { edge: e },
          ariaLabel: `${e.type} from ${e.source} to ${e.target}`,
          ...edgeStyle(e, hlActive && !hl && !selected, hl || selected),
        };
      }),
    [gEdges, hlEdges, hlActive, selection],
  );

  const onNodesChange = useCallback((changes: NodeChange[]) => {
    for (const c of changes) {
      if (c.type === "position" && c.position && !c.id.startsWith("lane:")) {
        const { id, position, dragging } = c;
        if (dragging) setDragPos((d) => ({ ...d, [id]: position }));
        else {
          setDragPos((d) => {
            const { [id]: last, ...rest } = d;
            const final = position ?? last;
            if (final) {
              setOverrides((o) => {
                const next = { ...o, [id]: final };
                onPositionsChange(next);
                return next;
              });
            }
            return rest;
          });
        }
      }
    }
  }, [onPositionsChange]);

  return (
    <>
      <div className="map-toolbar">
        <button className="small" onClick={() => rf.fitView({ padding: 0.12 })}>Fit</button>
        <button
          className="small"
          title="Discard manual card positions and use the automatic layout"
          onClick={() => {
            setOverrides({});
            onPositionsChange({});
          }}
        >
          Reset layout
        </button>
        {layoutMs !== null && <span className="badge" title="Measured on this machine">layout {layoutMs.toFixed(0)} ms</span>}
      </div>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={(ev, n) => !n.id.startsWith("lane:") && select({ kind: "node", id: n.id }, ev.shiftKey || ev.metaKey)}
        onEdgeClick={(_, e) => select({ kind: "edge", id: e.id, key: (e.data as { edge: GraphEdge }).edge.key })}
        onPaneClick={() => select(null)}
        minZoom={0.1}
        maxZoom={2}
        nodesConnectable={false}
        elevateEdgesOnSelect
        proOptions={{ hideAttribution: true }}
        fitView
      >
        <Background gap={24} color="#e3e7ee" />
        <Controls showInteractive={false} />
        <MiniMap pannable zoomable nodeColor={(n) => (n.type === "lane" ? "transparent" : "#9aa7b8")} />
      </ReactFlow>
      <Legend />
    </>
  );
}

export function CurriculumMap(props: { view: GraphView; savedPositions: Pos; onPositionsChange: (p: Pos) => void }) {
  return (
    <ReactFlowProvider>
      <MapInner {...props} />
    </ReactFlowProvider>
  );
}
