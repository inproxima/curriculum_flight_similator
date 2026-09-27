import { useState } from "react";
import { ApiError } from "../api/client";
import { useApplyChange, useScenario, useScenarioMutations } from "../api/scenarioHooks";
import { useUi } from "../store/ui";

/** Apply a what-if change, creating a scenario over the selected version the first time. The baseline never changes. */
export function useScenarioEdit() {
  const { versionId, scenarioId, setScenario } = useUi();
  const scenario = useScenario(scenarioId);
  const sm = useScenarioMutations(scenarioId);
  const applyChange = useApplyChange();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const apply = async (change: Record<string, unknown>, title = "What-if from the alignment web") => {
    setError(null);
    setBusy(true);
    try {
      if (scenarioId && scenario.data) return await applyChange(scenarioId, scenario.data.revision, change);
      // new scenario: apply the first change *before* switching to it, so no view ever caches it empty
      const created = await sm.create.mutateAsync({ base_version_id: versionId!, title });
      const s = await applyChange(created.id, created.revision, change);
      setScenario(created.id);
      return s;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
      return null;
    } finally {
      setBusy(false);
    }
  };
  return { apply, error, busy, scenario: scenario.data ?? null };
}
