import { useEffect, useMemo, useRef, useState } from "react";
import type { WebView } from "../../api/webHooks";
import { useUi, type Selection } from "../../store/ui";
import { CONNECTION_COLOR, CONNECTION_GLYPH, CONNECTION_LEVELS, CONNECTION_TEXT } from "../webStyle";
import { NetworkGraph } from "./NetworkGraph";
import { LENSES, NET_STYLE, activeLens, focusOf, networkVisibility, type Focus, type NetHit, type Shape } from "./networkModel";

const TYPE_ORDER = ["program_outcome", "course", "course_outcome", "assessment", "topic"];
const PLURAL: Record<string, [string, string]> = {
  program_outcome: ["program outcome", "program outcomes"],
  course: ["course", "courses"],
  course_outcome: ["course outcome", "course outcomes"],
  assessment: ["assessment", "assessments"],
  topic: ["topic", "topics"],
};

/** The toolbar row above the canvas: switch between the network and the columns, and (network) what to show. */
export function WebViewBar({ view, onFit }: { view: WebView | undefined; onFit: () => void }) {
  const { webView, setWebView, webFilters, setWebFilters, net, setNet, selection, select, clearHighlight } = useUi();
  const lens = activeLens(webFilters);
  return (
    <div className="web-viewbar" role="toolbar" aria-label="Web view">
      <div className="seg" role="group" aria-label="Layout">
        <button aria-pressed={webView === "network"} className={webView === "network" ? "on" : ""} onClick={() => setWebView("network")}
          title="Force-directed network: hover or click to light up connections">Network</button>
        <button aria-pressed={webView === "columns"} className={webView === "columns" ? "on" : ""} onClick={() => setWebView("columns")}
          title="Year and term columns">Columns</button>
      </div>
      {webView === "network" && view && (
        <>
          <div className="seg lenses" role="group" aria-label="Show">
            <span className="muted small">Show</span>
            {LENSES.map((l) => (
              <button key={l.key} aria-pressed={lens === l.key} className={lens === l.key ? "on" : ""}
                onClick={() => {
                  // a new view starts from the whole picture, not from an old selection
                  setWebFilters({ nodeTypes: l.nodeTypes, linkKinds: l.linkKinds });
                  select(null);
                  clearHighlight();
                  onFit();
                }}>{l.label}</button>
            ))}
            {!lens && <span className="badge" title="Set in the Layers panel">custom</span>}
          </div>
          <label className="row small" title="Remove elements outside the chosen view instead of fading them">
            <input type="checkbox" checked={net.hideRest} onChange={(e) => setNet({ hideRest: e.target.checked })} /> Hide the rest
          </label>
          {selection && (
            <label className="row small" title="Show only what the selection connects to">
              <input type="checkbox" checked={net.isolate} onChange={(e) => setNet({ isolate: e.target.checked })} /> Only the selection
            </label>
          )}
          <div className="seg" role="group" aria-label="Arrange">
            <button aria-pressed={net.arrange === "years"} className={net.arrange === "years" ? "on" : ""} onClick={() => setNet({ arrange: "years" })}
              title="Courses by year of study, left to right; program outcomes across the top">By year</button>
            <button aria-pressed={net.arrange === "clusters"} className={net.arrange === "clusters" ? "on" : ""} onClick={() => setNet({ arrange: "clusters" })}
              title="Let related courses and outcomes gather">Clusters</button>
          </div>
          <label className="row small" title="Spacing between elements">
            Spread
            <input type="range" min={0.6} max={2} step={0.1} value={net.spread} onChange={(e) => setNet({ spread: Number(e.target.value) })} aria-label="Spread" />
          </label>
          <button className="small" onClick={onFit} title="Fit what is shown into view">Fit</button>
        </>
      )}
    </div>
  );
}

