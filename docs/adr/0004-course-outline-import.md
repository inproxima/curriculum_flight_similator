# ADR 0004: Course outline import, edition precedence, and apply/revert

Status: accepted (2026-09-27). Scope: filling a course's missing pieces from its course outline.

## Context
Curriculum leads have a course's outline (for example CHEM 351, Fall 2026). They want to use it to fill in what the
program is missing for that course:
- **content:** outcomes, assessments, topics, description, credits;
- **links:** assessment → outcome, topic → outcome, outcome → program outcome.

Several things stood in the way:
- Upload, parsing and AI extraction already existed, but only on the Documents page, detached from any course.
- The inbox received every candidate one by one, with no idea what was actually missing.
- AI candidates were lost if the document wasn't assigned to a version yet.
- Nothing replaced older content. Worse, the resolver attached a newer outline's *different* wording as supporting
  evidence of the older outcome, because keys are positional (`CO1`, `CO2`, … in document order).
- Outlines rarely state the links that are missing, so those have to be interpreted, and labelled as such.

The rule "upload order never decides" (ADR 0001 §8, `ingest/resolve.py`) had no notion of a *newer* source.

## Decisions
1. **One course-scoped workflow** (`cfs.outlines`, table `outline_imports`, job `outline_import`).
   - The user uploads, pastes, or picks the outline from the course inspector.
   - The job then:
     1. reads it (the idempotent ingest stages, under a per-document advisory lock);
     2. extracts it (the existing `run_extraction`, cached by file hash, then filtered to the course);
     3. compares it with the draft;
     4. asks the reconcile agent (route `synthesize`);
     5. verifies the answer;
     6. builds a plan;
     7. previews the result.
   - Nothing is written to the curriculum until a person applies a selection of the plan. Every applied import can be
     reverted.
2. **The agent proposes; code decides.** The agent returns matches (same / reworded / new), skips, links (stated or
   inferred, with a quote and a strength) and whether each section is complete. Code decides everything else:
   - **Matching:** exact normalized matches always win. A "reworded" match needs similarity ≥ 0.5 or token overlap ≥
     0.3, and each existing element can be matched once.
   - **Links:** only this course's elements and the program outcomes, respecting `TYPE_PAIRS`.
     - No duplicate or previously rejected link.
     - The quote must be found in the outline or in either element's text.
     - A link counts as "stated" only if its outline quote names both ends; otherwise it is downgraded to an
       interpretation.
     - Caps per element.
   - **Retirement and defaults:** which elements are retire candidates, and every default selection.
3. **Edition precedence replaces "upload order never decides" for course-outline content.** The deciding question is
   now which edition is newer. A person confirms, and upload or processing order still never decides.
   - **The outline's academic year** comes, in order, from what the user entered, the document's recorded year, the
     year the extraction read from a verified quote, or a "Fall 2026" phrase on page 1.
   - **An element's edition** is the newest dated document behind its field evidence or its containment link.
   - **Defaults (add · replace · retire):**

     | Situation | Add | Replace | Retire |
     |---|---|---|---|
     | Outline older than the version | only into an empty section | blocked | blocked |
     | Outline later than the version, or unknown | pre-selected | selectable | selectable |
     | Same year as the version; element older or undated | pre-selected | pre-selected | pre-selected if the section is complete and the element wasn't entered manually |
     | Same edition as the element | pre-selected | pre-selected only if the element already cites this file | selectable |
     | Element newer than the outline | selectable | blocked | blocked |

   - **Absent sections:** a section the outline doesn't cover is never touched.
   - **Protected wording:** an element whose wording still appears in the outline can never be retired.
4. **Replace keeps identity; retire is a draft-only removal.**
   - **Replace** creates a new revision of the *same* entity, so its key and links persist.
   - **Retire** removes the element's membership, and every link touching it, from this draft only. Older and
     published versions keep it. A topic other courses cover is only detached from this course.
   - `superseded` stays unused, because not every reader filters on it.
   - New keys are unique across the organization (`CHEM351-CO7`). An existing entity whose latest text is identical
     is reused, so one element keeps one identity across versions.
5. **Copy-on-write evidence.** Evidence is never attached to a revision that another version pins, including drafts
   cloned from published versions. Confirming an element with the outline creates a new revision for this draft
   carrying that citation. As a result, applying and reverting leave every other version's content hash unchanged.
6. **Links.**
   - **Stated links** are documented: `explicit_statement`, accepted, no asserted strength, and a level only if the
     quote states one.
   - **Inferred links** the person keeps are accepted interpretations: trust 0.85, `strength_source` `ai_estimate`,
     or `reviewer` if edited. They are drawn long-dashed and never shown as documented.
   - Unticked proposals are not written anywhere.
7. **Concurrency and freshness.**
   - Apply, revert, review decisions and link decisions lock the version row (`assert_draft(lock=True)`).
   - A plan carries `etag = sha256(state_hash:plan_hash)`. `state_hash` covers the course, its members, the program
     outcomes, every link touching the course or its members, and its placements and rules.
   - Apply requires `If-Match` and returns:
     - 428 when the header is missing;
     - 409 `plan_stale` when the course changed since planning;
     - 409 `plan_changed` when the plan was re-run;
     - 422 when the selection breaks `depends_on` or `excludes`.
8. **Undo log and revert.**
   - Apply records every membership, placement, source, review-item and document-year change, with its before and
     after values.
   - Revert refuses (409 `revert_conflict`) if anything recorded has changed since. Otherwise it restores every
     "before" value.
   - Tests assert that after apply then revert, the content hash is identical to before.
9. **Inbox and sources.**
   - Open inbox items that the same document raised for this course are closed according to the plan's decisions,
     with a written reason, and reopened on revert. A course field left unselected stays in the inbox.
   - The document is assigned to the version only at apply time. It is authoritative when the years match and
     supporting otherwise. An existing assignment is never upgraded, and no resolve job is queued.
10. **Providers.** The outline text and this course's element texts are sent to the `extract` and `synthesize`
    routes. The gateway applies the AI policy of every document whose text is included, the spend caps and the
    audit. Identical inputs are served from cache.

Changes shipped with this ADR:
- The resolver no longer treats differing outcome wording under the same positional key as supporting evidence. It
  raises a wording conflict instead, keeping both sides.
- Choosing "proposed" on an outcome-statement or assessment-field conflict now revises the same entity.
- A failed ingest marks the document `failed`.
- The evidence viewer shows the text of non-PDF sources. Previously PDF.js threw on them and blanked the app.

## Consequences
- **What an outline can't fill is shown, not guessed.** Year and term of study come from the program, prerequisites
  from the calendar through the inbox, and bridges between courses from the existing alignment proposer. The plan
  lists each of these under "Not filled by this outline".
- **Cost:** a repeated import is served from cache, and a new plan costs one reconcile call.
- **Out of scope:**
  - requisites in outlines;
  - horizontal (`prepares_for`) links (since covered by the connection map, [ADR 0005](0005-connection-map.md),
    which an import can run right after Apply);
  - editing outcome wording inside the plan (wording stays verbatim; use a scenario);
  - documents that cover several courses;
  - redacting (rather than refusing) texts whose documents forbid a provider.
- **Known issue:** `resolve._attach` / `_attach_rel` still attach matching evidence to shared revisions. That needs
  the same copy-on-write fix separately.
