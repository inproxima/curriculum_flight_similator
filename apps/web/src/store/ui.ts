import { create } from "zustand";
import { persist } from "zustand/middleware";
import { DEFAULT_WEB_FILTERS, type WebFilters } from "../web/webFilters";

export type SimMode = "diff" | "ripple" | "split" | "list";
export interface SimState { open: boolean; runId: string | null; mode: SimMode; step: number; playing: boolean }

export type Selection = { kind: "node"; id: string } | { kind: "edge"; id: string; key: string | null } | null;
export type HighlightMode = "prior" | "downstream" | "finding" | null;

export interface Filters {
  years: number[]; // empty = all
  classifications: string[]; // empty = all
  reviewStates: string[]; // shown review states
  basis: string[]; // shown evidence bases
  pathway: string | null;
}

interface UiState {
  programId: string | null;
  versionId: string | null;
  scenarioId: string | null;
  compareBaseline: boolean;
  layers: string[];
  expanded: string[];
  focus: string | null;
  selection: Selection;
  multi: string[];
  highlight: { mode: HighlightMode; nodes: string[]; edges: string[]; label?: string };
  filters: Filters;
  sourceSpan: string | null;
  assistantOpen: boolean;
  webFilters: WebFilters;
  webFocus: string | null;
  sim: SimState;
  fitRequest: { ids: string[]; nonce: number } | null;
  /** Course outline import dialog: which course, and which import (null = start a new one). Not persisted. */
  outlineDialog: { courseId: string; importId: string | null } | null;
  /** Finished background jobs whose notification was dismissed (persisted, newest 50). */
  dismissedJobs: string[];
  dismissJob: (id: string) => void;
  /** The Web as a force-directed network (default) or as year/term columns. */
  webView: "network" | "columns";
  setWebView: (v: "network" | "columns") => void;
  net: NetSettings;
  setNet: (n: Partial<NetSettings>) => void;
  setProgram: (id: string | null) => void;
  setVersion: (id: string | null) => void;
  setScenario: (id: string | null) => void;
  setCompareBaseline: (v: boolean) => void;
  toggleLayer: (layer: string) => void;
  toggleExpanded: (id: string) => void;
  setFocus: (id: string | null) => void;
  select: (s: Selection, additive?: boolean) => void;
  setHighlight: (h: UiState["highlight"]) => void;
  clearHighlight: () => void;
  setFilters: (f: Partial<Filters>) => void;
  openSource: (spanId: string | null) => void;
  setAssistantOpen: (v: boolean) => void;
  setWebFilters: (f: Partial<WebFilters>) => void;
  resetWebFilters: () => void;
  setWebFocus: (id: string | null) => void;
  setSim: (s: Partial<SimState>) => void;
  requestFit: (ids: string[]) => void;
  setOutlineDialog: (d: UiState["outlineDialog"]) => void;
}

export interface NetSettings {
  arrange: "years" | "clusters";
  spread: number;
  hideRest: boolean;
  isolate: boolean;
}
const DEFAULT_NET: NetSettings = { arrange: "years", spread: 1, hideRest: false, isolate: false };

const closedSim: SimState = { open: false, runId: null, mode: "diff", step: 0, playing: false };

const emptyHighlight = { mode: null, nodes: [], edges: [] } as UiState["highlight"];

