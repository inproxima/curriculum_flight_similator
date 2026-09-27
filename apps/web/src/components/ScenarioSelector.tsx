import { useEffect } from "react";
import { ApiError } from "../api/client";
import { useScenario, useScenarios } from "../api/scenarioHooks";
import { useUi } from "../store/ui";

export function ScenarioSelector() {
  const { versionId, scenarioId, setScenario } = useUi();
  const { data } = useScenarios(versionId);
  const current = useScenario(scenarioId);
  // A remembered scenario that no longer exists (deleted, or a database reset) falls back to the baseline. Ask the
  // server rather than the cached list: a scenario created a moment ago is not in the cached list yet.
  const missing = current.error instanceof ApiError && current.error.status === 404;
  useEffect(() => {
    if (scenarioId && missing) setScenario(null);
  }, [scenarioId, missing, setScenario]);
  return (
    <label className="row">
      <span className="sr-only">Baseline or scenario</span>
      <select aria-label="Baseline or scenario" value={scenarioId ?? ""} onChange={(e) => setScenario(e.target.value || null)}>
        <option value="">Baseline (no changes)</option>
        {data?.map((s) => <option key={s.id} value={s.id}>Scenario: {s.title} (rev {s.revision})</option>)}
      </select>
    </label>
  );
}
