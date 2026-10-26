# Domain model (implemented)

## Identity and scope
`organizations` → `memberships(role: admin|editor|reviewer|viewer)` ← `users`. Every scoped row carries
`organization_id`. Every lookup goes through `cfs.core.scope.get_scoped`, and rows in another organization return
404, so their existence isn't disclosed. `programs` → `pathways`, `curriculum_versions(status: draft|published|archived)`.

## Documents and evidence
| Table | Purpose |
|---|---|
| `documents` | Stable document identity: title, source URL, type |
| `document_versions` | Immutable file: sha256 (unique per org), object key, MIME, **publication date**, **retrieval date**, **academic year**, **cohort applicability** (separate fields), processing status |
| `curriculum_sources` | Explicit document-version → curriculum-version assignment: authority (`authoritative`, `supporting`, `historical_reference`, `exploratory`), applicability, exploratory cross-version flag |
| `document_pages` | Original text, normalized text (FTS), extraction method, parser version, OCR state |
| `document_chunks` | Section-aware chunks with page ranges (FTS). `chunk_embeddings` (vector(1536), HNSW) exists for Phase 5 |
| `evidence_spans` | Immutable page + character offsets + verbatim quote + optional line boxes |
| `ingest_stage_runs` | Stage idempotency records |

## Curriculum
`entities` (stable identity: type + stable key such as `SYN 407`) → `entity_revisions` (immutable) with typed
detail tables (`course_revision_details`, `outcome_revision_details`, `assessment_revision_details`,
`topic_revision_details`, `activity_revision_details`). `curriculum_entity_memberships` pins a version to exact
revisions. `course_placements` holds year, term, pathway, and classification per version. `requirement_rules`
stores requisites as expression trees. `requirement_groups` holds elective groups.

### Relationships
`relationship_revisions` (immutable; stable `relationship_key`) + `curriculum_relationship_memberships` +
`relationship_evidence` (supporting/contradicting) + `entity_field_evidence` (per field, not per entity).

Separate columns for **origin** (`extracted`, `manual`, `ai_inferred`, `rule_derived`, `synthetic_fixture`),
**evidence basis** (`explicit_statement`, `interpretation`, `assumption`), and **review state** (`proposed`,
`accepted`, `rejected`, `superseded`). Accepting an inferred relationship changes the review state only. Relabeling
an interpretation as an explicit statement is refused (`basis_relabel_forbidden`).

Alignment links also carry an asserted **strength** (0–1), its **strength_source** (`ai_estimate`, `reviewer`,
`fixture`, `scenario`), and, for AI proposals, the `model_run_id`. The strong/moderate/weak label is computed by
`cfs.analysis.alignment` and never stored (ADR 0003). The same holds for the **connection level** (ADR 0005):
- a documented link is `direct`;
- an asserted strength ≥ 0.9 is `direct`, ≥ 0.7 is `strong`, and anything lower is `partial`;
- without an asserted strength, a computed *strong* label gives `strong` and anything else `partial`.

`TYPE_PAIRS` in that module lists the allowed endpoint types for each relationship type.

| Type | Direction | Propagates impact? |
|---|---|---|
| `formal_prerequisite` | prerequisite course → dependent course (derived from a rule; OR members flagged) | yes (evaluated against the rule) |
| `corequisite` | symmetric | no (not a cycle) |
| `inferred_preparation` | earlier course → later course | yes, as *uncertain* |
| `prepares_for` | topic/outcome/assessment → later topic/outcome (horizontal alignment) | yes |
| `contributes_to` | course outcome → program outcome (I/R/A level when stated) | yes (coverage) |
| `assesses` | assessment → outcome | reverse (assessment depends on outcome) |
| `supports` | topic (or activity) → course outcome | yes |
| `has_outcome`, `covers_topic`, `has_assessment` | course → member | containment only |
| `possible_overlap` | symmetric | **never** |

