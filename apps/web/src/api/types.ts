/** Hand-written types for loosely typed JSON payloads (graph views, traversals, analyses). */
export type EntityType = "course" | "program_outcome" | "course_outcome" | "topic" | "assessment" | "activity";
export type Basis = "explicit_statement" | "interpretation" | "assumption" | "mixed";
export type ReviewState = "proposed" | "accepted" | "rejected" | "superseded";
export type ChangeStatus = "unchanged" | "added" | "removed" | "modified";

export interface VersionInfo {
  id: string;
  label: string;
  status: string;
  program_id: string;
  program_name: string;
  academic_year: string | null;
  cohort: string | null;
  is_synthetic: boolean;
}

export interface Placement {
  id: string;
  year: number | null;
  term: string;
  classification: string;
  pathway: string | null;
  offering_conditions: string | null;
}

export interface GraphNode {
  id: string;
  type: EntityType;
  key: string;
  title: string;
  description: string | null;
  year: number | null;
  term: string | null;
  classification: string | null;
  pathway: string | null;
  placements: Placement[];
  details: Record<string, unknown>;
  origin: string;
  is_synthetic: boolean;
  evidence: { fields: string[]; with_evidence: string[]; ratio: number | null };
  status: ChangeStatus;
  owner_course: string | null;
  expanded: boolean;
}

export interface GraphEdge {
  id: string;
  key: string | null;
  revision_id: string | null;
  source: string;
  target: string;
  type: string;
  basis: Basis;
  origin: string;
  review_state: ReviewState;
  level: string | null;
  rule_id: string | null;
  has_evidence: boolean;
  contradicted: boolean;
  derived: boolean;
  via: string[];
  status: ChangeStatus;
  rationale: string | null;
  is_synthetic: boolean;
  alternative_group?: string | null;
}

export interface GraphView {
  curriculum_version: VersionInfo;
  scenario: { id: string; title: string; revision: number } | null;
  scenario_revision: number | null;
  layers: string[];
  available_layers: string[];
  nodes: GraphNode[];
  edges: GraphEdge[];
  truncated: { total: number; shown: number; message: string } | null;
  stats: { nodes: number; edges: number; courses: number };
}

export interface EntitySnap {
  id: string;
  type: EntityType;
  key: string;
  revision_id: string;
  revision_number: number;
  title: string;
  description: string | null;
  details: Record<string, unknown>;
  origin: string;
  is_synthetic: boolean;
  field_evidence: Record<string, number>;
}

export interface EntityView {
  id: string;
  type: EntityType;
  key: string;
  title: string;
  placement?: Placement | null;
}

export interface PathEdge {
  key: string;
  type: string;
  source: string;
  target: string;
  basis: Basis;
  origin: string;
  review_state: ReviewState;
  has_evidence: boolean;
  via: string | null;
  reverse?: boolean;
}

export interface RuleEval {
  rule_id: string;
  kind: string;
  rendered: string;
  source_text: string | null;
  basis: Basis;
  review_state: ReviewState;
  evidence_span_id: string | null;
  status: "satisfied_for_all" | "conditional" | "unknown" | "unsatisfied";
  reasons: string[];
  course_codes: string[];
}

export interface PriorItem {
  entity: EntityView;
  depth: number;
  relation: "formal_requirement" | "inferred_preparation" | "topic_or_outcome_preparation";
  basis: "documented" | "inferred";
  path: string[];
  edges: PathEdge[];
  exposure?: string;
  timing?: string;
  opportunities?: { course: EntityView; exposure: string; timing: string }[];
  gap?: string;
}

export interface PriorLearning {
  entity: EntityView;
  requirement_rules: RuleEval[];
  items: PriorItem[];
  assumptions: Record<string, string>;
}

export interface Downstream {
  entity: EntityView;
  items: { entity: EntityView; depth: number; basis: string; path: string[]; edges: PathEdge[] }[];
  dependent_courses: { course: EntityView; via: string[]; basis: string }[];
  alternatives: { course: EntityView; alternatives: string[]; rule: string }[];
}

export interface Contribution {
  outcomes: {
    outcome: EntityView & { statement_verbatim: string | null; field_evidence: Record<string, number> };
    program_outcomes: { plo: EntityView; level: string | null; basis: Basis; review_state: ReviewState; has_evidence: boolean }[];
    assessed_by: EntityView[];
    basis: Basis;
    has_evidence: boolean;
  }[];
  topics: { topic: EntityView; basis: Basis; has_evidence: boolean }[];
  assessments: { assessment: EntityView; details: Record<string, unknown>; field_evidence: Record<string, number>; assesses: EntityView[] }[];
}

export interface EntityDetail {
  entity: EntitySnap;
  placements: Placement[];
  field_evidence: Record<string, { span_id: string; stance: string }[]>;
  revisions: { id: string; revision_number: number; origin: string; created_at: string }[];
  relationships: (GraphEdge & { other: string })[];
  curriculum_version: VersionInfo;
  contribution?: Contribution;
  requirement_rules?: RuleEval[];
  unknowns?: string[];
}

export interface RelationshipDetail {
  relationship: {
    key: string;
    revision_id: string;
    type: string;
    source: string;
    target: string;
    basis: Basis;
    origin: string;
    review_state: ReviewState;
    level: string | null;
    rationale: string | null;
    is_synthetic: boolean;
    evidence: { supporting: number; contradicting: number };
  };
  meaning: string;
  source: EntitySnap;
  target: EntitySnap;
  evidence: { span_id: string; stance: string; note: string | null }[];
  history: { revision_id: string; revision_number: number; review_state: string; created_at: string }[];
  rule: { id: string; rendered: string; source_text: string | null } | null;
  curriculum_version: VersionInfo;
}
