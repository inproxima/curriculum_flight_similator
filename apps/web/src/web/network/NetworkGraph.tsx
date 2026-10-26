import { forceCollide, forceX, forceY } from "d3-force";
import ForceGraph from "force-graph";
import { useEffect, useRef } from "react";
import type { WebEdge, WebNode, WebView } from "../../api/webHooks";
import { CONNECTION_COLOR, CONNECTION_GLYPH, CONNECTION_TEXT, PROVENANCE_TEXT, STATUS_COLOR, TYPE_COLOR, type ColorBy, type ConnectionLevel } from "../webStyle";
import {
  BAND,
  NET_STYLE,
  PICK_TOLERANCE,
  SHAPE_GEOM,
  focusOf,
  linkAlpha,
  linkShown,
  nodeShown,
  pickAt,
  radius,
  shortLabel,
  showLabel,
  yearX,
  type Box,
  type DrawState,
  type Focus,
  type NetHit,
  type NetVis,
  type PickLink,
  type PickNode,
  type Shape,
} from "./networkModel";

interface NNode {
  id: string;
  n: WebNode;
  r: number;
  /** the box of its drawn name (program outcomes and courses), so a click on the name picks it; null when not drawn */
  lb?: Box | null;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
  fx?: number;
  fy?: number;
}
interface NLink {
  id: string;
  e: WebEdge;
  source: string | NNode;
  target: string | NNode;
  containment: boolean;
  horizontal: boolean;
}

export interface NetworkProps {
  view: WebView;
  vis: NetVis;
  /** kept lit (selection, a highlighted finding); everything else fades */
  focus: Focus | null;
  selectedId: string | null;
  colorBy: ColorBy;
  arrangement: "years" | "clusters";
  spread: number;
  showGaps: boolean;
  hideRest: boolean;
  isolate: boolean;
  diff: boolean;
  fitNonce: number;
  /** bumped when a selection or highlight made elsewhere (search, the Inspector, a finding) should be brought into
   *  view; clicks on the canvas never move the camera */
  revealNonce: number;
  /** a click on the canvas: an element, a link, or the background (null) */
  onPick: (hit: NetHit) => void;
}

const SELECT_RING = "#c77700";
const GAP_NONE = "#b42318";
const GAP_UNMAPPED = "#8a93a3";
const PRIMARY: Record<string, string> = { course_outcome: "contributes_to", assessment: "assesses", topic: "supports" };

function rgba(hex: string, a: number): string {
  const h = hex.replace("#", "");
  const v = parseInt(h.length === 3 ? h.split("").map((c) => c + c).join("") : h, 16);
  return `rgba(${(v >> 16) & 255},${(v >> 8) & 255},${v & 255},${Math.max(0, Math.min(1, a)).toFixed(3)})`;
}

function shapePath(ctx: CanvasRenderingContext2D, shape: Shape, x: number, y: number, r: number) {
  ctx.beginPath();
  if (shape === "circle") ctx.arc(x, y, r, 0, 2 * Math.PI);
  else if (shape === "square") {
    const h = r * SHAPE_GEOM.square;
    ctx.rect(x - h, y - h, h * 2, h * 2);
  } else if (shape === "diamond") {
    const d = r * SHAPE_GEOM.diamond;
    ctx.moveTo(x, y - d);
    ctx.lineTo(x + d, y);
    ctx.lineTo(x, y + d);
    ctx.lineTo(x - d, y);
    ctx.closePath();
  } else if (shape === "hexagon") {
    for (let i = 0; i < 6; i++) {
      const a = (Math.PI / 3) * i + Math.PI / 6;
      const px = x + r * SHAPE_GEOM.hexagon * Math.cos(a);
      const py = y + r * SHAPE_GEOM.hexagon * Math.sin(a);
      if (i) ctx.lineTo(px, py);
      else ctx.moveTo(px, py);
    }
    ctx.closePath();
  } else {
    const w = r * SHAPE_GEOM.boxW;
    const h = r * SHAPE_GEOM.boxH;
    ctx.roundRect(x - w, y - h, w * 2, h * 2, r * SHAPE_GEOM.boxCorner);
  }
}