## Scenarios and analysis
`scenarios(base_version_id, revision, head)` → ordered `scenario_changes(op_type, payload, before, after,
assumptions)`. Operations: `add_course`, `remove_course`, `move_course`, `set_classification`, `add_outcome`,
`remove_outcome`, `modify_outcome`, `add_topic`, `remove_topic`, `change_assessment` (absolute or relative
weeks), `add_assessment`, `remove_assessment`, `change_delivery`, `set_workload` (low/typical/high assumption),
`modify_requirement`, `add_relationship`, `remove_relationship`, and for the Alignment Web: `modify_course`,
`add_program_outcome`, `modify_program_outcome`, `remove_program_outcome`, `modify_topic` (including the outcomes
it supports), `change_assessment` (title, description, hours, and the outcomes it assesses), and
`set_relationship_strength`. Contributions may carry an asserted strength. The diff reports relationships as
added, removed, or **modified**, and publishing revises modified links in place.

`analysis_runs(scenario_revision, input_hash, algorithm_version)` → `findings(consequence_class, severity,
evidence_basis, affected ids, assumptions, suggested actions)` + `finding_paths` + `finding_evidence`.
Consequence classes: `direct_documented`, `indirect_potential`, `uncertain_inferred`, `judgment_needed`,
`missing_evidence`. A run is **stale** when the scenario's revision or input hash has changed since it ran.
The run summary also holds `ripple` (elements reached per hop, ranked impacts with status and path) and a
`scorecard` (baseline, scenario, delta).

## Operations
`jobs` + `job_events` (durable progress; SSE replays from `Last-Event-ID`), `review_items` +
`review_decisions`, `audit_events`, `saved_views`. `conversations`, `messages`, `model_runs`, and `tool_calls`
exist for Phase 5.

## Course outline imports (ADR 0004)
`outline_imports` holds one outline reconciled against one course of one draft version. Its columns:
- `organization_id`, `curriculum_version_id`, `course_entity_id`, `document_version_id`, `job_id`;
- `status`: `queued`, `running`, `ready`, `applied`, `discarded`, `failed` or `reverted`;
- `options`: what the user entered;
- `edition`;
- `extraction`: the verified candidates, reused when the import is re-run;
- `plan`: items with their evidence, reasons, `depends_on` / `excludes`, and the missing pieces each one fills;
- `state_hash` and `plan_hash` (the ETag is a hash of both);
- `result`: counts, the missing pieces left, and `undo`, an ordered list of `{kind, id, before, after}` for
  memberships, placements, sources, review items and the document's academic year;
- `error`, and who created, applied and reverted it, and when.

The `outline_import` job reads, extracts, compares, reconciles, verifies and plans; it never writes curriculum data.
Apply and revert run in one transaction under a row lock on the version. Retiring an element removes its membership
and every link touching it from that draft only. Replacing an element is a new revision of the same entity. Evidence
is never attached to a revision another version shares.

## Connection map (ADR 0005)
`alignment_verdicts` records what the AI mapping concluded for one element of one draft version. One row per
(`curriculum_version_id`, `entity_id`, `rel_type`):
- `rel_type`: `contributes_to`, `assesses`, `supports`, `prepares_for`, or `prepared_by` (whether anything in an
  earlier course prepares for it);
- `verdict`: `linked` or `none`;
- `reason`: the model's explanation for `none`;
- `model_run_id` (`SET NULL` on delete) and `examined_at`.

No row means "not mapped". Verdicts are advisory: the links themselves are ordinary proposed relationships
(`ai_inferred`, `interpretation`) with the level stored as an asserted strength. The `ai_map_alignment` job
(payload: `curriculum_version_id`, `course_ids`, `forward`, `cost_cap_usd`) writes both, on draft versions only.
The Web payload exposes, per edge, `connection`, and per outcome, assessment and topic, a `mapping` object of the
form `{kind: {best, count, verdict, reason}}`.