/** The network itself, its status line and legend. Selection and layers are the Web's own (shared store). */
export function NetworkCanvas({ view, fitNonce }: { view: WebView; fitNonce: number }) {
  const { webFilters, selection, select, highlight, clearHighlight, net } = useUi();
  const vis = useMemo(() => networkVisibility(view, webFilters), [view, webFilters]);

  // A click on the canvas selects what is under the pointer (or clears, on the background) and replaces a highlight:
  // the click decides what is lit. The camera stays put for those; a selection or highlight made elsewhere (search,
  // the Inspector, a finding) is brought into view.
  const fromCanvas = useRef<Selection>(null);
  const [revealNonce, setRevealNonce] = useState(0);
  useEffect(() => {
    if (selection && selection !== fromCanvas.current) setRevealNonce((n) => n + 1);
  }, [selection]);
  useEffect(() => {
    if (highlight.mode && highlight.nodes.length) setRevealNonce((n) => n + 1);
  }, [highlight]);
  const pick = (hit: NetHit) => {
    const next: Selection = !hit
      ? null
      : hit.kind === "node"
        ? { kind: "node", id: hit.id }
        : { kind: "edge", id: hit.id, key: view.edges.find((e) => e.id === hit.id)?.key ?? null };
    fromCanvas.current = next;
    clearHighlight();
    select(next);
  };

  const focus = useMemo<Focus | null>(() => {
    if (highlight.mode && highlight.nodes.length) return { nodes: new Set(highlight.nodes), links: new Set(highlight.edges) };
    if (selection?.kind === "node" && view.nodes.some((n) => n.id === selection.id)) return focusOf(view, selection.id, vis);
    if (selection?.kind === "edge") {
      const e = view.edges.find((x) => x.id === selection.id);
      if (e) return { nodes: new Set([e.source, e.target]), links: new Set([e.id]) };
    }
    return null;
  }, [view, vis, selection, highlight]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const n of view.nodes) if (vis.node.get(n.id) === "bright") c[n.type] = (c[n.type] ?? 0) + 1;
    return c;
  }, [view, vis]);
  const status = `${TYPE_ORDER.filter((t) => counts[t]).map((t) => `${counts[t]} ${PLURAL[t][counts[t] === 1 ? 0 : 1]}`).join(" · ") || "Nothing matches the layers"} · ${vis.drawn} link${vis.drawn === 1 ? "" : "s"}`;
  const sel = selection?.kind === "node" ? view.nodes.find((n) => n.id === selection.id) : undefined;

  return (
    <div className="net-view" role="region" aria-label="Network of the alignment web">
      <NetworkGraph
        view={view}
        vis={vis}
        focus={focus}
        selectedId={selection?.kind === "node" ? selection.id : null}
        colorBy={webFilters.colorBy ?? "connection"}
        arrangement={net.arrange}
        spread={net.spread}
        showGaps={webFilters.showGaps}
        hideRest={net.hideRest}
        isolate={net.isolate && !!focus}
        diff={!!view.scenario}
        fitNonce={fitNonce}
        revealNonce={revealNonce}
        onPick={pick}
      />
      <div className="net-status small" aria-live="polite">
        <span className="net-status-text">{status}</span>
        {sel && focus && <span> · <strong>{sel.key}</strong> lights up {focus.nodes.size - 1} connected element{focus.nodes.size === 2 ? "" : "s"}</span>}
        <span className="muted"> · hover to preview, click to select, click empty space to clear, scroll to zoom, drag to pan</span>
      </div>
      <p className="sr-only">
        The network is drawn on a canvas. Use the search box to select an element by keyboard: the Inspector then lists
        its connections, and the Connections page shows the same links as tables.
      </p>
      <NetworkLegend />
    </div>
  );
}

function Glyph({ shape, color }: { shape: Shape; color: string }) {
  const s = 14;
  const c = s / 2;
  const d =
    shape === "diamond" ? `M${c} 1 L${s - 1} ${c} L${c} ${s - 1} L1 ${c} Z`
      : shape === "square" ? "M2.5 2.5 H11.5 V11.5 H2.5 Z"
      : shape === "box" ? "M1.5 3 H12.5 V11 H1.5 Z"
      : shape === "hexagon" ? `M${c} 1 L12.6 4 L12.6 10 L${c} 13 L1.4 10 L1.4 4 Z`
      : "";
  return (
    <svg width={s} height={s} aria-hidden>
      {shape === "circle" ? <circle cx={c} cy={c} r={5} fill={color} /> : <path d={d} fill={color} />}
    </svg>
  );
}

function NetworkLegend() {
  const [open, setOpen] = useState(false);
  const colorBy = useUi((s) => s.webFilters.colorBy ?? "connection");
  return (
    <div className="legend web-legend net-legend">
      <button className="link small" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide legend" : "Legend"}</button>
      {open && (
        <div className="small" style={{ marginTop: 4 }}>
          <div className="legend-levels">
            {TYPE_ORDER.map((t) => (
              <span key={t} className="row"><Glyph shape={NET_STYLE[t].shape} color={NET_STYLE[t].color} /> {NET_STYLE[t].label}</span>
            ))}
          </div>
          {colorBy === "connection" ? (
            <div className="legend-levels">
              <span>Link colour = connection level:</span>
              {CONNECTION_LEVELS.map((c) => (
                <span key={c} className="row">
                  <span className="conn-swatch" style={{ borderTopColor: CONNECTION_COLOR[c] }} aria-hidden /> {CONNECTION_TEXT[c]} <span aria-hidden>{CONNECTION_GLYPH[c]}</span>
                </span>
              ))}
            </div>
          ) : (
            <div>Link colour = link type (see Layers)</div>
          )}
          <div>Solid = documented · long dash = accepted interpretation · short dash = proposed by the AI (AI?) · thicker = stronger</div>
          <div>
            <span className="ring none" aria-hidden /> red ring: the AI found no connection · <span className="ring unmapped" aria-hidden /> dashed grey ring: not mapped yet
          </div>
          <div>Faded = outside the chosen view (context) · moving dots show the direction of highlighted links</div>
        </div>
      )}
    </div>
  );
}