function tip(lines: (string | [string, string])[]): HTMLElement {
  const box = document.createElement("div");
  box.className = "net-tip";
  for (const l of lines) {
    const d = document.createElement("div");
    if (Array.isArray(l)) {
      d.className = l[0];
      d.textContent = l[1];
    } else d.textContent = l;
    box.appendChild(d);
  }
  return box;
}

function nodeTip(n: WebNode, courseKey: string | undefined): HTMLElement {
  const style = NET_STYLE[n.type];
  const lines: (string | [string, string])[] = [
    ["net-tip-type", `${style?.label ?? n.type}${courseKey ? ` · ${courseKey}` : ""}${n.year ? ` · Year ${n.year}` : ""}`],
    ["net-tip-key", n.key],
  ];
  if (n.title !== n.key) lines.push(n.title.length > 180 ? `${n.title.slice(0, 179)}…` : n.title);
  const kind = PRIMARY[n.type];
  const m = kind ? n.mapping?.[kind] : undefined;
  if (m?.best) lines.push(["net-tip-ok", `${CONNECTION_GLYPH[m.best]} ${CONNECTION_TEXT[m.best]} connection (${m.count})`]);
  else if (m?.verdict === "none") lines.push(["net-tip-none", `✗ AI found no connection: ${m.reason ?? ""}`]);
  else if (m) lines.push(["net-tip-muted", "? not mapped yet"]);
  lines.push(["net-tip-muted", "Click to select · the Inspector shows everything about it"]);
  return tip(lines);
}

function linkTip(e: WebEdge, byId: Map<string, WebNode>): HTMLElement {
  const lines: (string | [string, string])[] = [
    ["net-tip-key", `${byId.get(e.source)?.key ?? "?"} → ${byId.get(e.target)?.key ?? "?"}`],
    e.type.replace(/_/g, " "),
  ];
  if (e.connection) lines.push(["net-tip-ok", `${CONNECTION_GLYPH[e.connection as ConnectionLevel]} ${CONNECTION_TEXT[e.connection as ConnectionLevel]} connection`]);
  lines.push(["net-tip-muted", `${PROVENANCE_TEXT[e.provenance] ?? e.provenance}${e.provenance === "proposed" ? " (AI?)" : ""}`]);
  lines.push(["net-tip-muted", "Click to see why and to review it"]);
  return tip(lines);
}

type Tunable = { distance: (f: (l: NLink) => number) => Tunable; strength: (f: ((x: never) => number) | number) => Tunable; distanceMax?: (d: number) => Tunable };

/** Forces for an arrangement: "years" pulls each course to its year band (members follow their course) and keeps
 *  program outcomes in a row above; "clusters" lets the structure find its own shape. The link and charge forces are
 *  force-graph's own, tuned in place, so they keep their links. */
function applyForces(g: ForceGraph<NNode, NLink>, arrangement: "years" | "clusters", spread: number, byId: Map<string, WebNode>, lastYear: number) {
  const plo = (n: NNode) => n.n.type === "program_outcome";
  const anchor = (n: NNode) => n.n.type === "course";
  const link = g.d3Force("link") as unknown as Tunable | undefined;
  link?.distance((l) => (l.containment ? 24 : l.horizontal ? 180 : 120) * spread).strength(((l: NLink) => (l.containment ? 0.9 : l.horizontal ? 0.004 : 0.02)) as never);
  const charge = g.d3Force("charge") as unknown as Tunable | undefined;
  charge?.strength(((n: NNode) => -(plo(n) ? 320 : anchor(n) ? 200 : 26) * spread) as never);
  charge?.distanceMax?.(1100);
  g.d3Force("collide", forceCollide<NNode>((n) => n.r + 1.6) as never)
    .d3Force(
      "x",
      (arrangement === "years"
        ? forceX<NNode>((n) => yearX(n.n, byId, lastYear)).strength((n) => (plo(n) ? 0 : anchor(n) ? 0.4 : 0.05))
        : forceX<NNode>(0).strength(0.02)) as never,
    )
    // years: courses held in a band below the program-outcome row (members follow their course)
    .d3Force("y", forceY<NNode>(0).strength((n) => (arrangement === "years" ? (anchor(n) ? 0.12 : plo(n) ? 0 : 0.015) : 0.02)) as never)
    .d3Force("center", null);
}