export const useUi = create<UiState>()(
  persist(
    (set) => ({
      programId: null,
      versionId: null,
      scenarioId: null,
      compareBaseline: true,
      layers: ["prerequisites", "preparation"],
      expanded: [],
      focus: null,
      selection: null,
      multi: [],
      highlight: emptyHighlight,
      filters: { years: [], classifications: [], reviewStates: ["accepted", "proposed"], basis: [], pathway: null },
      sourceSpan: null,
      assistantOpen: false,
      webFilters: DEFAULT_WEB_FILTERS,
      webFocus: null,
      sim: closedSim,
      fitRequest: null,
      outlineDialog: null,
      dismissedJobs: [],
      dismissJob: (id) => set((s) => ({ dismissedJobs: [id, ...s.dismissedJobs.filter((x) => x !== id)].slice(0, 50) })),
      webView: "network",
      // the columns' focused slice means nothing to the network, and its banner would linger there
      setWebView: (webView) => set(webView === "network" ? { webView, webFocus: null } : { webView }),
      net: DEFAULT_NET,
      setNet: (n) => set((s) => ({ net: { ...s.net, ...n } })),
      setProgram: (programId) =>
        set({ programId, versionId: null, scenarioId: null, selection: null, highlight: emptyHighlight, expanded: [], focus: null, webFocus: null, sim: closedSim, outlineDialog: null }),
      setVersion: (versionId) =>
        set({ versionId, scenarioId: null, selection: null, highlight: emptyHighlight, expanded: [], focus: null, webFocus: null, sim: closedSim, outlineDialog: null }),
      setScenario: (scenarioId) => set({ scenarioId, highlight: emptyHighlight, sim: closedSim }),
      setCompareBaseline: (compareBaseline) => set({ compareBaseline }),
      toggleLayer: (layer) =>
        set((s) => ({ layers: s.layers.includes(layer) ? s.layers.filter((l) => l !== layer) : [...s.layers, layer] })),
      toggleExpanded: (id) =>
        set((s) => ({ expanded: s.expanded.includes(id) ? s.expanded.filter((x) => x !== id) : [...s.expanded, id] })),
      setFocus: (focus) => set({ focus }),
      select: (selection, additive = false) =>
        set((s) => {
          if (additive && selection?.kind === "node") {
            const multi = s.multi.includes(selection.id) ? s.multi.filter((x) => x !== selection.id) : [...s.multi, selection.id];
            return { selection, multi };
          }
          return { selection, multi: selection?.kind === "node" ? [selection.id] : [] };
        }),
      setHighlight: (highlight) => set({ highlight }),
      clearHighlight: () => set({ highlight: emptyHighlight }),
      setFilters: (f) => set((s) => ({ filters: { ...s.filters, ...f } })),
      openSource: (sourceSpan) => set({ sourceSpan }),
      setAssistantOpen: (assistantOpen) => set({ assistantOpen }),
      setWebFilters: (f) => set((s) => ({ webFilters: { ...s.webFilters, ...f } })),
      resetWebFilters: () => set({ webFilters: DEFAULT_WEB_FILTERS }),
      setWebFocus: (webFocus) => set({ webFocus }),
      setSim: (sim) => set((s) => ({ sim: { ...s.sim, ...sim } })),
      requestFit: (ids) => set((s) => ({ fitRequest: { ids, nonce: (s.fitRequest?.nonce ?? 0) + 1 } })),
      setOutlineDialog: (outlineDialog) => set({ outlineDialog }),
    }),
    {
      name: "cfs-ui",
      partialize: (s) => ({
        programId: s.programId,
        versionId: s.versionId,
        scenarioId: s.scenarioId,
        layers: s.layers,
        filters: s.filters,
        compareBaseline: s.compareBaseline,
        webFilters: s.webFilters,
        dismissedJobs: s.dismissedJobs,
        webView: s.webView,
        net: s.net,
      }),
      merge: (persisted, current) => {
        const p = (persisted ?? {}) as Partial<UiState>;
        const saved = p.webFilters;
        // layers saved before the connection map existed: switch on the topic-preparation links it maps
        const upgraded = saved && !saved.connections && !saved.linkKinds?.includes("topic_prep")
          ? { ...saved, linkKinds: [...saved.linkKinds, "topic_prep" as const] }
          : saved;
        return { ...current, ...p, webFilters: { ...DEFAULT_WEB_FILTERS, ...(upgraded ?? {}) }, net: { ...DEFAULT_NET, ...(p.net ?? {}) } };
      },
    },
  ),
);
