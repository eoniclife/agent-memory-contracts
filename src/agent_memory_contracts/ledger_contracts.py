"""Contracts for reducer-approved trusted memory ledgers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .ledger_ids import make_ledger_entry_id, make_reducer_decision_id

SCHEMA_VERSION = "1.0.0"

DECISION_TYPES = {"promote", "reject", "supersede", "retract", "contest", "archive"}
LEDGER_TYPES = {"fact", "preference", "decision"}
LEDGER_STATUSES = {"active", "stale", "superseded", "retracted", "contested", "archived"}
CONFIDENCE = {"low", "medium", "high"}
RISK_CLASSES = {"low", "medium", "high"}
CHECK_VALUES = {"pass", "fail", "unknown"}
CHECK_KEYS = {"provenance", "temporal_validity", "contradiction_scan", "privacy", "usefulness"}
SCOPES = {"global", "project", "person", "company", "domain", "source_local", "context_specific"}
PREFERENCE_DOMAINS = {"writing", "product", "investing", "architecture", "design", "communication", "operations", "personal", "other"}
STRENGTHS = {"weak", "medium", "strong", "hard_constraint"}
DECISION_SCOPES = {"project", "architecture", "personal", "operational", "other"}
REVERSIBILITY = {"low", "medium", "high", "unknown"}
LEDGER_PREFIXES = ("fact_", "pref_", "dec_")

CANDIDATE_ONLY_FIELDS = {
    "candidate_type",
    "extracted_at",
    "extracted_by",
    "natural_language_summary",
    "review",
    "autostart_eligible",
    "counterevidence_span_ids",
    "example_span_ids",
    "contrast_span_ids",
    "claim_scope",
    "temporal_hint",
    "strength_hint",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def parse_iso8601(value: str) -> datetime:
    _require(isinstance(value, str) and bool(value), "expected non-empty ISO-8601 string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_iso8601(value: str) -> bool:
    try:
        parse_iso8601(value)
        return True
    except (TypeError, ValueError):
        return False


def _is_iso8601_or_none(value: str | None) -> bool:
    if value is None:
        return True
    return _is_iso8601(value)


def _string_list(name: str, value: list[str], prefix: str | tuple[str, ...] | None = None, allow_empty: bool = True) -> None:
    _require(isinstance(value, list), f"{name} must be a list")
    if not allow_empty:
        _require(bool(value), f"{name} must not be empty")
    for item in value:
        _require(isinstance(item, str), f"{name} entries must be strings")
        if prefix is not None:
            _require(item.startswith(prefix), f"{name} entries must start with {prefix}")


def _object(name: str, value: dict[str, Any]) -> None:
    _require(isinstance(value, dict), f"{name} must be object")


def _build_record(cls: type, data: dict[str, Any]):
    try:
        return cls(**data)
    except TypeError as exc:
        raise ValueError(f"invalid {cls.__name__}: {exc}") from exc


def _assert_no_candidate_only_fields(record: dict[str, Any]) -> None:
    found = sorted(CANDIDATE_ONLY_FIELDS.intersection(record.keys()))
    _require(not found, f"ledger record contains candidate-only fields: {found}")
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        metadata_found = sorted(CANDIDATE_ONLY_FIELDS.intersection(metadata.keys()))
        _require(not metadata_found, f"ledger metadata contains candidate-only fields: {metadata_found}")


def _validate_temporal_order(name: str, start: str | None, end: str | None) -> None:
    if start is not None and end is not None:
        _require(parse_iso8601(end) >= parse_iso8601(start), f"{name} must be >= valid_from")


@dataclass(frozen=True)
class MemoryReducerDecision:
    id: str
    schema_version: str
    decision_type: str
    target_candidate_ids: list[str]
    target_ledger_entry_ids: list[str]
    evidence_span_ids: list[str]
    rationale: str
    decided_by: dict[str, str | None]
    decided_at: str
    confidence: str
    risk_class: str
    checks: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryReducerDecision":
        record = _build_record(cls, data)
        record.validate()
        return record

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "schema_version must be 1.0.0")
        _require(self.decision_type in DECISION_TYPES, "invalid decision_type")
        _string_list("target_candidate_ids", self.target_candidate_ids, "cand_")
        _string_list("target_ledger_entry_ids", self.target_ledger_entry_ids, LEDGER_PREFIXES)
        if self.decision_type == "reject":
            _require(not self.target_ledger_entry_ids, "reject reducer decisions must not target ledger entries")
        _string_list("evidence_span_ids", self.evidence_span_ids, "span_", allow_empty=False)
        _require(isinstance(self.rationale, str) and self.rationale, "rationale is required")
        _object("decided_by", self.decided_by)
        _require(set(self.decided_by.keys()) == {"agent", "model", "tool", "prompt_ref"}, "decided_by keys must be agent, model, tool, prompt_ref")
        for key in ["agent", "model"]:
            _require(isinstance(self.decided_by.get(key), str) and self.decided_by.get(key), f"decided_by.{key} is required")
        for key in ["tool", "prompt_ref"]:
            _require(self.decided_by.get(key) is None or isinstance(self.decided_by.get(key), str), f"decided_by.{key} invalid")
        _require(_is_iso8601(self.decided_at), "decided_at must be ISO-8601")
        _require(self.confidence in CONFIDENCE, "invalid confidence")
        _require(self.risk_class in RISK_CLASSES, "invalid risk_class")
        _object("checks", self.checks)
        _require(set(self.checks.keys()) == CHECK_KEYS, "checks must include provenance, temporal_validity, contradiction_scan, privacy, usefulness")
        for key, value in self.checks.items():
            _require(value in CHECK_VALUES, f"checks.{key} invalid")
        _object("metadata", self.metadata)
        _require(self.id == self.expected_id(), f"reducer decision id mismatch: expected {self.expected_id()}")

    def expected_id(self) -> str:
        return make_reducer_decision_id(
            self.decision_type,
            self.target_candidate_ids,
            self.target_ledger_entry_ids,
            self.evidence_span_ids,
            self.rationale,
        )


@dataclass(frozen=True)
class LedgerEntryBase:
    id: str
    schema_version: str
    ledger_type: str
    status: str
    confidence: str
    scope: str
    source_record_ids: list[str]
    episode_record_ids: list[str]
    evidence_span_ids: list[str]
    candidate_ids: list[str]
    reducer_decision_id: str
    observed_at: str | None
    asserted_at: str | None
    valid_from: str | None
    valid_until: str | None
    stale_after: str | None
    created_at: str
    updated_at: str
    supersedes: list[str]
    superseded_by: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    def validate_base(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "schema_version must be 1.0.0")
        _require(self.ledger_type in LEDGER_TYPES, "invalid ledger_type")
        _require(self.status in LEDGER_STATUSES, "invalid status")
        _require(self.confidence in CONFIDENCE, "invalid confidence")
        _require(self.scope in SCOPES, "invalid scope")
        _string_list("source_record_ids", self.source_record_ids, "src_")
        _string_list("episode_record_ids", self.episode_record_ids, "ep_")
        _string_list("evidence_span_ids", self.evidence_span_ids, "span_", allow_empty=False)
        _string_list("candidate_ids", self.candidate_ids, "cand_")
        _require(isinstance(self.reducer_decision_id, str) and self.reducer_decision_id.startswith("redmem_"), "reducer_decision_id is required")
        for name in ["observed_at", "asserted_at", "valid_from", "valid_until", "stale_after"]:
            _require(_is_iso8601_or_none(getattr(self, name)), f"{name} must be ISO-8601 or null")
        _require(_is_iso8601(self.created_at), "created_at must be ISO-8601")
        _require(_is_iso8601(self.updated_at), "updated_at must be ISO-8601")
        _validate_temporal_order("valid_until", self.valid_from, self.valid_until)
        _validate_temporal_order("stale_after", self.valid_from, self.stale_after)
        _string_list("supersedes", self.supersedes, LEDGER_PREFIXES)
        _string_list("superseded_by", self.superseded_by, LEDGER_PREFIXES)
        if self.status == "superseded":
            _require(bool(self.superseded_by), "status superseded requires superseded_by")
            _require(self.valid_until is not None, "status superseded requires valid_until")
        if self.status == "active":
            _require(not self.superseded_by, "active entries must not have superseded_by")
        _object("metadata", self.metadata)
        _assert_no_candidate_only_fields(self.__dict__)
        _require(self.id == self.expected_id(), f"ledger entry id mismatch: expected {self.expected_id()}")

    def semantic_payload(self) -> dict[str, Any]:
        raise NotImplementedError

    def expected_id(self) -> str:
        return make_ledger_entry_id(self.ledger_type, self.evidence_span_ids, self.semantic_payload())

    def validate(self) -> None:
        self.validate_base()


@dataclass(frozen=True)
class FactLedgerEntry(LedgerEntryBase):
    subject: str = ""
    predicate: str = ""
    object: str = ""
    fact_text: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FactLedgerEntry":
        _assert_no_candidate_only_fields(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "ledger_type": self.ledger_type,
            "subject": self.subject,
            "predicate": self.predicate,
            "object": self.object,
            "scope": self.scope,
            "valid_from": self.valid_from,
            "evidence_span_ids": sorted(self.evidence_span_ids),
        }

    def validate(self) -> None:
        self.validate_base()
        _require(self.ledger_type == "fact", "ledger_type must be fact")
        for name in ["subject", "predicate", "object", "fact_text"]:
            _require(isinstance(getattr(self, name), str) and getattr(self, name), f"{name} is required")


@dataclass(frozen=True)
class PreferenceLedgerEntry(LedgerEntryBase):
    subject: str = ""
    preference_text: str = ""
    domain: str = ""
    strength: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PreferenceLedgerEntry":
        _assert_no_candidate_only_fields(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "ledger_type": self.ledger_type,
            "subject": self.subject,
            "preference_text": self.preference_text,
            "domain": self.domain,
            "scope": self.scope,
            "valid_from": self.valid_from,
            "evidence_span_ids": sorted(self.evidence_span_ids),
        }

    def validate(self) -> None:
        self.validate_base()
        _require(self.ledger_type == "preference", "ledger_type must be preference")
        _require(isinstance(self.subject, str) and self.subject, "subject is required")
        _require(isinstance(self.preference_text, str) and self.preference_text, "preference_text is required")
        _require(self.domain in PREFERENCE_DOMAINS, "invalid domain")
        _require(self.strength in STRENGTHS, "invalid strength")


@dataclass(frozen=True)
class DecisionLedgerEntry(LedgerEntryBase):
    decision_text: str = ""
    decision_scope: str = ""
    alternatives_considered: list[str] = field(default_factory=list)
    rationale_text: str | None = None
    owner: str | None = None
    reversibility: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DecisionLedgerEntry":
        _assert_no_candidate_only_fields(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "ledger_type": self.ledger_type,
            "decision_text": self.decision_text,
            "decision_scope": self.decision_scope,
            "scope": self.scope,
            "valid_from": self.valid_from,
            "evidence_span_ids": sorted(self.evidence_span_ids),
        }

    def validate(self) -> None:
        self.validate_base()
        _require(self.ledger_type == "decision", "ledger_type must be decision")
        _require(isinstance(self.decision_text, str) and self.decision_text, "decision_text is required")
        _require(self.decision_scope in DECISION_SCOPES, "invalid decision_scope")
        _string_list("alternatives_considered", self.alternatives_considered)
        _require(self.rationale_text is None or isinstance(self.rationale_text, str), "rationale_text invalid")
        _require(self.owner is None or isinstance(self.owner, str), "owner invalid")
        _require(self.reversibility in REVERSIBILITY, "invalid reversibility")


LEDGER_CLASS_BY_TYPE = {
    "fact": FactLedgerEntry,
    "preference": PreferenceLedgerEntry,
    "decision": DecisionLedgerEntry,
}


def reducer_decision_from_dict(data: dict[str, Any]) -> MemoryReducerDecision:
    return MemoryReducerDecision.from_dict(data)


def ledger_entry_from_dict(data: dict[str, Any]) -> LedgerEntryBase:
    _assert_no_candidate_only_fields(data)
    ledger_type = data.get("ledger_type")
    _require(ledger_type in LEDGER_CLASS_BY_TYPE, "invalid ledger_type")
    return LEDGER_CLASS_BY_TYPE[ledger_type].from_dict(data)


def _validate_span_refs(record: Any, span_ids: Iterable[str], spans_by_id: dict[str, dict[str, Any]]) -> None:
    for span_id in span_ids:
        _require(span_id in spans_by_id, f"dangling evidence_span_id: {span_id}")
        span = spans_by_id[span_id]
        if getattr(record, "source_record_ids", None):
            _require(span["source_id"] in record.source_record_ids, "span source not in source_record_ids")
        if getattr(record, "episode_record_ids", None) and span.get("episode_id") is not None:
            _require(span["episode_id"] in record.episode_record_ids, "span episode not in episode_record_ids")


def _validate_reducer_authorizes_entry(entry: LedgerEntryBase, reducer: MemoryReducerDecision) -> None:
    _require(entry.id in reducer.target_ledger_entry_ids, "ledger entry is not targeted by reducer decision")
    if entry.candidate_ids:
        _require(set(entry.candidate_ids).issubset(set(reducer.target_candidate_ids)), "ledger candidate_ids not authorized by reducer decision")
    else:
        _require(entry.metadata.get("human_asserted") is True, "ledger entries without candidate_ids require metadata.human_asserted")
    _require(set(entry.evidence_span_ids).issubset(set(reducer.evidence_span_ids)), "ledger evidence_span_ids not authorized by reducer decision")

    if entry.status == "active" and not entry.supersedes:
        expected = {"promote"}
    elif entry.status == "active" and entry.supersedes:
        expected = {"supersede"}
    elif entry.status == "stale":
        expected = {"promote", "supersede"}
    elif entry.status == "superseded":
        expected = {"supersede"}
    elif entry.status == "retracted":
        expected = {"retract"}
    elif entry.status == "contested":
        expected = {"contest"}
    elif entry.status == "archived":
        expected = {"archive"}
    else:
        expected = set()
    _require(reducer.decision_type in expected, f"{entry.status} ledger entry not authorized by {reducer.decision_type} reducer decision")


def validate_ledger_bundle(
    source_records: Iterable[dict[str, Any]],
    episode_records: Iterable[dict[str, Any]],
    evidence_spans: Iterable[dict[str, Any]],
    candidate_records: Iterable[dict[str, Any]],
    reducer_decisions: Iterable[dict[str, Any]],
    ledger_entries: Iterable[dict[str, Any]],
) -> None:
    source_ids = {record["id"] for record in source_records}
    episode_ids = {record["id"] for record in episode_records}
    spans_by_id = {record["id"]: record for record in evidence_spans}
    candidate_ids = {record["id"] for record in candidate_records}

    reducers_by_id: dict[str, MemoryReducerDecision] = {}
    for raw_reducer in reducer_decisions:
        reducer = reducer_decision_from_dict(raw_reducer)
        _require(reducer.id not in reducers_by_id, f"duplicate reducer decision id: {reducer.id}")
        reducers_by_id[reducer.id] = reducer
        for candidate_id in reducer.target_candidate_ids:
            _require(candidate_id in candidate_ids, f"dangling target_candidate_id: {candidate_id}")
        _validate_span_refs(reducer, reducer.evidence_span_ids, spans_by_id)

    ledgers_by_id: dict[str, LedgerEntryBase] = {}
    for raw_entry in ledger_entries:
        _assert_no_candidate_only_fields(raw_entry)
        entry = ledger_entry_from_dict(raw_entry)
        _require(entry.id not in ledgers_by_id, f"duplicate ledger entry id: {entry.id}")
        ledgers_by_id[entry.id] = entry
        for source_id in entry.source_record_ids:
            _require(source_id in source_ids, f"dangling source_record_id: {source_id}")
        for episode_id in entry.episode_record_ids:
            _require(episode_id in episode_ids, f"dangling episode_record_id: {episode_id}")
        for candidate_id in entry.candidate_ids:
            _require(candidate_id in candidate_ids, f"dangling candidate_id: {candidate_id}")
        _require(entry.reducer_decision_id in reducers_by_id, f"dangling reducer_decision_id: {entry.reducer_decision_id}")
        reducer = reducers_by_id[entry.reducer_decision_id]
        _validate_span_refs(entry, entry.evidence_span_ids, spans_by_id)
        _validate_reducer_authorizes_entry(entry, reducer)

    for reducer in reducers_by_id.values():
        for ledger_id in reducer.target_ledger_entry_ids:
            _require(ledger_id in ledgers_by_id, f"dangling target_ledger_entry_id: {ledger_id}")

    for entry in ledgers_by_id.values():
        for linked_id in entry.supersedes + entry.superseded_by:
            _require(linked_id in ledgers_by_id, f"dangling supersession reference: {linked_id}")
        if entry.status == "superseded":
            _require(bool(entry.superseded_by), "status superseded requires superseded_by")
            _require(entry.valid_until is not None, "status superseded requires valid_until")
        if entry.status == "active":
            _require(not entry.superseded_by, "active entries must not have superseded_by")
        for older_id in entry.supersedes:
            older = ledgers_by_id[older_id]
            _require(entry.id in older.superseded_by, "non-reciprocal supersession link")
        for newer_id in entry.superseded_by:
            newer = ledgers_by_id[newer_id]
            _require(entry.id in newer.supersedes, "non-reciprocal supersession link")
            _require(newer.valid_from is not None, "supersession successor requires valid_from")
            _require(entry.valid_until is not None, "superseded entry requires valid_until")
            _require(parse_iso8601(entry.valid_until) <= parse_iso8601(newer.valid_from), "superseded entry valid_until must be <= successor valid_from")
