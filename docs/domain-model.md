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

| Type | Direction | Propagates impact? |
|---|---|---|
| `formal_prerequisite` | prerequisite course → dependent course (derived from a rule; OR members flagged) | yes (evaluated against the rule) |
| `corequisite` | symmetric | no (not a cycle) |
| `inferred_preparation` | earlier course → later course | yes, as *uncertain* |
| `prepares_for` | topic/outcome → later topic/outcome | yes |
| `contributes_to` | course outcome → program outcome (I/R/A level when stated) | yes (coverage) |
| `assesses` | assessment → outcome | reverse (assessment depends on outcome) |
| `has_outcome`, `covers_topic`, `has_assessment` | course → member | containment only |
| `possible_overlap` | symmetric | **never** |

## Scenarios and analysis
`scenarios(base_version_id, revision, head)` → ordered `scenario_changes(op_type, payload, before, after,
assumptions)`. Operations: `add_course`, `remove_course`, `move_course`, `set_classification`, `add_outcome`,
`remove_outcome`, `modify_outcome`, `add_topic`, `remove_topic`, `change_assessment` (absolute or relative
weeks), `add_assessment`, `remove_assessment`, `change_delivery`, `set_workload` (low/typical/high assumption),
`modify_requirement`, `add_relationship`, `remove_relationship`.

`analysis_runs(scenario_revision, input_hash, algorithm_version)` → `findings(consequence_class, severity,
evidence_basis, affected ids, assumptions, suggested actions)` + `finding_paths` + `finding_evidence`.
Consequence classes: `direct_documented`, `indirect_potential`, `uncertain_inferred`, `judgment_needed`,
`missing_evidence`. A run is **stale** when the scenario's revision or input hash has changed since it ran.

## Operations
`jobs` + `job_events` (durable progress; SSE replays from `Last-Event-ID`), `review_items` +
`review_decisions`, `audit_events`, `saved_views`. `conversations`, `messages`, `model_runs`, and `tool_calls`
exist for Phase 5.
