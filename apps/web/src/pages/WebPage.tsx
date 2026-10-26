import { useEffect, useMemo, useState } from "react";
import { useWeb, type WebView } from "../api/webHooks";
import { AssistantPanel } from "../components/AssistantPanel";
import { ChangeComposer } from "../components/ChangeComposer";
import { Inspector } from "../components/Inspector";
import { LayersPanel } from "../components/LayersPanel";
import { OutlineImportDialog } from "../components/outline/OutlineImportDialog";
import { ProposedQueue } from "../components/ProposedQueue";
import { ScorecardStrip } from "../components/ScorecardStrip";
import { SimulationPanel } from "../components/SimulationPanel";
import { useUi } from "../store/ui";
import { WebGraph } from "../web/WebGraph";
import { applyWebFilters } from "../web/webFilters";
import { layoutWeb } from "../web/webLayout";
import { NetworkCanvas, WebViewBar } from "../web/network/NetworkView";
import { CONNECTION_COLOR, CONNECTION_GLYPH, CONNECTION_HELP, CONNECTION_LEVELS, CONNECTION_TEXT } from "../web/webStyle";

/** The network needs every element at once (it focuses on the client); the columns fetch a focused slice. */
export function useWebForUi() {
  const { versionId, scenarioId, webFocus, webView } = useUi();
  return useWeb(versionId, webView === "network" ? { scenarioId, detail: "full" } : { scenarioId, focus: webFocus });
}