/** Force-directed network of the whole web on a canvas. Positions persist across data refreshes; layers, hover
 *  and selection only change how things are drawn.
 *
 *  force-graph draws, pans, zooms and drags; hover and clicks are resolved here with `pickAt` on the live positions.
 *  Its own hit-testing reads a hidden canvas repainted at most every 800 ms (stale after any zoom or pan), keeps links
 *  clickable after they fade out, gives small elements a hit area of a pixel or two, and drops a click whose pointer
 *  moved at all while pressed. */
export function NetworkGraph(p: NetworkProps) {
  const box = useRef<HTMLDivElement>(null);
  const tipBox = useRef<HTMLDivElement>(null);
  const graph = useRef<ForceGraph<NNode, NLink> | null>(null);
  const props = useRef(p);
  props.current = p;
  /** the hover preview: what the element under the pointer lights */
  const hover = useRef<Focus | null>(null);
  /** what the pointer is over (drives the tooltip and the hover preview) */
  const hovered = useRef<NetHit>(null);
  /** re-pick under a still pointer when the content moves or what can be picked changes */
  const repick = useRef(() => {});
  const zoom = useRef(1);
  const nodes = useRef(new Map<string, NNode>());
  const shape = useRef({ sig: "", byId: new Map<string, WebNode>(), lastYear: 4 });
  const fitted = useRef(false);
  const settled = useRef(false);

  const kick = () => {
    const g = graph.current;
    if (!g) return;
    // re-setting style accessors redraws (and re-seeds link particles) without reheating the layout
    g.linkWidth((l) => linkWidth(l)).linkDirectionalParticles((l) => particles(l));
  };
  const particles = (l: NLink) => (lit()?.links.has(l.id) && !l.containment ? 2 : 0);

  /** the selection or highlight, when it lights something */
  const held = () => (props.current.focus && props.current.focus.nodes.size ? props.current.focus : null);
  const lit = () => hover.current ?? held();
  const drawState = (): DrawState => {
    const { vis, focus, hideRest, isolate } = props.current;
    return { vis, focus, lit: lit(), hideRest, isolate };
  };

  /** What a click at a canvas point (CSS pixels) would pick. Only what can be seen is offered: while a selection or
   *  highlight lights part of the web, only its links, and only lit elements reach beyond their shape (the hover
   *  preview does not count: it would change what is pickable under the pointer). */
  const hitAt = (sx: number, sy: number, pointer: string): NetHit => {
    const g = graph.current;
    if (!g) return null;
    const f = held();
    const s: DrawState = { ...drawState(), lit: f };
    const at = g.screen2GraphCoords(sx, sy);
    const pn: PickNode[] = [];
    for (const nd of g.graphData().nodes) {
      if (nd.x === undefined || nd.y === undefined || !nodeShown(nd.id, s)) continue;
      const near = f ? f.nodes.has(nd.id) : (s.vis.node.get(nd.id) ?? "faded") !== "faded";
      pn.push({ id: nd.id, x: nd.x, y: nd.y, r: nd.r, shape: NET_STYLE[nd.n.type]?.shape ?? "circle", near, label: nd.lb ?? null });
    }
    const pl: PickLink[] = [];
    for (const l of g.graphData().links) {
      const a = l.source as NNode;
      const b = l.target as NNode;
      if (a.x === undefined || a.y === undefined || b.x === undefined || b.y === undefined) continue;
      if ((f && !f.links.has(l.id)) || !linkShown(l.id, s)) continue;
      pl.push({ id: l.id, x1: a.x, y1: a.y, x2: b.x, y2: b.y });
    }
    return pickAt(at.x, at.y, g.zoom(), pn, pl, pointer === "touch" ? PICK_TOLERANCE.touch : PICK_TOLERANCE.mouse);
  };

  const linkBase = (l: NLink) => {
    const { colorBy, diff } = props.current;
    const e = l.e;
    if (diff && e.status && e.status !== "unchanged") return STATUS_COLOR[e.status] ?? "#8a96a8";
    if (colorBy === "connection" && e.connection) return CONNECTION_COLOR[e.connection as ConnectionLevel];
    return TYPE_COLOR[e.type] ?? "#8a96a8";
  };
  function linkWidth(l: NLink): number {
    const f = lit();
    const on = !!f?.links.has(l.id);
    const label = l.e.strength?.label;
    const w = l.containment ? 0.6 : label === "strong" ? 1.7 : label === "moderate" ? 1.2 : 0.85;
    return on ? w + 1.2 : w;
  }

  // create once
  useEffect(() => {
    const el = box.current!;
    const g = new ForceGraph<NNode, NLink>(el)
      .nodeId("id")
      .backgroundColor("rgba(0,0,0,0)")
      .minZoom(0.08)
      .maxZoom(12)
      .warmupTicks(90)
      .cooldownTicks(420)
      .d3AlphaDecay(0.028)
      .d3VelocityDecay(0.34)
      .nodeCanvasObjectMode(() => "replace")
      .nodeVisibility((n) => nodeShown(n.id, drawState()))
      .linkVisibility((l) => linkShown(l.id, drawState()))
      .linkColor((l) => {
        const f = lit();
        const factor = props.current.vis.link.get(l.id) ?? 0;
        const base = linkAlpha(props.current.vis.drawn);
        const a = f ? (f.links.has(l.id) ? 0.92 : 0.03) : (l.containment ? Math.min(base, 0.14) : base) * factor;
        return rgba(linkBase(l), a);
      })
      .linkWidth((l) => linkWidth(l))
      .linkLineDash((l) => {
        const k = zoom.current || 1;
        const pv = l.e.provenance;
        return pv === "proposed" ? [4 / k, 3 / k] : pv === "accepted_inference" ? [9 / k, 3 / k] : null;
      })
      .linkDirectionalParticles((l) => particles(l))
      .linkDirectionalParticleWidth(2.6)
      .linkDirectionalParticleSpeed(0.007)
      .linkDirectionalParticleColor((l) => linkBase(l))
      // the tooltip and the pointer cursor are ours (see hitAt)
      .nodeLabel(() => "")
      .linkLabel(() => "")
      .showPointerCursor(false)
      .nodeCanvasObject((node, ctx, k) => {
        const { vis, selectedId, showGaps, diff, focus } = props.current;
        const n = node.n;
        const x = node.x ?? 0;
        const y = node.y ?? 0;
        const st = vis.node.get(node.id) ?? "faded";
        const f = lit();
        const on = !!f?.nodes.has(node.id) || node.id === selectedId;
        let alpha = st === "bright" ? 1 : st === "context" ? 0.5 : 0.13;
        if (f && !on) alpha = Math.min(alpha, 0.09);
        if (on) alpha = Math.max(alpha, st === "faded" ? 0.55 : 1);
        const style = NET_STYLE[n.type] ?? { color: "#8a96a8", shape: "circle" as Shape };
        const r = node.r;
        ctx.save();
        ctx.globalAlpha = alpha;
        if (diff && n.status && n.status !== "unchanged") {
          shapePath(ctx, style.shape, x, y, r + 4 / Math.max(k, 0.6));
          ctx.fillStyle = rgba(STATUS_COLOR[n.status] ?? "#999", 0.45);
          ctx.fill();
        }
        if (node.id === selectedId || (focus && focus.nodes.size === 1 && focus.nodes.has(node.id))) {
          ctx.beginPath();
          ctx.arc(x, y, r + 5 / Math.max(k, 0.5), 0, 2 * Math.PI);
          ctx.strokeStyle = SELECT_RING;
          ctx.lineWidth = 2.4 / k;
          ctx.stroke();
        }
        const kind = PRIMARY[n.type];
        const m = kind ? n.mapping?.[kind] : undefined;
        if (showGaps && m && !m.best) {
          ctx.beginPath();
          ctx.arc(x, y, r + 2.4, 0, 2 * Math.PI);
          ctx.setLineDash(m.verdict === "none" ? [] : [2 / k, 2 / k]);
          ctx.strokeStyle = m.verdict === "none" ? GAP_NONE : GAP_UNMAPPED;
          ctx.lineWidth = (m.verdict === "none" ? 1.8 : 1.2) / k;
          ctx.stroke();
          ctx.setLineDash([]);
        }
        shapePath(ctx, style.shape, x, y, r);
        ctx.fillStyle = style.color;
        ctx.fill();
        ctx.strokeStyle = "rgba(255,255,255,0.9)";
        ctx.lineWidth = 1 / k;
        ctx.stroke();
        node.lb = null;
        if (showLabel(n, k, on) && alpha > 0.3) {
          const anchor = n.type === "program_outcome" || n.type === "course";
          const px = anchor ? 12 : 10.5;
          ctx.font = `${anchor ? 600 : 400} ${px / k}px system-ui, -apple-system, "Segoe UI", sans-serif`;
          ctx.textAlign = "center";
          ctx.textBaseline = "top";
          const text = shortLabel(n);
          const ty = y + r * 1.3 + 1.5 / k;
          ctx.lineWidth = 3 / k;
          ctx.strokeStyle = "rgba(255,255,255,0.92)";
          ctx.strokeText(text, x, ty);
          ctx.fillStyle = "#1d2433";
          ctx.fillText(text, x, ty);
          if (anchor) {
            // program outcome and course names can be clicked (an element's small label would cover its neighbours)
            const half = ctx.measureText(text).width / 2 + 2 / k;
            node.lb = { x0: x - half, x1: x + half, y0: ty - 1 / k, y1: ty + (px * 1.25) / k };
          }
        }
        ctx.restore();
      })
      // force-graph's own hit area, used only to start dragging an element
      .nodePointerAreaPaint((node, color, ctx) => {
        ctx.beginPath();
        ctx.arc(node.x ?? 0, node.y ?? 0, node.r + 2.5, 0, 2 * Math.PI);
        ctx.fillStyle = color;
        ctx.fill();
      })
      .onEngineStop(() => {
        if (!fitted.current) {
          fitted.current = true;
          g.zoomToFit(500, 40);
          window.setTimeout(() => (settled.current = true), 650); // after the fit animation
        } else settled.current = true;
      });
    graph.current = g;

    // Hover and click. A press that moves less than a few pixels is a click on what was under it when pressed (what the
    // tooltip showed); more is a pan or a drag. Pointer events only, never captured or prevented: d3-zoom pans on the
    // mouse events that follow them.
    const tipEl = tipBox.current!;
    let pointer: { x: number; y: number; type: string } | null = null; // a hovering pointer, client coordinates
    let press: { id: number; x: number; y: number; hit: NetHit; moved: boolean; clickPx: number } | null = null;
    let frame = 0;
    const local = (cx: number, cy: number) => {
      const r = el.getBoundingClientRect();
      return { x: cx - r.left, y: cy - r.top, w: r.width, h: r.height };
    };
    const setHover = (hit: NetHit) => {
      const cur = hovered.current;
      if (hit?.kind === cur?.kind && hit?.id === cur?.id) return;
      hovered.current = hit;
      const { view, vis } = props.current;
      hover.current = hit?.kind === "node" ? focusOf(view, hit.id, vis) : null;
      el.style.cursor = hit ? "pointer" : "";
      let content: HTMLElement | null = null;
      if (hit?.kind === "node") {
        const n = shape.current.byId.get(hit.id);
        const owner = n?.course_ids?.[0] ?? n?.course_id;
        content = n ? nodeTip(n, owner ? shape.current.byId.get(owner)?.key : undefined) : null;
      } else if (hit) {
        const e = g.graphData().links.find((l) => l.id === hit.id)?.e;
        content = e ? linkTip(e, shape.current.byId) : null;
      }
      tipEl.replaceChildren(...(content ? [content] : []));
      tipEl.hidden = !content;
      kick();
    };
    const pickHere = () => {
      frame = 0;
      if (!pointer || press) return;
      const { x, y, w, h } = local(pointer.x, pointer.y);
      if (x < 0 || y < 0 || x > w || y > h) return setHover(null);
      const hit = hitAt(x, y, pointer.type);
      setHover(hit);
      if (!hit) return;
      // beside the pointer, kept inside the canvas: shifted left as the pointer nears the right edge, above near the bottom
      tipEl.style.left = `${x}px`;
      tipEl.style.top = `${y}px`;
      tipEl.style.transform = `translate(-${Math.round((x / Math.max(w, 1)) * 100)}%, ${h > 130 && h - y < 110 ? "calc(-100% - 10px)" : "18px"})`;
      tipEl.hidden = !tipEl.childElementCount;
    };
    const schedulePick = () => {
      if (!frame && pointer && !press) frame = requestAnimationFrame(pickHere);
    };
    repick.current = schedulePick;
    const onMove = (ev: PointerEvent) => {
      if (ev.pointerType === "touch") return; // no hover on touch
      pointer = { x: ev.clientX, y: ev.clientY, type: ev.pointerType };
      schedulePick();
    };
    const onLeave = () => {
      pointer = null;
      if (!press) setHover(null);
    };
    const onPressMove = (ev: PointerEvent) => {
      if (press && ev.pointerId === press.id && Math.hypot(ev.clientX - press.x, ev.clientY - press.y) > press.clickPx) press.moved = true;
    };
    const endPress = () => {
      press = null;
      window.removeEventListener("pointermove", onPressMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", endPress);
      if (pointer) schedulePick();
      else setHover(null);
    };
    function onUp(ev: PointerEvent) {
      if (!press || ev.pointerId !== press.id) return;
      const { hit, moved, x, y, clickPx } = press;
      endPress();
      if (!moved && Math.hypot(ev.clientX - x, ev.clientY - y) <= clickPx) props.current.onPick(hit);
    }
    const onDown = (ev: PointerEvent) => {
      if (press) {
        press.moved = true; // a second finger: a pinch, not a click
        return;
      }
      if (ev.button !== 0 || !ev.isPrimary || ev.ctrlKey) return;
      const { x, y } = local(ev.clientX, ev.clientY);
      const tol = ev.pointerType === "touch" ? PICK_TOLERANCE.touch : PICK_TOLERANCE.mouse;
      press = { id: ev.pointerId, x: ev.clientX, y: ev.clientY, hit: hitAt(x, y, ev.pointerType), moved: false, clickPx: tol.clickPx };
      tipEl.hidden = true;
      window.addEventListener("pointermove", onPressMove);
      window.addEventListener("pointerup", onUp);
      window.addEventListener("pointercancel", endPress);
    };
    el.addEventListener("pointermove", onMove);
    el.addEventListener("pointerleave", onLeave);
    el.addEventListener("pointerdown", onDown);
    // the content moves under a still pointer while zooming and while the layout settles
    g.onZoom(({ k }) => {
      zoom.current = k;
      schedulePick();
    }).onEngineTick(schedulePick);

    const ro = new ResizeObserver(() => {
      // force-graph keeps the centre when the canvas resizes; keep the top-left instead, so nothing moves under the
      // pointer when a panel beside or below it grows (the change composer gains a line when something is selected)
      const corner = g.screen2GraphCoords(0, 0);
      const [w, h] = [el.clientWidth, el.clientHeight];
      g.width(w).height(h);
      if (fitted.current) g.centerAt(corner.x + w / 2 / g.zoom(), corner.y + h / 2 / g.zoom());
    });
    ro.observe(el);
    if (import.meta.env.DEV) {
      // end-to-end tests find nodes on the canvas through this hook (development builds only)
      (el as unknown as { __net: unknown }).__net = {
        screenOf: (id: string) => {
          const nd = nodes.current.get(id);
          return nd && nd.x !== undefined && nd.y !== undefined ? g.graph2ScreenCoords(nd.x, nd.y) : null;
        },
        zoom: () => zoom.current,
        idOf: (key: string) => [...nodes.current.values()].find((nd) => nd.n.key === key)?.id ?? null,
        settled: () => settled.current,
        /** what a click at this canvas point (CSS pixels) would pick */
        pickAt: (x: number, y: number) => hitAt(x, y, "mouse"),
        list: () => [...nodes.current.values()].map((nd) => ({ id: nd.id, key: nd.n.key, type: nd.n.type, title: nd.n.title })),
        /** links with whether they are drawn and lit (by the selection or highlight) and their midpoint on screen */
        links: () => {
          const f = held();
          const s: DrawState = { ...drawState(), lit: f };
          return g.graphData().links.map((l) => {
            const a = l.source as NNode;
            const b = l.target as NNode;
            const ok = a.x !== undefined && a.y !== undefined && b.x !== undefined && b.y !== undefined;
            return { id: l.id, drawn: linkShown(l.id, s), lit: !!f?.links.has(l.id), mid: ok ? g.graph2ScreenCoords((a.x! + b.x!) / 2, (a.y! + b.y!) / 2) : null };
          });
        },
        graph: g,
      };
    }
    return () => {
      el.removeEventListener("pointermove", onMove);
      el.removeEventListener("pointerleave", onLeave);
      el.removeEventListener("pointerdown", onDown);
      window.removeEventListener("pointermove", onPressMove);
      window.removeEventListener("pointerup", onUp);
      window.removeEventListener("pointercancel", endPress);
      if (frame) cancelAnimationFrame(frame);
      repick.current = () => {};
      hovered.current = null;
      hover.current = null;
      tipEl.hidden = true;
      el.style.cursor = "";
      ro.disconnect();
      g._destructor();
      graph.current = null;
      shape.current.sig = ""; // a new instance (e.g. React's development remount) must receive the data again
      fitted.current = false;
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // data: keep node objects (and their positions) across refreshes; only a new set of ids re-runs the layout
  useEffect(() => {
    const g = graph.current;
    if (!g) return;
    const { view } = p;
    const byId = new Map(view.nodes.map((n) => [n.id, n]));
    const edges = view.edges.filter((e) => !e.bundle && byId.has(e.source) && byId.has(e.target));
    const degree = new Map<string, number>();
    for (const e of edges) {
      if (e.category === "containment") continue;
      degree.set(e.source, (degree.get(e.source) ?? 0) + 1);
      degree.set(e.target, (degree.get(e.target) ?? 0) + 1);
    }
    const lastYear = Math.max(4, ...view.nodes.map((n) => (n.type === "course" ? n.year ?? 0 : 0)));
    shape.current.byId = byId;
    shape.current.lastYear = lastYear;
    const sig = `${view.nodes.map((n) => n.id).join(",")}|${edges.map((e) => e.id).join(",")}`;
    const prev = nodes.current;
    const next = new Map<string, NNode>();
    const courseAt = new Map<string, { x: number; y: number }>();
    for (const n of view.nodes) {
      const old = prev.get(n.id);
      if (old && n.type === "course" && old.x !== undefined) courseAt.set(n.id, { x: old.x, y: old.y ?? 0 });
    }
    for (const n of view.nodes) {
      const old = prev.get(n.id);
      const nd: NNode = old ?? { id: n.id, n, r: 4 };
      nd.n = n;
      nd.r = radius(n, degree.get(n.id) ?? 0);
      if (nd.x === undefined) {
        const owner = n.course_ids?.[0] ?? n.course_id;
        const near = owner ? courseAt.get(owner) : undefined;
        nd.x = near ? near.x + (Math.random() - 0.5) * 60 : yearX(n, byId, lastYear) + (Math.random() - 0.5) * 120;
        nd.y = near ? near.y + (Math.random() - 0.5) * 60 : (Math.random() - 0.5) * 500;
      }
      next.set(n.id, nd);
    }
    nodes.current = next;
    if (sig === shape.current.sig) {
      // same elements and links: refresh what they say (review decisions, strengths) without moving anything
      const byLink = new Map(edges.map((e) => [e.id, e]));
      for (const l of g.graphData().links) l.e = byLink.get(l.id) ?? l.e;
      kick();
      return;
    }
    shape.current.sig = sig;
    const hz = new Set(["prepares_for", "formal_prerequisite", "corequisite", "inferred_preparation"]);
    const links: NLink[] = edges.map((e) => ({
      id: e.id,
      e,
      source: e.source,
      target: e.target,
      containment: e.category === "containment",
      horizontal: hz.has(e.type),
    }));
    pinPlos(p.arrangement, p.spread);
    settled.current = false;
    g.graphData({ nodes: [...next.values()], links });
    applyForces(g, p.arrangement, p.spread, byId, lastYear);
  }, [p.view]); // eslint-disable-line react-hooks/exhaustive-deps

  /** Program outcomes: a row above the year bands, or free hubs in clusters. */
  const pinPlos = (arrangement: "years" | "clusters", spread: number) => {
    const { lastYear } = shape.current;
    const plos = [...nodes.current.values()].filter((n) => n.n.type === "program_outcome").sort((a, b) => a.n.key.localeCompare(b.n.key, undefined, { numeric: true }));
    plos.forEach((n, i) => {
      if (arrangement === "years") {
        n.fx = plos.length > 1 ? (i / (plos.length - 1)) * (lastYear - 0.4) * BAND - BAND * 0.3 : 0;
        n.fy = -BAND * 1.05 * spread;
      } else {
        n.fx = undefined;
        n.fy = undefined;
      }
    });
  };

  // arrangement and spacing (not on first render: the data effect set them up)
  const firstArrange = useRef(true);
  useEffect(() => {
    const g = graph.current;
    if (firstArrange.current) {
      firstArrange.current = false;
      return;
    }
    if (!g) return;
    pinPlos(p.arrangement, p.spread);
    applyForces(g, p.arrangement, p.spread, shape.current.byId, shape.current.lastYear);
    settled.current = false;
    g.d3ReheatSimulation();
    fitted.current = false;
  }, [p.arrangement, p.spread]); // eslint-disable-line react-hooks/exhaustive-deps

  // anything that only changes the drawing (and what the pointer can pick)
  useEffect(() => {
    const h = hovered.current;
    if (h?.kind === "node") hover.current = focusOf(p.view, h.id, p.vis);
    kick();
    repick.current();
  }, [p.vis, p.focus, p.selectedId, p.colorBy, p.showGaps, p.hideRest, p.isolate, p.diff]); // eslint-disable-line react-hooks/exhaustive-deps

  // bring a selection or highlight made elsewhere (search, the Inspector, a finding) and what it lights into view
  useEffect(() => {
    const g = graph.current;
    const f = props.current.focus;
    if (!p.revealNonce || !g || !f || !f.nodes.size) return;
    if (f.nodes.size === 1) {
      const nd = nodes.current.get([...f.nodes][0]);
      if (nd?.x !== undefined && nd.y !== undefined) {
        g.centerAt(nd.x, nd.y, 600);
        g.zoom(Math.max(zoom.current, 2.4), 600);
      }
    } else g.zoomToFit(650, 70, (n) => f.nodes.has(n.id));
  }, [p.revealNonce]);

  useEffect(() => {
    if (p.fitNonce) graph.current?.zoomToFit(500, 40, (n) => (props.current.vis.node.get(n.id) ?? "faded") !== "faded");
  }, [p.fitNonce]);

  return (
    <>
      <div ref={box} className="net-canvas" />
      {/* force-graph empties .net-canvas when it starts, so the tooltip lives beside it */}
      <div ref={tipBox} className="net-tooltip" hidden aria-hidden="true" />
    </>
  );
}

