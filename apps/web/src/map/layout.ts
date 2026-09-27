/**
 * ELK layout, executed off the main thread in ELK's own web worker (elk-api + elk-worker).
 * Academic lanes are enforced with ELK partitioning: partitions pin year/term order left → right.
 */
import ELK from "elkjs/lib/elk-api.js";
import elkWorkerUrl from "elkjs/lib/elk-worker.min.js?url";

export interface LayoutNode {
  id: string;
  width: number;
  height: number;
  partition: number;
}
export interface LayoutEdge {
  id: string;
  source: string;
  target: string;
}
export interface LayoutRequest {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
}
export interface LayoutResponse {
  positions: Record<string, { x: number; y: number }>;
  ms: number;
}

let elk: InstanceType<typeof ELK> | null = null;

export async function runLayout({ nodes, edges }: LayoutRequest): Promise<LayoutResponse> {
  elk ??= new ELK({ workerUrl: elkWorkerUrl });
  const t0 = performance.now();
  const ids = new Set(nodes.map((n) => n.id));
  const res = await elk.layout({
    id: "root",
    layoutOptions: {
      "elk.algorithm": "layered",
      "elk.direction": "RIGHT",
      "elk.partitioning.activate": "true",
      "elk.layered.spacing.nodeNodeBetweenLayers": "90",
      "elk.spacing.nodeNode": "18",
      "elk.layered.nodePlacement.strategy": "BRANDES_KOEPF",
      "elk.separateConnectedComponents": "false",
    },
    children: nodes.map((n) => ({
      id: n.id,
      width: n.width,
      height: n.height,
      layoutOptions: { "elk.partitioning.partition": String(n.partition) },
    })),
    edges: edges
      .filter((e) => ids.has(e.source) && ids.has(e.target))
      .map((e) => ({ id: e.id, sources: [e.source], targets: [e.target] })),
  });
  const positions: Record<string, { x: number; y: number }> = {};
  for (const c of res.children ?? []) positions[c.id] = { x: c.x ?? 0, y: c.y ?? 0 };
  return { positions, ms: performance.now() - t0 };
}
