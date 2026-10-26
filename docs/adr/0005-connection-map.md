# ADR 0005: Connection map (AI-surmised links with a level, and "none found")

Status: accepted (2026-09-27). Scope: tentatively connecting every course outcome, assessment and topic to what it
should serve, and showing how direct each connection is.

## Context
Curriculum leads want to see, for the whole program:
- every course outcome tentatively connected to the program outcomes it serves;
- every assessment connected to the course outcomes it tests;
- every topic connected to the course outcomes it supports;
- horizontally, whether a topic or outcome builds on an earlier (prerequisite) course and whether a later course
  builds on it.

Each connection should show how direct it is: stated or directly connected, strongly implied, or only partly
connected / surmised. When the AI finds no connection at all, that must be visible too, and distinguishable from
"nobody has looked yet".

What existed:
- `ai.alignment` proposes links only where something is missing (gaps). It never examines elements that already
  have a link, and it records nothing when it finds nothing.
- Strength (ADR 0003) is `trust × intensity × support`: a quality score that mixes provenance with the stated
  contribution level. It does not answer "how directly does this outcome address that program outcome".

## Decisions
1. **Connection level, three steps, derived and never stored as a label** (`analysis.alignment.connection_level`):

   | Level | Glyph | Meaning | How it is set |
   |---|---|---|---|
   | `direct` | ●●● | Stated in a source, or the element explicitly addresses the target | any documented link (`explicit_statement`, accepted); an AI link asserted at ≥ 0.9 |
   | `strong` | ●●○ | Clearly implied | asserted 0.7–0.9; otherwise a computed strength label of *strong* |
   | `partial` | ●○○ | Plausible but indirect, or only part of the target (a surmise) | anything else |

   An AI link stores its level as an asserted strength (direct 0.95, strong 0.8, partial 0.5, `strength_source`
   `ai_estimate`), so strength (ADR 0003) and level stay consistent and a reviewer's edit moves both. No rule
   changed, so `ENGINE_VERSION` is unchanged.
2. **Map every element, not just the gaps** (`cfs.ai.mapping`, job `ai_map_alignment`, route `synthesize`). Batches:
   - **vertical**, one per course: its outcomes → program outcomes, its assessments and topics → its outcomes;
   - **forward**, one per course: its topics and outcomes → later courses' outcomes and topics (`prepares_for`).
     "Later" means a higher year, a later known term in the same year, or a course whose prerequisite names it;
   - **backward**, only for a course whose earlier courses are not all in the same run: its topics and outcomes →
     earlier courses' elements it builds on (prerequisites first), stored as `prepares_for` from the earlier
     element. A whole-program run needs no backward batches, so each earlier → later pair is asked once.

   Targets are whole courses, up to 400 elements, and never the batch's own (shared) elements.
3. **The model proposes; code verifies.** Every link is checked before it is written:
   - the target is in the batch;
   - the relation is the one this batch maps for that source type, and the pair is allowed by `TYPE_PAIRS`;
   - the level is known;
   - it has a verbatim quote from either element;
   - it is not a duplicate, never re-proposes a link a reviewer rejected, and documented links outrank it;
   - there are at most 4 per source.

   Links are written as `proposed` / `ai_inferred` / `interpretation`, drawn dashed with **AI?** until someone
   accepts them. Nothing becomes documented. Draft versions only; there are no inbox items, because links are
   reviewed on the Web, in the Inspector and on the Connections page.
4. **"None found" is a recorded verdict** (table `alignment_verdicts`, one row per version × element × kind):
   - `linked`: the element has an active link of that kind;
   - `none`: the model examined the element, returned no link, and gave a reason, which is shown with the ✗.

   Kinds: `contributes_to`, `assesses`, `supports`, `prepares_for`, and `prepared_by`. An element with no row is
   **not mapped**, shown as `?`.
   - **`prepared_by`** ("builds on nothing earlier") is set only once every earlier course that teaches something
     has been examined against the element: by those courses' forward batches (whose model answered every source),
     or by the element's own backward batch.
   - **Omitted elements:** if the model leaves elements out of its answer, one follow-up call asks for exactly
     those elements.
   - **Directions that don't apply** are not shown: nothing earlier for a first course, nothing later for a last
     course.
