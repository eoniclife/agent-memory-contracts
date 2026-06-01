"""Contracts for untrusted candidate interpretation records."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .candidate_ids import make_candidate_id

SCHEMA_VERSION = "1.0.0"

CANDIDATE_TYPES = {"claim", "preference", "decision", "task", "taste_signal"}
CONFIDENCE = {"low", "medium", "high"}
RISK_CLASSES = {"low", "medium", "high"}
STATUSES = {"candidate", "rejected", "promoted", "duplicate", "needs_review"}
CLAIM_SCOPES = {"global", "project", "person", "company", "domain", "source_local"}
PREFERENCE_DOMAINS = {"writing", "product", "investing", "architecture", "design", "communication", "operations", "personal", "other"}
PREFERENCE_SCOPES = {"global", "project_specific", "context_specific"}
STRENGTH_HINTS = {"weak", "medium", "strong", "hard_constraint", "unknown"}
DECISION_SCOPES = {"project", "architecture", "personal", "operational", "other"}
REVERSIBILITY = {"low", "medium", "high", "unknown"}
TASK_KINDS = {"follow_up", "research", "build", "review", "write", "decide", "schedule", "monitor", "other"}
URGENCY_HINTS = {"low", "medium", "high", "unknown"}
SAFETY_LANES = {"internal_only", "requires_review", "external_action", "destructive", "sensitive"}
TASTE_DOMAINS = {"writing", "product", "investing", "architecture", "design", "communication", "operations", "other"}
TASTE_SIGNAL_KINDS = {"positive_example", "negative_example", "contrast_pair", "objection", "principle", "correction"}

FORBIDDEN_TRUSTED_MEMORY_KEYS = {
    "fact_id",
    "preference_id",
    "decision_id",
    "project_state_id",
    "taste_card_id",
    "promoted_at",
    "valid_from",
    "valid_until",
    "supersedes",
    "superseded_by",
    "truth_status",
    "trusted_memory",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _is_iso8601_or_none(value: str | None) -> bool:
    if value is None:
        return True
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _is_iso8601(value: str) -> bool:
    if not isinstance(value, str) or not value:
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except ValueError:
        return False


def _string_list(name: str, value: list[str], prefix: str | None = None, allow_empty: bool = True) -> None:
    _require(isinstance(value, list), f"{name} must be a list")
    if not allow_empty:
        _require(bool(value), f"{name} must not be empty")
    for item in value:
        _require(isinstance(item, str), f"{name} entries must be strings")
        if prefix:
            _require(item.startswith(prefix), f"{name} entries must start with {prefix}")


def _object(name: str, value: dict[str, Any]) -> None:
    _require(isinstance(value, dict), f"{name} must be object")


def _build_record(cls: type, data: dict[str, Any]):
    try:
        return cls(**data)
    except TypeError as exc:
        raise ValueError(f"invalid {cls.__name__}: {exc}") from exc


def _assert_no_forbidden_keys(record: dict[str, Any]) -> None:
    found = sorted(FORBIDDEN_TRUSTED_MEMORY_KEYS.intersection(record.keys()))
    _require(not found, f"candidate contains forbidden trusted-memory keys: {found}")
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        metadata_found = sorted(FORBIDDEN_TRUSTED_MEMORY_KEYS.intersection(metadata.keys()))
        _require(not metadata_found, f"candidate metadata contains forbidden trusted-memory keys: {metadata_found}")


@dataclass(frozen=True)
class CandidateBase:
    id: str
    schema_version: str
    candidate_type: str
    source_record_ids: list[str]
    episode_record_ids: list[str]
    evidence_span_ids: list[str]
    natural_language_summary: str
    extracted_by: dict[str, str | None]
    extracted_at: str
    confidence: str
    risk_class: str
    status: str
    review: dict[str, str | None]
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate_base(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "schema_version must be 1.0.0")
        _require(self.candidate_type in CANDIDATE_TYPES, "invalid candidate_type")
        _string_list("source_record_ids", self.source_record_ids, "src_")
        _string_list("episode_record_ids", self.episode_record_ids, "ep_")
        _string_list("evidence_span_ids", self.evidence_span_ids, "span_", allow_empty=False)
        _require(isinstance(self.natural_language_summary, str) and self.natural_language_summary, "natural_language_summary is required")
        _object("extracted_by", self.extracted_by)
        _require(set(self.extracted_by.keys()) == {"agent", "model", "tool", "prompt_ref"}, "extracted_by keys must be agent, model, tool, prompt_ref")
        for key in ["agent", "model"]:
            _require(isinstance(self.extracted_by.get(key), str) and self.extracted_by.get(key), f"extracted_by.{key} is required")
        for key in ["tool", "prompt_ref"]:
            _require(self.extracted_by.get(key) is None or isinstance(self.extracted_by.get(key), str), f"extracted_by.{key} invalid")
        _require(_is_iso8601(self.extracted_at), "extracted_at must be ISO-8601")
        _require(self.confidence in CONFIDENCE, "invalid confidence")
        _require(self.risk_class in RISK_CLASSES, "invalid risk_class")
        _require(self.status in STATUSES, "invalid status")
        _object("review", self.review)
        _require(set(self.review.keys()) == {"reviewed_by", "reviewed_at", "review_notes"}, "review keys must be reviewed_by, reviewed_at, review_notes")
        _require(self.review.get("reviewed_by") is None or isinstance(self.review.get("reviewed_by"), str), "review.reviewed_by invalid")
        _require(_is_iso8601_or_none(self.review.get("reviewed_at")), "review.reviewed_at invalid")
        _require(self.review.get("review_notes") is None or isinstance(self.review.get("review_notes"), str), "review.review_notes invalid")
        _object("metadata", self.metadata)
        metadata_found = sorted(FORBIDDEN_TRUSTED_MEMORY_KEYS.intersection(self.metadata.keys()))
        _require(not metadata_found, f"candidate metadata contains forbidden trusted-memory keys: {metadata_found}")
        _require(self.id == self.expected_id(), f"candidate id mismatch: expected {self.expected_id()}")

    def semantic_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def expected_id(self) -> str:
        return make_candidate_id(self.candidate_type, self.evidence_span_ids, self.semantic_payload())

    def validate(self) -> None:
        self.validate_base()


@dataclass(frozen=True)
class CandidateClaim(CandidateBase):
    subject: str = ""
    predicate: str = ""
    object: str = ""
    claim_text: str = ""
    claim_scope: str = ""
    temporal_hint: dict[str, str | None] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateClaim":
        _assert_no_forbidden_keys(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "claim_text": self.claim_text,
            "claim_scope": self.claim_scope,
            "temporal_hint": self.temporal_hint,
        }

    def validate(self) -> None:
        self.validate_base()
        _require(self.candidate_type == "claim", "candidate_type must be claim")
        for name in ["subject", "predicate", "object", "claim_text"]:
            _require(isinstance(getattr(self, name), str) and getattr(self, name), f"{name} is required")
        _require(self.claim_scope in CLAIM_SCOPES, "invalid claim_scope")
        _object("temporal_hint", self.temporal_hint)
        for key in ["observed_at", "asserted_at", "valid_from_hint", "valid_until_hint"]:
            _require(_is_iso8601_or_none(self.temporal_hint.get(key)), f"temporal_hint.{key} invalid")


@dataclass(frozen=True)
class CandidatePreference(CandidateBase):
    subject: str = ""
    preference_text: str = ""
    domain: str = ""
    scope: str = ""
    strength_hint: str = ""
    counterevidence_span_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidatePreference":
        _assert_no_forbidden_keys(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "preference_text": self.preference_text,
            "domain": self.domain,
            "scope": self.scope,
            "strength_hint": self.strength_hint,
            "counterevidence_span_ids": sorted(self.counterevidence_span_ids),
        }

    def validate(self) -> None:
        self.validate_base()
        _require(self.candidate_type == "preference", "candidate_type must be preference")
        _require(isinstance(self.subject, str) and self.subject, "subject is required")
        _require(isinstance(self.preference_text, str) and self.preference_text, "preference_text is required")
        _require(self.domain in PREFERENCE_DOMAINS, "invalid domain")
        _require(self.scope in PREFERENCE_SCOPES, "invalid scope")
        _require(self.strength_hint in STRENGTH_HINTS, "invalid strength_hint")
        _string_list("counterevidence_span_ids", self.counterevidence_span_ids, "span_")


@dataclass(frozen=True)
class CandidateDecision(CandidateBase):
    decision_text: str = ""
    decision_scope: str = ""
    alternatives_mentioned: list[str] = field(default_factory=list)
    rationale_text: str | None = None
    decision_time_hint: str | None = None
    owner_hint: str | None = None
    reversibility: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateDecision":
        _assert_no_forbidden_keys(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "decision_text": self.decision_text,
            "decision_scope": self.decision_scope,
            "alternatives_mentioned": self.alternatives_mentioned,
            "rationale_text": self.rationale_text,
            "decision_time_hint": self.decision_time_hint,
            "owner_hint": self.owner_hint,
            "reversibility": self.reversibility,
        }

    def validate(self) -> None:
        self.validate_base()
        _require(self.candidate_type == "decision", "candidate_type must be decision")
        _require(isinstance(self.decision_text, str) and self.decision_text, "decision_text is required")
        _require(self.decision_scope in DECISION_SCOPES, "invalid decision_scope")
        _string_list("alternatives_mentioned", self.alternatives_mentioned)
        _require(self.rationale_text is None or isinstance(self.rationale_text, str), "rationale_text invalid")
        _require(_is_iso8601_or_none(self.decision_time_hint), "decision_time_hint invalid")
        _require(self.owner_hint is None or isinstance(self.owner_hint, str), "owner_hint invalid")
        _require(self.reversibility in REVERSIBILITY, "invalid reversibility")


@dataclass(frozen=True)
class CandidateTask(CandidateBase):
    task_text: str = ""
    task_kind: str = ""
    project_refs: list[str] = field(default_factory=list)
    owner_hint: str | None = None
    due_at_hint: str | None = None
    urgency_hint: str = ""
    safety_lane: str = ""
    autostart_eligible: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateTask":
        _assert_no_forbidden_keys(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "task_text": self.task_text,
            "task_kind": self.task_kind,
            "project_refs": self.project_refs,
            "owner_hint": self.owner_hint,
            "due_at_hint": self.due_at_hint,
            "urgency_hint": self.urgency_hint,
            "safety_lane": self.safety_lane,
            "autostart_eligible": self.autostart_eligible,
        }

    def validate(self) -> None:
        self.validate_base()
        _require(self.candidate_type == "task", "candidate_type must be task")
        _require(isinstance(self.task_text, str) and self.task_text, "task_text is required")
        _require(self.task_kind in TASK_KINDS, "invalid task_kind")
        _string_list("project_refs", self.project_refs)
        _require(self.owner_hint is None or isinstance(self.owner_hint, str), "owner_hint invalid")
        _require(_is_iso8601_or_none(self.due_at_hint), "due_at_hint invalid")
        _require(self.urgency_hint in URGENCY_HINTS, "invalid urgency_hint")
        _require(self.safety_lane in SAFETY_LANES, "invalid safety_lane")
        _require(self.autostart_eligible is False, "CandidateTask autostart_eligible must be false in Sprint 2")


@dataclass(frozen=True)
class CandidateTasteSignal(CandidateBase):
    domain: str = ""
    signal_kind: str = ""
    taste_text: str = ""
    example_span_ids: list[str] = field(default_factory=list)
    contrast_span_ids: list[str] = field(default_factory=list)
    strength_hint: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CandidateTasteSignal":
        _assert_no_forbidden_keys(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "signal_kind": self.signal_kind,
            "taste_text": self.taste_text,
            "example_span_ids": sorted(self.example_span_ids),
            "contrast_span_ids": sorted(self.contrast_span_ids),
            "strength_hint": self.strength_hint,
        }

    def validate(self) -> None:
        self.validate_base()
        _require(self.candidate_type == "taste_signal", "candidate_type must be taste_signal")
        _require(self.domain in TASTE_DOMAINS, "invalid domain")
        _require(self.signal_kind in TASTE_SIGNAL_KINDS, "invalid signal_kind")
        _require(isinstance(self.taste_text, str) and self.taste_text, "taste_text is required")
        _string_list("example_span_ids", self.example_span_ids, "span_")
        _string_list("contrast_span_ids", self.contrast_span_ids, "span_")
        _require(self.example_span_ids or self.contrast_span_ids, "taste signals require EvidenceSpan anchors")
        _require(self.strength_hint in STRENGTH_HINTS, "invalid strength_hint")


CLASS_BY_TYPE = {
    "claim": CandidateClaim,
    "preference": CandidatePreference,
    "decision": CandidateDecision,
    "task": CandidateTask,
    "taste_signal": CandidateTasteSignal,
}


def candidate_from_dict(data: dict[str, Any]) -> CandidateBase:
    _assert_no_forbidden_keys(data)
    candidate_type = data.get("candidate_type")
    _require(candidate_type in CLASS_BY_TYPE, "invalid candidate_type")
    return CLASS_BY_TYPE[candidate_type].from_dict(data)


def candidate_auxiliary_span_ids(candidate: CandidateBase) -> list[str]:
    if isinstance(candidate, CandidatePreference):
        return list(candidate.counterevidence_span_ids)
    if isinstance(candidate, CandidateTasteSignal):
        return list(candidate.example_span_ids) + list(candidate.contrast_span_ids)
    return []


def validate_span_refs(candidate: CandidateBase, span_ids: Iterable[str], spans_by_id: dict[str, dict[str, Any]], *, require_primary: bool) -> None:
    primary_span_ids = set(candidate.evidence_span_ids)
    for span_id in span_ids:
        _require(span_id in spans_by_id, f"dangling evidence_span_id: {span_id}")
        if require_primary:
            _require(span_id in primary_span_ids, f"auxiliary evidence span not included in evidence_span_ids: {span_id}")
        span = spans_by_id[span_id]
        if candidate.source_record_ids:
            _require(span["source_id"] in candidate.source_record_ids, "candidate span source not in source_record_ids")
        if candidate.episode_record_ids and span.get("episode_id") is not None:
            _require(span["episode_id"] in candidate.episode_record_ids, "candidate span episode not in episode_record_ids")


def validate_candidate_bundle(
    source_records: Iterable[dict[str, Any]],
    episode_records: Iterable[dict[str, Any]],
    evidence_spans: Iterable[dict[str, Any]],
    candidate_records: Iterable[dict[str, Any]],
) -> None:
    source_ids = {record["id"] for record in source_records}
    episode_ids = {record["id"] for record in episode_records}
    spans_by_id = {record["id"]: record for record in evidence_spans}
    seen_candidate_ids: set[str] = set()

    for raw in candidate_records:
        _assert_no_forbidden_keys(raw)
        candidate = candidate_from_dict(raw)
        _require(candidate.id not in seen_candidate_ids, f"duplicate candidate id: {candidate.id}")
        seen_candidate_ids.add(candidate.id)

        for source_id in candidate.source_record_ids:
            _require(source_id in source_ids, f"dangling source_record_id: {source_id}")
        for episode_id in candidate.episode_record_ids:
            _require(episode_id in episode_ids, f"dangling episode_record_id: {episode_id}")
        validate_span_refs(candidate, candidate.evidence_span_ids, spans_by_id, require_primary=False)
        validate_span_refs(candidate, candidate_auxiliary_span_ids(candidate), spans_by_id, require_primary=True)
