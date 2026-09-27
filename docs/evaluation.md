# Evaluation: real BHSc sources (2026-09-26)

Gold standard: [fixtures/eval/bhsc_gold.yaml](../fixtures/eval/bhsc_gold.yaml), built by reading the three documents. Re-run with `cd services/api && uv run python -m cfs.evals.bhsc --retrieval`. Extraction is scored on the most recent `gpt-6-sol` output **after** deterministic verification (no new model calls). Retrieval runs hybrid search (full text + `text-embedding-3-small`) scoped to each curriculum version's assigned sources.

**Limits:** this is a small, hand-built set. The retrieval queries were written by someone who had read the documents, so this is a sanity check, not a benchmark. Inferred mappings (course → program outcome, preparation) are interpretations with no ground truth; they are left for faculty review in the inbox rather than scored.

## Extraction (per document, after verification)

| Document | Task | Precision | Recall | False positives | Missed |
|---|---|---:|---:|---|---|
| outline_2025_26 | Courses (code) | 100% | 100% | — | — |
| outline_2025_26 | Course + year | 100% | 100% | — | — |
| outline_2025_26 | Full-year term | n/a | n/a | — | — |
| outline_2025_26 | Credits | 100% | 100% | — | — |
| outline_2025_26 | Option rule (name+year) | 89% | 100% | Bcem 393 Or 341|2 | — |
| outline_2025_26 | Option slot counts | | 8/8 correct | | |
| outline_2025_26 | Program outcome labels | n/a | n/a | — | — |
| outline_2025_26 | Verifier drops | | | 47 model items rejected | |
| webpage_2026 | Courses (code) | 94% | 100% | MDSC 402 | — |
| webpage_2026 | Course + year | 100% | 100% | — | — |
| webpage_2026 | Full-year term | 100% | 100% | — | — |
| webpage_2026 | Credits | n/a | n/a | — | — |
| webpage_2026 | Option rule (name+year) | 100% | 100% | — | — |
| webpage_2026 | Option slot counts | | 9/9 correct | | |
| webpage_2026 | Program outcome labels | n/a | n/a | — | — |
| webpage_2026 | Verifier drops | | | 1 model items rejected | |
| review_2019 | Courses (code) | n/a | n/a | — | — |
| review_2019 | Course + year | n/a | n/a | — | — |
| review_2019 | Full-year term | n/a | n/a | — | — |
| review_2019 | Credits | n/a | n/a | — | — |
| review_2019 | Option rule (name+year) | n/a | n/a | — | — |
| review_2019 | Option slot counts | n/a | n/a | | |
| review_2019 | Program outcome labels | 100% | 100% | — | — |
| review_2019 | Verifier drops | | | 0 model items rejected | |

## Retrieval

| Query | Version | Mode | Hit@3 | Hit@5 | First relevant rank |
|---|---|---|---:|---:|---:|
| honours thesis defended in front of a panel oral examination | 5b3490f1 | hybrid | ✓ | ✓ | 1 |
| program-level learning outcomes headings | 5b3490f1 | hybrid | ✓ | ✓ | 1 |
| acceptable Faculty of Arts courses for the Biomedical Option | 5b3490f1 | hybrid | ✓ | ✓ | 1 |
| statistics literacy for analysing health-related data | 5b3490f1 | hybrid | ✓ | ✓ | 1 |
| precision medicine course action plan | 5b3490f1 | hybrid | ✓ | ✓ | 1 |
| cannot be taken in the same year as MDSC 508 | 5b3490f1 | hybrid | ✓ | ✓ | 1 |
| honours thesis defended in front of a panel oral examination | d122511e | hybrid | ✓ | ✓ | 1 |
| program-level learning outcomes headings | d122511e | hybrid | ✓ | ✓ | 1 |
| acceptable Faculty of Arts courses for the Biomedical Option | d122511e | hybrid | ✓ | ✓ | 1 |
| statistics literacy for analysing health-related data | d122511e | hybrid | ✓ | ✓ | 1 |
| precision medicine course action plan | d122511e | hybrid | ✓ | ✓ | 1 |
| cannot be taken in the same year as MDSC 508 | d122511e | hybrid | ✓ | ✓ | 1 |
| **Total** | | | **12/12** | **12/12** | |

## Assistant (manual spot checks, 2026-09-26)

| Question | Route/model | Verified citations | Dropped | Notes |
|---|---|---:|---:|---|
| What are students expected to have encountered before MDSC 407? | extract / gpt-6-sol | 6 | 0 | Separated sources, map and interpretation; flagged insufficient documentation; asked for the MDSC 407 outline |
| Which program outcomes does MDSC 407 support? (UI) | extract / gpt-6-sol | 1 | 0 | Reported no documented alignment; PLO4 and PLO5 offered as interpretation only |
| Minimal scenario for earlier statistics exposure (simulate) | pedagogy / claude-sonnet-5 | 4 | 0 | Two proposals, both valid against the projection, with assumptions |
| Critique of that proposal | critique / claude-opus-5-5 | n/a | n/a | 7 concerns (1 arguably wrong: a Year 3 attribution the webpage supports), 5 faculty questions |
