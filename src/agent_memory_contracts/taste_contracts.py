"""Contracts for reducer-approved trusted taste memory."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, TypeVar, cast

from .taste_ids import make_taste_card_id, make_taste_reducer_decision_id

T = TypeVar("T")

SCHEMA_VERSION = "1.0.0"

DECISION_TYPES = {"promote", "reject", "supersede", "retract", "contest", "archive"}
TASTE_STATUSES = {"active", "stale", "superseded", "retracted", "contested", "archived"}
CONFIDENCE = {"low", "medium", "high"}
RISK_CLASSES = {"low", "medium", "high"}
CHECK_VALUES = {"pass", "fail", "unknown"}
CHECK_KEYS = {"provenance", "specificity", "example_grounding", "contrast_grounding", "privacy", "usefulness"}
DOMAINS = {"writing", "product", "investing", "architecture", "design", "communication", "operations", "personal", "other"}
SCOPES = {"global", "project", "context_specific"}
STRENGTHS = {"weak", "medium", "strong", "hard_constraint"}
TASTE_KINDS = {"preference", "aversion", "principle", "objection_pattern", "positive_example", "negative_example", "contrast_pair"}

CANDIDATE_ONLY_FIELDS = {
    "candidate_type",
    "extracted_at",
    "extracted_by",
    "natural_language_summary",
    "review",
    "strength_hint",
    "signal_kind",
    "taste_text",
    "autostart_eligible",
}

LEDGER_ONLY_FIELDS = {
    "ledger_type",
    "fact_text",
    "decision_text",
    "preference_text",
    "predicate",
    "object",
}

TRUSTED_MEMORY_REFERENCE_FIELDS = {
    "fact_id",
    "preference_id",
    "decision_id",
    "project_state_id",
    "ledger_entry_id",
    "memory_reducer_decision_id",
}


def _require(condition: object, message: str) -> None:
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


def _build_record(cls: type[T], data: dict[str, Any]) -> T:
    try:
        return cls(**data)
    except TypeError as exc:
        raise ValueError(f"invalid {cls.__name__}: {exc}") from exc


def _assert_no_boundary_fields(record: dict[str, Any]) -> None:
    forbidden_fields = CANDIDATE_ONLY_FIELDS | LEDGER_ONLY_FIELDS | TRUSTED_MEMORY_REFERENCE_FIELDS
    forbidden = sorted(forbidden_fields.intersection(record.keys()))
    _require(not forbidden, f"taste record contains forbidden fields: {forbidden}")
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        metadata_forbidden = sorted(forbidden_fields.intersection(metadata.keys()))
        _require(not metadata_forbidden, f"taste metadata contains forbidden fields: {metadata_forbidden}")


def _validate_temporal_order(name: str, start: str | None, end: str | None) -> None:
    if start is not None and end is not None:
        _require(parse_iso8601(end) >= parse_iso8601(start), f"{name} must be >= valid_from")


@dataclass(frozen=True)
class TasteReducerDecision:
    id: str
    schema_version: str
    decision_type: str
    target_taste_signal_ids: list[str]
    target_taste_card_ids: list[str]
    evidence_span_ids: list[str]
    rationale: str
    decided_by: dict[str, str | None]
    decided_at: str
    confidence: str
    risk_class: str
    checks: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TasteReducerDecision":
        _assert_no_boundary_fields(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "schema_version must be 1.0.0")
        _require(self.decision_type in DECISION_TYPES, "invalid decision_type")
        _string_list("target_taste_signal_ids", self.target_taste_signal_ids, "cand_taste_")
        _string_list("target_taste_card_ids", self.target_taste_card_ids, "taste_")
        if self.decision_type == "reject":
            _require(not self.target_taste_card_ids, "reject taste reducer decisions must not target TasteCards")
        if self.decision_type == "supersede":
            _require(len(self.target_taste_card_ids) >= 2, "supersede taste reducer decisions must target old and new TasteCards")
        if self.decision_type in {"retract", "contest", "archive"}:
            _require(bool(self.target_taste_card_ids), f"{self.decision_type} taste reducer decisions must target TasteCards")
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
        _require(set(self.checks.keys()) == CHECK_KEYS, "checks must include provenance, specificity, example_grounding, contrast_grounding, privacy, usefulness")
        for key, value in self.checks.items():
            _require(value in CHECK_VALUES, f"checks.{key} invalid")
        _object("metadata", self.metadata)
        _assert_no_boundary_fields(self.__dict__)
        _require(self.id == self.expected_id(), f"taste reducer decision id mismatch: expected {self.expected_id()}")

    def expected_id(self) -> str:
        return make_taste_reducer_decision_id(
            self.decision_type,
            self.target_taste_signal_ids,
            self.target_taste_card_ids,
            self.evidence_span_ids,
            self.rationale,
        )


@dataclass(frozen=True)
class TasteCard:
    id: str
    schema_version: str
    card_type: str
    status: str
    subject: str
    domain: str
    scope: str
    project_refs: list[str]
    principle: str
    rationale: str
    strength: str
    confidence: str
    taste_kind: str
    source_record_ids: list[str]
    episode_record_ids: list[str]
    evidence_span_ids: list[str]
    candidate_taste_signal_ids: list[str]
    positive_example_span_ids: list[str]
    negative_example_span_ids: list[str]
    contrast_pairs: list[dict[str, str]]
    objection_patterns: list[str]
    application_notes: list[str]
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

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TasteCard":
        _assert_no_boundary_fields(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "domain": self.domain,
            "scope": self.scope,
            "project_refs": sorted(self.project_refs),
            "principle": self.principle,
            "taste_kind": self.taste_kind,
            "valid_from": self.valid_from,
            "evidence_span_ids": sorted(self.evidence_span_ids),
        }

    def expected_id(self) -> str:
        return make_taste_card_id(self.evidence_span_ids, self.semantic_payload())

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "schema_version must be 1.0.0")
        _require(self.card_type == "taste_card", "card_type must be taste_card")
        _require(self.status in TASTE_STATUSES, "invalid status")
        _require(isinstance(self.subject, str) and self.subject, "subject is required")
        _require(self.domain in DOMAINS, "invalid domain")
        _require(self.scope in SCOPES, "invalid scope")
        _string_list("project_refs", self.project_refs)
        for name in ["principle", "rationale"]:
            _require(isinstance(getattr(self, name), str) and getattr(self, name), f"{name} is required")
        _require(self.strength in STRENGTHS, "invalid strength")
        _require(self.confidence in CONFIDENCE, "invalid confidence")
        _require(self.taste_kind in TASTE_KINDS, "invalid taste_kind")
        _string_list("source_record_ids", self.source_record_ids, "src_")
        _string_list("episode_record_ids", self.episode_record_ids, "ep_")
        _string_list("evidence_span_ids", self.evidence_span_ids, "span_", allow_empty=False)
        _string_list("candidate_taste_signal_ids", self.candidate_taste_signal_ids, "cand_taste_")
        _string_list("positive_example_span_ids", self.positive_example_span_ids, "span_")
        _string_list("negative_example_span_ids", self.negative_example_span_ids, "span_")
        _validate_contrast_pairs(self.contrast_pairs)
        _string_list("objection_patterns", self.objection_patterns)
        _string_list("application_notes", self.application_notes)
        _require(
            bool(self.positive_example_span_ids or self.negative_example_span_ids or self.contrast_pairs),
            "TasteCards require role-grounded span evidence",
        )
        _require(isinstance(self.reducer_decision_id, str) and self.reducer_decision_id.startswith("redtaste_"), "reducer_decision_id is required")
        for name in ["observed_at", "asserted_at", "valid_from", "valid_until", "stale_after"]:
            _require(_is_iso8601_or_none(getattr(self, name)), f"{name} must be ISO-8601 or null")
        _require(_is_iso8601(self.created_at), "created_at must be ISO-8601")
        _require(_is_iso8601(self.updated_at), "updated_at must be ISO-8601")
        _validate_temporal_order("valid_until", self.valid_from, self.valid_until)
        _validate_temporal_order("stale_after", self.valid_from, self.stale_after)
        _string_list("supersedes", self.supersedes, "taste_")
        _string_list("superseded_by", self.superseded_by, "taste_")
        if self.status == "superseded":
            _require(bool(self.superseded_by), "status superseded requires superseded_by")
            _require(self.valid_until is not None, "status superseded requires valid_until")
        if self.status == "active":
            _require(not self.superseded_by, "active TasteCards must not have superseded_by")
        _object("metadata", self.metadata)
        _assert_no_boundary_fields(self.__dict__)
        _require(self.id == self.expected_id(), f"TasteCard id mismatch: expected {self.expected_id()}")


def _validate_contrast_pairs(pairs: list[dict[str, str]]) -> None:
    _require(isinstance(pairs, list), "contrast_pairs must be a list")
    for pair in pairs:
        _object("contrast_pairs entry", pair)
        _require(set(pair.keys()) == {"preferred_span_id", "rejected_span_id", "reason"}, "contrast pair keys invalid")
        _require(isinstance(pair["preferred_span_id"], str) and pair["preferred_span_id"].startswith("span_"), "preferred_span_id invalid")
        _require(isinstance(pair["rejected_span_id"], str) and pair["rejected_span_id"].startswith("span_"), "rejected_span_id invalid")
        _require(isinstance(pair["reason"], str) and pair["reason"], "contrast pair reason is required")


def taste_reducer_decision_from_dict(data: dict[str, Any]) -> TasteReducerDecision:
    return TasteReducerDecision.from_dict(data)


def taste_card_from_dict(data: dict[str, Any]) -> TasteCard:
    return TasteCard.from_dict(data)


def _validate_span_refs(record: Any, span_ids: Iterable[str], spans_by_id: dict[str, dict[str, Any]]) -> None:
    for span_id in span_ids:
        _require(span_id in spans_by_id, f"dangling evidence_span_id: {span_id}")
        span = spans_by_id[span_id]
        if getattr(record, "source_record_ids", None):
            _require(span["source_id"] in record.source_record_ids, "span source not in source_record_ids")
        if getattr(record, "episode_record_ids", None) and span.get("episode_id") is not None:
            _require(span["episode_id"] in record.episode_record_ids, "span episode not in episode_record_ids")


def _card_grounding_span_ids(card: TasteCard) -> list[str]:
    span_ids = list(card.positive_example_span_ids) + list(card.negative_example_span_ids)
    for pair in card.contrast_pairs:
        span_ids.extend([pair["preferred_span_id"], pair["rejected_span_id"]])
    return span_ids


def _validate_taste_reducer_authorizes_card(card: TasteCard, reducer: TasteReducerDecision) -> None:
    _require(card.id in reducer.target_taste_card_ids, "TasteCard is not targeted by reducer decision")
    if card.candidate_taste_signal_ids:
        _require(set(card.candidate_taste_signal_ids).issubset(set(reducer.target_taste_signal_ids)), "TasteCard candidate_taste_signal_ids not authorized by reducer decision")
    else:
        _require(card.metadata.get("human_asserted") is True, "TasteCards without candidate_taste_signal_ids require metadata.human_asserted")
    _require(set(card.evidence_span_ids).issubset(set(reducer.evidence_span_ids)), "TasteCard evidence_span_ids not authorized by reducer decision")
    if card.supersedes:
        _require(set(card.supersedes).issubset(set(reducer.target_taste_card_ids)), "superseding TasteCard reducer does not target superseded cards")
    if card.superseded_by:
        _require(set(card.superseded_by).issubset(set(reducer.target_taste_card_ids)), "superseded TasteCard reducer does not target successor cards")

    if card.status == "active" and not card.supersedes:
        expected = {"promote"}
    elif card.status == "active" and card.supersedes:
        expected = {"supersede"}
    elif card.status == "stale":
        expected = {"promote", "supersede"}
    elif card.status == "superseded":
        expected = {"supersede"}
    elif card.status == "retracted":
        expected = {"retract"}
    elif card.status == "contested":
        expected = {"contest"}
    elif card.status == "archived":
        expected = {"archive"}
    else:
        expected = set()
    _require(reducer.decision_type in expected, f"{card.status} TasteCard not authorized by {reducer.decision_type} reducer decision")


def validate_taste_bundle(
    source_records: Iterable[dict[str, Any]],
    episode_records: Iterable[dict[str, Any]],
    evidence_spans: Iterable[dict[str, Any]],
    candidate_records: Iterable[dict[str, Any]],
    taste_reducer_decisions: Iterable[dict[str, Any]],
    taste_cards: Iterable[dict[str, Any]],
) -> None:
    source_ids = {record["id"] for record in source_records}
    episode_ids = {record["id"] for record in episode_records}
    spans_by_id = {record["id"]: record for record in evidence_spans}
    candidates_by_id = {record["id"]: record for record in candidate_records}

    reducers_by_id: dict[str, TasteReducerDecision] = {}
    for raw_reducer in taste_reducer_decisions:
        reducer = taste_reducer_decision_from_dict(raw_reducer)
        _require(reducer.id not in reducers_by_id, f"duplicate TasteReducerDecision id: {reducer.id}")
        reducers_by_id[reducer.id] = reducer
        for candidate_id in reducer.target_taste_signal_ids:
            _require(candidate_id in candidates_by_id, f"dangling target_taste_signal_id: {candidate_id}")
            _require(candidates_by_id[candidate_id].get("candidate_type") == "taste_signal", "target_taste_signal_id is not candidate_type taste_signal")
        _validate_span_refs(reducer, reducer.evidence_span_ids, spans_by_id)

    cards_by_id: dict[str, TasteCard] = {}
    for raw_card in taste_cards:
        _assert_no_boundary_fields(raw_card)
        card = taste_card_from_dict(raw_card)
        _require(card.id not in cards_by_id, f"duplicate TasteCard id: {card.id}")
        cards_by_id[card.id] = card
        for source_id in card.source_record_ids:
            _require(source_id in source_ids, f"dangling source_record_id: {source_id}")
        for episode_id in card.episode_record_ids:
            _require(episode_id in episode_ids, f"dangling episode_record_id: {episode_id}")
        for candidate_id in card.candidate_taste_signal_ids:
            _require(candidate_id in candidates_by_id, f"dangling candidate_taste_signal_id: {candidate_id}")
            _require(candidates_by_id[candidate_id].get("candidate_type") == "taste_signal", "candidate_taste_signal_id is not candidate_type taste_signal")
        _require(card.reducer_decision_id in reducers_by_id, f"dangling reducer_decision_id: {card.reducer_decision_id}")
        reducer = reducers_by_id[card.reducer_decision_id]
        _validate_span_refs(card, card.evidence_span_ids, spans_by_id)
        for grounding_span_id in _card_grounding_span_ids(card):
            _require(grounding_span_id in card.evidence_span_ids, "TasteCard grounding span not included in evidence_span_ids")
        _validate_taste_reducer_authorizes_card(card, reducer)

    for reducer in reducers_by_id.values():
        for card_id in reducer.target_taste_card_ids:
            _require(card_id in cards_by_id, f"dangling target_taste_card_id: {card_id}")

    for card in cards_by_id.values():
        for linked_id in card.supersedes + card.superseded_by:
            _require(linked_id in cards_by_id, f"dangling supersession reference: {linked_id}")
        if card.status == "superseded":
            _require(card.valid_until is not None, "status superseded requires valid_until")
        if card.status == "active":
            _require(not card.superseded_by, "active TasteCards must not have superseded_by")
        for older_id in card.supersedes:
            older = cards_by_id[older_id]
            _require(card.id in older.superseded_by, "non-reciprocal supersession link")
        for newer_id in card.superseded_by:
            newer = cards_by_id[newer_id]
            _require(card.id in newer.supersedes, "non-reciprocal supersession link")
            _require(newer.valid_from is not None, "supersession successor requires valid_from")
            _require(card.valid_until is not None, "superseded TasteCard requires valid_until")
            _require(
                parse_iso8601(cast(str, card.valid_until))
                <= parse_iso8601(cast(str, newer.valid_from)),
                "superseded TasteCard valid_until must be <= successor valid_from",
            )


def is_taste_card_active_at(card: dict[str, Any], query_time: str) -> bool:
    if card["status"] != "active":
        return False
    if card.get("superseded_by"):
        return False
    query = parse_iso8601(query_time)
    valid_from = card.get("valid_from")
    valid_until = card.get("valid_until")
    stale_after = card.get("stale_after")
    if valid_from is not None and query < parse_iso8601(valid_from):
        return False
    if valid_until is not None and query >= parse_iso8601(valid_until):
        return False
    if stale_after is not None and query >= parse_iso8601(stale_after):
        return False
    return True


def current_taste_cards(cards: Iterable[dict[str, Any]], query_time: str) -> list[dict[str, Any]]:
    return [card for card in cards if is_taste_card_active_at(card, query_time)]


def _is_taste_card_valid_as_of(card: dict[str, Any], query_time: str) -> bool:
    if card["status"] in {"retracted", "archived"}:
        return False
    query = parse_iso8601(query_time)
    valid_from = card.get("valid_from")
    valid_until = card.get("valid_until")
    if valid_from is not None and query < parse_iso8601(valid_from):
        return False
    if valid_until is not None and query >= parse_iso8601(valid_until):
        return False
    return True


def taste_cards_as_of(cards: Iterable[dict[str, Any]], query_time: str) -> list[dict[str, Any]]:
    return [card for card in cards if _is_taste_card_valid_as_of(card, query_time)]


def taste_supersession_chain(card_id: str, cards: Iterable[dict[str, Any]]) -> list[str]:
    by_id = {card["id"]: card for card in cards}
    chain = [card_id]
    current = by_id.get(card_id)
    while current and current.get("superseded_by"):
        next_id = current["superseded_by"][0]
        chain.append(next_id)
        current = by_id.get(next_id)
    return chain