export function WebPage() {
  const { versionId, sim, webFilters, webFocus, setWebFocus, selection, select, highlight, clearHighlight, requestFit, assistantOpen, webView } = useUi();
  const web = useWebForUi();
  const view = web.data;
  const [queueOpen, setQueueOpen] = useState(false);
  const [fitNonce, setFitNonce] = useState(0);
  const network = webView === "network";

  const needsLayout = !network || sim.open;
  const layout = useMemo(() => (view && needsLayout ? layoutWeb(view.nodes, view.edges, view.columns) : null), [view, needsLayout]);
  const visible = useMemo(() => (view ? applyWebFilters(view, webFilters) : { nodes: new Set<string>(), edges: new Set<string>() }), [view, webFilters]);
  const emphasis = useMemo(() => {
    if (highlight.mode && highlight.nodes.length) return new Set(highlight.nodes);
    if (webFocus && view?.focus?.node_ids.length) return new Set(view.focus.node_ids);
    return null;
  }, [highlight, webFocus, view?.focus]);

  // selecting a program outcome or course anywhere (graph, inspector, search) focuses its neighbourhood
  const selType = selection?.kind === "node" ? view?.nodes.find((n) => n.id === selection.id)?.type : undefined;
  useEffect(() => {
    if (network) return; // the network lights up the neighbourhood itself
    if (selection?.kind === "node" && (selType === "program_outcome" || selType === "course")) setWebFocus(selection.id);
  }, [selection, selType, setWebFocus, network]);

  // fit the viewport to a new focus once its neighbourhood arrives
  const focusKey = view?.focus?.id ?? null;
  useEffect(() => {
    if (focusKey && view?.focus?.node_ids.length) requestFit(view.focus.node_ids);
  }, [focusKey]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Escape means nothing to a checkbox or radio button, so it still clears the selection from one
      if (e.key !== "Escape" || (e.target as HTMLElement)?.closest('input:not([type="checkbox"]):not([type="radio"]), textarea, select, [role=dialog]')) return;
      if (highlight.mode) clearHighlight();
      else if (webFocus && !network) setWebFocus(null);
      else select(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [highlight.mode, webFocus, clearHighlight, setWebFocus, select, network]);

  return (
    <div className={`workspace web ${assistantOpen ? "with-assistant" : ""}`}>
      <LayersPanel view={view} onReviewProposed={() => { setQueueOpen(true); select(null); }} />
      <main className="center web-center" aria-label="Alignment web">
        {view && <ScorecardStrip score={view.scorecard} baseline={view.scorecard_baseline} />}
        <WebViewBar view={view} onFit={() => setFitNonce((n) => n + 1)} />
        <div className="web-canvas">
          {web.error && <div className="map-notice error card">Could not load the alignment web: {String(web.error)}</div>}
          {view?.truncated && <div className="map-notice notice small">{String(view.truncated.message)}</div>}
          {view?.scenario && !sim.open && <ScenarioBanner view={view} />}
          {view && layout && sim.open && view.scenario && <SimulationPanel view={view} layout={layout} />}
          {view && network && !(sim.open && view.scenario) && <NetworkCanvas key={versionId ?? ""} view={view} fitNonce={fitNonce} />}
          {view && layout && !network && !(sim.open && view.scenario) && (
            <WebGraph
              key={`${versionId}`}
              view={view}
              layout={layout}
              visible={visible}
              emphasis={emphasis}
              diff={!!view.scenario}
              showGaps={webFilters.showGaps}
              selectedId={selection?.id ?? null}
              onNodeClick={(n) => select({ kind: "node", id: n.id })}
              onEdgeClick={(e) => select({ kind: "edge", id: e.id, key: e.key })}
              onPaneClick={() => select(null)}
            />
          )}
          {!versionId && <div className="page muted">Select a program and curriculum version.</div>}
          {view && !sim.open && !network && <WebLegend />}
        </div>
        {view && <ChangeComposer view={view} />}
      </main>
      <aside className="panel right" aria-label="Inspector">
        {view && (queueOpen && !selection ? <ProposedQueue view={view} onClose={() => setQueueOpen(false)} /> : <Inspector view={view} />)}
      </aside>
      {assistantOpen && <AssistantPanel />}
      {view && <OutlineImportDialog view={view} />}
    </div>
  );
}

function ScenarioBanner({ view }: { view: WebView }) {
  const n = view.nodes.filter((x) => x.status !== "unchanged").length;
  const e = view.edges.filter((x) => x.status !== "unchanged").length;
  return (
    <div className="map-notice info small" style={{ left: 10, right: "auto" }}>
      Scenario <strong>{view.scenario!.title}</strong> (rev {view.scenario!.revision}) · {n} element(s) and {e} link(s) differ from the
      baseline. Colour plus a glyph marks each change: + added, − removed, ▲ strengthened, ▼ weakened, ~ modified.
    </div>
  );
}

function WebLegend() {
  const [open, setOpen] = useState(false);
  const colorBy = useUi((s) => s.webFilters.colorBy ?? "connection");
  return (
    <div className="legend web-legend">
      <button className="link small" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Hide legend" : "Legend"}</button>
      {open && (
        <div className="small" style={{ marginTop: 4 }}>
          {colorBy === "connection" ? (
            <div className="legend-levels">
              <span>Colour = connection level:</span>
              {CONNECTION_LEVELS.map((c) => (
                <span key={c} className="row" title={CONNECTION_HELP[c]}>
                  <span className="conn-swatch" style={{ borderTopColor: CONNECTION_COLOR[c] }} aria-hidden />
                  {CONNECTION_TEXT[c]} <span aria-hidden>{CONNECTION_GLYPH[c]}</span>
                </span>
              ))}
            </div>
          ) : (
            <div>Colour = link type (see Layers)</div>
          )}
          <div>
            On an element: <span className="map-chip none">✗ none found</span> the AI examined it and found no connection ·{" "}
            <span className="map-chip unmapped">? not mapped</span> not examined yet
          </div>
          <div>Line width = strength (thick strong · medium moderate · thin weak)</div>
          <div>Solid = documented · long dash = accepted interpretation · short dash = proposed (AI?)</div>
          <div>Dotted red “⚠ no link” = prerequisite with no outcome-level bridge</div>
          <div>PLO pips I · R · A = introduced / reinforced / assessed somewhere in the program</div>
          <div>✓A / ✗A on an outcome = assessed / not assessed · T n = topics supporting it</div>
        </div>
      )}
    </div>
  );
}
