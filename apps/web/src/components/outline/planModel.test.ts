import { applyLabel, blockedReason, changeCount, initialSelection, sectionCounts, selectLinks, toggle, type ModelPlan } from "./planModel";

const item = (id: string, over: Partial<ModelPlan["items"][number]> = {}): ModelPlan["items"][number] => ({
  id,
  section: "outcomes",
  action: "add",
  title: id,
  noop: false,
  selectable: true,
  default_selected: true,
  reason: "",
  depends_on: [],
  excludes: [],
  fills: [],
  link: null,
  ...over,
});

const link = (id: string, strength: number, over: Partial<ModelPlan["items"][number]> = {}) =>
  item(id, { section: "links", action: "link", link: { basis: "inferred", model_strength: strength, strength, rel_type: "assesses" }, ...over });

const plan: ModelPlan = {
  items: [
    item("o:n1"), // new outcome
    item("a:n2", { section: "assessments" }), // new assessment
    item("o:e2", { action: "retire", excludes: ["l3"] }),
    item("o:e1", { action: "keep", noop: true, selectable: false, default_selected: false, reason: "Already cites this outline." }),
    item("a:e3", { section: "assessments", action: "replace", selectable: false, default_selected: false, blocked_reason: "Newer source." }),
    link("l1", 0.9, { depends_on: ["a:n2", "o:n1"], link: { basis: "stated", model_strength: 0.9, strength: null, rel_type: "assesses" } }),
    link("l2", 0.7, { depends_on: ["o:n1"] }),
    link("l3", 0.65, { default_selected: false, excludes: ["o:e2"] }),
    link("l4", 0.4, { default_selected: false }),
  ],
};

describe("outline plan selection", () => {
  it("starts from the defaults, never selecting blocked or unchanged items", () => {
    const s = initialSelection(plan);
    expect([...s].sort()).toEqual(["a:n2", "l1", "l2", "o:e2", "o:n1"]);
    expect(changeCount(plan, s)).toBe(5);
  });

  it("deselecting a new element deselects the links that need it", () => {
    const { sel, also } = toggle(plan, initialSelection(plan), "o:n1", false);
    expect(sel.has("l1")).toBe(false);
    expect(sel.has("l2")).toBe(false);
    expect(also.sort()).toEqual(["l1", "l2"]);
  });

  it("selecting a link selects the elements it needs", () => {
    const { sel, also } = toggle(plan, new Set(), "l1", true);
    expect([...sel].sort()).toEqual(["a:n2", "l1", "o:n1"]);
    expect(also.sort()).toEqual(["a:n2", "o:n1"]);
  });

  it("exclusions work both ways", () => {
    let s = toggle(plan, initialSelection(plan), "l3", true).sel;
    expect(s.has("l3")).toBe(true);
    expect(s.has("o:e2")).toBe(false); // keeping the element the link needs
    s = toggle(plan, s, "o:e2", true).sel;
    expect(s.has("o:e2")).toBe(true);
    expect(s.has("l3")).toBe(false);
  });

  it("never selects blocked or unchanged items", () => {
    expect(toggle(plan, new Set(), "a:e3", true).sel.size).toBe(0);
    expect(toggle(plan, new Set(), "o:e1", true).sel.size).toBe(0);
    expect(blockedReason(plan.items[4])).toBe("Newer source.");
    expect(blockedReason(plan.items[3])).toBe("Already cites this outline.");
    expect(blockedReason(plan.items[0])).toBeNull();
  });

  it("group actions respect a strength threshold and never override a retirement", () => {
    const r = selectLinks(plan, new Set(), ["l2", "l3", "l4"], true, 0.6);
    expect([...r.sel].sort()).toEqual(["l2", "l3", "o:n1"]);
    expect(r.skipped).toBe(0);
    expect(selectLinks(plan, r.sel, ["l2", "l3", "l4"], false).sel.has("l2")).toBe(false);
    // with the retirement of o:e2 selected, l3 (which needs o:e2) is skipped rather than silently undoing it
    const withRetire = selectLinks(plan, initialSelection(plan), ["l2", "l3", "l4"], true, 0.6);
    expect(withRetire.sel.has("o:e2")).toBe(true);
    expect(withRetire.sel.has("l3")).toBe(false);
    expect(withRetire.skipped).toBe(1);
  });

  it("refuses a link whose element can't be added", () => {
    const blockedPlan: ModelPlan = {
      items: [item("o:n9", { selectable: false, default_selected: false, blocked_reason: "Older outline." }), link("l9", 0.9, { depends_on: ["o:n9"] })],
    };
    const r = toggle(blockedPlan, new Set(), "l9", true);
    expect(r.sel.size).toBe(0);
  });

  it("counts sections and labels the apply button", () => {
    const c = sectionCounts(plan, initialSelection(plan));
    expect(c.outcomes).toEqual({ total: 3, selected: 2, unchanged: 1 });
    expect(c["links:stated"]).toEqual({ total: 1, selected: 1, unchanged: 0 });
    expect(c["links:inferred"].selected).toBe(1);
    expect(applyLabel(1)).toBe("Apply 1 change");
    expect(applyLabel(5, { closes: ["a", "b"], missing_before: 9 })).toBe("Apply 5 changes · closes 2 of 9 missing pieces");
  });
});
