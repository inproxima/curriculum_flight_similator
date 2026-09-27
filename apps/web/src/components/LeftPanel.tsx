import { usePathways } from "../api/hooks";
import { LAYER_LABEL } from "../lib/graphStyle";
import { useUi } from "../store/ui";

const YEARS = [1, 2, 3, 4];
const CLASSES: [string, string][] = [["required", "Required"], ["pathway_required", "Pathway-required"], ["elective", "Elective"], ["unknown", "Unknown"]];

function toggle<T>(arr: T[], v: T): T[] {
  return arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v];
}

export function LeftPanel() {
  const { programId, layers, toggleLayer, filters, setFilters, highlight, clearHighlight, focus, setFocus, expanded, toggleExpanded } = useUi();
  const pathways = usePathways(programId);
  return (
    <aside className="panel" aria-label="Map filters">
      {highlight.mode && (
        <section className="info small">
          <div><strong>Highlighting:</strong> {highlight.label}</div>
          <button className="small" onClick={clearHighlight}>Clear highlight</button>
        </section>
      )}
      {focus && (
        <section className="info small">
          Showing neighborhood only. <button className="small" onClick={() => setFocus(null)}>Show whole program</button>
        </section>
      )}
      <section>
        <h4>Layers</h4>
        {Object.entries(LAYER_LABEL).map(([k, label]) => (
          <label key={k} className="row"><input type="checkbox" checked={layers.includes(k)} onChange={() => toggleLayer(k)} /> {label}</label>
        ))}
      </section>
      <section>
        <h4>Years</h4>
        <div className="row wrap">
          {YEARS.map((y) => (
            <label key={y} className="row"><input type="checkbox" checked={filters.years.includes(y)} onChange={() => setFilters({ years: toggle(filters.years, y) })} /> Y{y}</label>
          ))}
          <label className="row"><input type="checkbox" checked={filters.years.includes(0)} onChange={() => setFilters({ years: toggle(filters.years, 0) })} /> Unknown</label>
        </div>
        <p className="muted small">None checked = all years.</p>
      </section>
      <section>
        <h4>Pathway</h4>
        <select aria-label="Pathway" value={filters.pathway ?? ""} onChange={(e) => setFilters({ pathway: e.target.value || null })}>
          <option value="">All pathways</option>
          {pathways.data?.map((p) => <option key={p.id} value={p.code}>{p.name}</option>)}
        </select>
        <p className="muted small">Also sets the pathway assumption used for preparation analysis.</p>
      </section>
      <section>
        <h4>Course classification</h4>
        {CLASSES.map(([k, label]) => (
          <label key={k} className="row"><input type="checkbox" checked={filters.classifications.includes(k)} onChange={() => setFilters({ classifications: toggle(filters.classifications, k) })} /> {label}</label>
        ))}
      </section>
      <section>
        <h4>Relationship evidence</h4>
        <label className="row"><input type="checkbox" checked={!filters.basis.length || filters.basis.includes("explicit_statement")}
          onChange={() => setFilters({ basis: toggle(filters.basis.length ? filters.basis : ["explicit_statement", "interpretation", "assumption"], "explicit_statement") })} /> Documented</label>
        <label className="row"><input type="checkbox" checked={!filters.basis.length || filters.basis.includes("interpretation")}
          onChange={() => setFilters({ basis: toggle(filters.basis.length ? filters.basis : ["explicit_statement", "interpretation", "assumption"], "interpretation") })} /> Inferred</label>
      </section>
      <section>
        <h4>Review state</h4>
        {["accepted", "proposed", "rejected"].map((s) => (
          <label key={s} className="row"><input type="checkbox" checked={filters.reviewStates.includes(s)} onChange={() => setFilters({ reviewStates: toggle(filters.reviewStates, s) })} /> {s}</label>
        ))}
      </section>
      {expanded.length > 0 && (
        <section>
          <h4>Expanded courses</h4>
          <button className="small" onClick={() => expanded.forEach((id) => toggleExpanded(id))}>Collapse all ({expanded.length})</button>
        </section>
      )}
    </aside>
  );
}
