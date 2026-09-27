import { useScenarios } from "../api/scenarioHooks";
import { useUi } from "../store/ui";

export function ScenarioSelector() {
  const { versionId, scenarioId, setScenario } = useUi();
  const { data } = useScenarios(versionId);
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