5. **Cost is shown before anything is sent.**
   - `GET …/ai-map/preview` estimates the calls and cost without a model call.
   - The UI confirms before `POST …/ai-map` runs.
   - The job's cost cap is max(the default cap, 1.5 × the estimate + $0.25), and never more than $25.
   - Identical prompts are served from cache, so re-mapping costs only the batches whose content changed.
   - Element texts (outcome statements, assessment titles, topic names) are sent to the `synthesize` route's
     provider under each source document's AI policy.
6. **Presentation:** colour + dots + words, never colour alone.
   - **Web:**
     - links are coloured by level (a toggle switches back to colour-by-type);
     - a Layers filter per level;
     - each outcome, assessment and topic carries its best level, a red **✗ none found** with the reason, or a
       grey **? not mapped**;
     - outcomes and topics also carry **← earlier** and **later →** flags;
     - "Map connections (AI)" runs the mapping for the selected course or for the whole program.
   - **Inspector:** a level chip on every link; a connection summary per element, with the AI's reasons; and in
     the course view, a table of each topic and outcome against what it builds on and what builds on it.
   - **Connections page** (`/connections`):
     - summary bars per direction;
     - heat maps (outcome × program outcome; per course, assessment × outcome and topic × outcome);
     - an across-courses matrix (element × course, ← / →);
     - clicking a cell shows the links, with accept / edit / reject for proposals.
   - **Course outline import:** "Then map this course's connections (AI)" is on by default after Apply
     (`map_after`).
   - **Progress and completion (job center):** a whole-program run takes about 20 minutes, so a run never depends on
     the page that started it.
     - A card at the bottom right of every page shows progress and the latest step ("Call 18/33: …"), with
       **Stop**. The tab title also shows progress.
     - When the run ends, the card turns into a notification with the result, the actual cost and **Open
       Connections**. It stays until dismissed (for a day, across reloads).
     - A system notification is sent if the tab is in the background and the user allowed it.
     - The state comes from the server (`GET /jobs?kind=…&mine=true`, with each job's `last_message`), so runs
       started elsewhere or before a reload are found too.
     - Map and Propose buttons are disabled while a run for the version is in progress.

## Consequences
- **Complete coverage, visibly.** The program-wide map either shows each element's best connection or says "none
  found, because …". A missing link no longer reads the same as one nobody looked for.
- **Proposals accumulate for review.** Re-running the mapping lists existing links as "already linked", so the
  model adds only what is new. Accepting keeps the level (the asserted strength). Rejecting prevents it from being
  proposed again.
- **Cost:**
  - About two calls per course for a whole program.
  - The first BHSc 2026–27 run took 33 calls, about 22 minutes and $5.75. It proposed 909 links (146 direct, 571
    strong, 192 partial) and recorded 238 "none found" verdicts.
  - The estimate now assumes 135 output tokens per element (it was 110, which under-estimated by about 17%).
  - The job result reports the calls made and the actual `cost_usd`.
  - Mapping one course takes at most three calls, plus a follow-up for any elements the model skips.
- **Pairs the prompt now spells out:** a course outcome prepares only for a later *outcome*, and a topic builds only
  on an earlier *topic*, matching `TYPE_PAIRS`. The first run dropped 42 outcome → later-topic links, because the
  prompt allowed them but the rules don't.
- **Limits:**
  - The level is the model's judgement, verified only for its quote and scope.
  - `prepared_by` for a topic several courses share uses the union of those courses' earlier courses.
  - A verdict is a snapshot: after editing an element, map it again.
