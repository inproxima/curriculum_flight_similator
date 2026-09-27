# ADR 0003: Alignment Web, link strength, and the simulated ripple

Status: accepted (2026-09-27). Scope: the Alignment Web redesign (v2).

## Context
Curriculum leaders need to see how everything connects:
- **Vertically:** program outcome ↔ course outcome ↔ assessment/assignment ↔ topic.
- **Horizontally:** outcomes and assessments in one course → outcomes in the next.

They also need to see how strong each connection is, and what a small change does to everything else (the
"butterfly effect"). Most of these links are not written in any document, so they must be proposed, reviewed,
and rated without ever being passed off as documented facts.

## Decisions
1. **Strength is stored as an asserted value; the label is always computed.** `relationship_revisions` gains
   three columns (migration `0003`):
   - `strength` (0–1, CHECK constrained);
   - `strength_source` (`ai_estimate`, `reviewer`, `fixture`, `scenario`);
   - `model_run_id` (for AI audit).

   Strong, moderate and weak are derived in `cfs.analysis.alignment` and never written, so a label cannot
   contradict the evidence it rests on.
2. **No new relationship types.** Enum values are CHECK-constrained, and the existing types already express
   every link:

   | Link | Type |
   |---|---|
   | course outcome → program outcome | `contributes_to` |
   | assessment → course outcome | `assesses` |
   | topic → course outcome | `supports` (widened from activity-only) |
   | earlier outcome or assessment → later outcome | `prepares_for` |

   `TYPE_PAIRS` in `cfs.analysis.alignment` lists the allowed (source type, target type) pairs. Scenario
   operations, AI proposals and inline decisions all validate against it.
3. **One transparent formula:** `effective = trust × intensity × support`.
   - **trust:** documented (explicit and accepted) 1.00; accepted interpretation 0.85; manual or scenario 0.80;
     proposed 0.60.
   - **intensity:** the asserted strength if there is one. Otherwise, for a contribution, its level
     (assess 1.0, reinforce 0.8, introduce 0.6, unstated 0.7); for an assessment link, the assessment weight
     (0.55 + 0.45·min(w, 40)/40, or 0.7 if unknown); for anything else, 0.7.
   - **support** (contributions from a course outcome only): 0.7, plus 0.2 if the outcome is assessed, plus 0.1
     if a topic supports it. Everything else uses 1.0.
   - **Labels:** strong ≥ 0.70, moderate ≥ 0.45, weak > 0. **Missing** means an expected link is absent (a gap).

   Documented links outrank AI proposals by construction: a proposal's trust of 0.6 caps it below a documented
   link of the same asserted strength.
4. **Aggregates:**
   - **Chain:** as strong as its weakest link (minimum).
   - **Course pair:** as strong as its best outcome-level bridge (maximum). A prerequisite with no bridge is an
     "unbridged prerequisite".
   - **Program-outcome thread:** the mean of the best strength at each stage (introduce, reinforce, assess),
     where a missing stage counts 0. Its status is one of `complete`, `out_of_order`, `never_assessed`,
     `incomplete`, `uncovered` or `levels_unstated`.
   - **Strengthened / weakened:** a change counts when |Δ| ≥ 0.05 or the label changes.
5. **AI proposes; people decide.**
   - `ai_propose_alignment` (route `synthesize`) batches per course (vertical) and per missing or weak course
     pair (horizontal).
   - Every proposal is verified: scope, `TYPE_PAIRS`, a verbatim quote from one of the two elements, no duplicate
     of an existing link, and no link a reviewer already rejected.
   - Survivors are written to the **draft** version as `proposed / ai_inferred / interpretation`, carrying the
     model's strength as an `ai_estimate`. They appear immediately as dashed links, with an inbox item.
   - Inline decisions (`decide_relationship`) can accept, edit or reject. Accepting keeps the link labelled as an
     interpretation. Documented links cannot be rejected inline.
6. **Scenario edits are the planned design.** Inside a projection, the planner's own changes (marked
   `scenario`) count toward coverage. They are always shown as scenario changes, never as source statements.
7. **Ripple.** The engine (`engine-1.4.0`) walks the union of baseline and scenario links from the edits
   themselves. It follows typed links outward:
   - assessment or topic → outcome;
   - outcome → program outcome and later outcomes;
   - course → what it contains and the courses that depend on it.

   Each reached element gets a hop count, a path, and a status (removed > added > broken > weakened >
   strengthened > modified > affected). Links whose strength changed only as a consequence are never seeds. The
   summary holds `ripple` (steps by hop and ranked impacts) and a baseline, scenario and delta `scorecard`. New
   findings: `plo_thread_broken` / `plo_thread_improved`, `horizontal_chain_broken` / `weakened`,
   `outcome_no_longer_assessed`, `orphan_created`, `alignment_weakened` / `strengthened`, and `plo_removed`.
8. **Deterministic layout on the client.** The Web places elements by course column (year and term order) and
   type band. Program outcomes and shared topics sit at the barycentre of their courses. Positions never
   depend on filters, so diff, ripple, and before/after views keep every element in the same place.

## Consequences
- Adding the strength columns changes snapshot content hashes, and the engine version bump invalidates cached
  analysis runs. Both are intended.
- AI proposals raise *inferred* coverage only. Documented coverage and the scorecard keep them separate.
- The published BHSc version must be copied to a draft before AI alignment can run.
