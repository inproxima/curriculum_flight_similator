"""Domain enumerations. Stored as constrained VARCHAR (non-native enums) so migrations stay simple."""

from enum import StrEnum


class Role(StrEnum):
    admin = "admin"
    editor = "editor"
    reviewer = "reviewer"
    viewer = "viewer"


class VersionStatus(StrEnum):
    draft = "draft"
    published = "published"
    archived = "archived"


class DocumentType(StrEnum):
    program_outline = "program_outline"
    review_report = "review_report"
    webpage_extract = "webpage_extract"
    course_outline = "course_outline"
    assessment_document = "assessment_document"
    pasted_text = "pasted_text"
    other = "other"


class ProcessingStatus(StrEnum):
    uploaded = "uploaded"
    processing = "processing"
    processed = "processed"
    partial = "partial"
    failed = "failed"


class SourceAuthority(StrEnum):
    authoritative = "authoritative"
    supporting = "supporting"
    historical_reference = "historical_reference"
    exploratory = "exploratory"


class ApprovalStatus(StrEnum):
    proposed = "proposed"
    approved = "approved"
    rejected = "rejected"


class OcrState(StrEnum):
    not_needed = "not_needed"
    needed = "needed"
    done = "done"
    unavailable = "unavailable"


class EntityType(StrEnum):
    course = "course"
    program_outcome = "program_outcome"
    course_outcome = "course_outcome"
    topic = "topic"
    assessment = "assessment"
    activity = "activity"


class Origin(StrEnum):
    extracted = "extracted"
    manual = "manual"
    ai_inferred = "ai_inferred"
    rule_derived = "rule_derived"
    synthetic_fixture = "synthetic_fixture"


class EvidenceBasis(StrEnum):
    explicit_statement = "explicit_statement"
    interpretation = "interpretation"
    assumption = "assumption"


class ReviewState(StrEnum):
    proposed = "proposed"
    accepted = "accepted"
    rejected = "rejected"
    superseded = "superseded"


class Term(StrEnum):
    fall = "fall"
    winter = "winter"
    spring = "spring"
    summer = "summer"
    full_year = "full_year"
    unknown = "unknown"


class PlacementClass(StrEnum):
    required = "required"
    pathway_required = "pathway_required"
    elective = "elective"
    optional = "optional"
    unknown = "unknown"


class RelType(StrEnum):
    """Direction convention.

    Dependency edges: prerequisite / supporting element -> dependent element.
    Containment edges: container (course) -> member (outcome, topic, assessment, activity).
    Alignment edges: contributing element -> element it contributes to.
    Overlap edges are symmetric; stored with source_id < target_id.
    """

    # containment
    has_outcome = "has_outcome"
    covers_topic = "covers_topic"
    has_assessment = "has_assessment"
    has_activity = "has_activity"
    # alignment
    contributes_to = "contributes_to"  # course_outcome -> program_outcome
    assesses = "assesses"  # assessment -> outcome
    supports = "supports"  # activity -> outcome/topic
    # dependency
    formal_prerequisite = "formal_prerequisite"  # documented requisite, course -> course
    corequisite = "corequisite"  # documented co-requisite, symmetric
    inferred_preparation = "inferred_preparation"  # pedagogical preparation, not a formal rule
    prepares_for = "prepares_for"  # topic/outcome -> later topic/outcome/assessment
    # non-propagating
    possible_overlap = "possible_overlap"


DEPENDENCY_TYPES = {RelType.formal_prerequisite, RelType.inferred_preparation, RelType.prepares_for}
CONTAINMENT_TYPES = {RelType.has_outcome, RelType.covers_topic, RelType.has_assessment, RelType.has_activity}
ALIGNMENT_TYPES = {RelType.contributes_to, RelType.assesses, RelType.supports}
NON_PROPAGATING_TYPES = {RelType.possible_overlap, RelType.corequisite}


class RuleKind(StrEnum):
    prerequisite = "prerequisite"
    corequisite = "corequisite"
    antirequisite = "antirequisite"


class RequirementGroupKind(StrEnum):
    elective_group = "elective_group"
    requirement = "requirement"


class ContributionLevel(StrEnum):
    introduce = "introduce"
    reinforce = "reinforce"
    assess = "assess"


class EvidenceStance(StrEnum):
    supporting = "supporting"
    contradicting = "contradicting"


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    partial = "partial"
    failed = "failed"
    cancelled = "cancelled"


class ReviewItemKind(StrEnum):
    possible_duplicate = "possible_duplicate"
    uncertain_course_match = "uncertain_course_match"
    candidate_entity = "candidate_entity"
    candidate_relationship = "candidate_relationship"
    inferred_mapping = "inferred_mapping"
    conflicting_requirement = "conflicting_requirement"
    missing_academic_year = "missing_academic_year"
    unreadable_page = "unreadable_page"
    weak_evidence = "weak_evidence"


class ReviewItemStatus(StrEnum):
    open = "open"
    accepted = "accepted"
    edited = "edited"
    rejected = "rejected"
    deferred = "deferred"


class ScenarioState(StrEnum):
    draft = "draft"
    under_review = "under_review"
    approved = "approved"
    published = "published"
    archived = "archived"


class ConsequenceClass(StrEnum):
    direct_documented = "direct_documented"
    indirect_potential = "indirect_potential"
    uncertain_inferred = "uncertain_inferred"
    judgment_needed = "judgment_needed"
    missing_evidence = "missing_evidence"


class Severity(StrEnum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"
