import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useMemo, useRef } from "react";
import { getJson } from "../api/client";
import { useGraph } from "../api/hooks";
import type { GraphView } from "../api/types";
import { AssistantPanel } from "../components/AssistantPanel";
import { DetailsPanel } from "../components/DetailsPanel";
import { LeftPanel } from "../components/LeftPanel";
import { CurriculumMap } from "../map/CurriculumMap";
import { useUi } from "../store/ui";

type Pos = Record<string, { x: number; y: number }>;

export function useGraphForUi() {
  const { versionId, layers, expanded, focus, scenarioId, compareBaseline } = useUi();
  return useGraph(versionId, { layers, expand: expanded, focus, scenarioId, compare: compareBaseline });
}

export function evidenceRatio(view: GraphView | undefined): number | null {
  if (!view) return null;
  let have = 0, total = 0;
  for (const n of view.nodes) if (n.type === "course") { have += n.evidence.with_evidence.length; total += n.evidence.fields.length; }
  return total ? have / total : null;
}

export function MapPage() {
  const { versionId, scenarioId, assistantOpen } = useUi();
  const graph = useGraphForUi();
  const qc = useQueryClient();

  const viewName = scenarioId ? `scenario:${scenarioId}` : "default";
  const views = useQuery({
    queryKey: ["views", versionId],
    enabled: !!versionId,
    queryFn: () => getJson<{ name: string; positions: Pos }[]>(`/api/v1/versions/${versionId}/views`),
  });
  const saved = useMemo(() => views.data?.find((v) => v.name === viewName)?.positions ?? {}, [views.data, viewName]);
  const timer = useRef<number | undefined>(undefined);
  const onPositionsChange = useCallback((positions: Pos) => {
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(async () => {
      await fetch(`/api/v1/versions/${versionId}/views`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: viewName, scenario_id: scenarioId, positions }),
      });
      qc.invalidateQueries({ queryKey: ["views", versionId] });
    }, 600);
  }, [versionId, viewName, scenarioId, qc]);

  return (
    <div className={`workspace ${assistantOpen ? "with-assistant" : ""}`}>
      <LeftPanel />
      <main className="center" aria-label="Curriculum map">
        {graph.error && <div className="map-notice error card">Could not load graph: {String(graph.error)}</div>}
        {graph.data?.truncated && <div className="map-notice notice">{graph.data.truncated.message} ({graph.data.truncated.shown} of {graph.data.truncated.total} shown)</div>}
        {graph.data?.scenario && (
          <div className="map-notice info small">
            Scenario <strong>{graph.data.scenario.title}</strong> (rev {graph.data.scenario.revision}) over {graph.data.curriculum_version.label}. Baseline is unchanged.
          </div>
        )}
        {graph.data && <CurriculumMap key={`${versionId}:${scenarioId ?? "base"}`} view={graph.data} savedPositions={saved} onPositionsChange={onPositionsChange} />}
        {!versionId && <div className="page muted">Select a program and curriculum version.</div>}
      </main>
      <aside className="panel right" aria-label="Details">
        <DetailsPanel />
      </aside>
      {assistantOpen && <AssistantPanel />}
    </div>
  );
}
