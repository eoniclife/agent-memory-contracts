"""Contracts for reducer-approved project and core state snapshots."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from .ledger_contracts import ledger_entry_from_dict
from .state_ids import make_core_state_id, make_project_state_id, make_state_reducer_decision_id
from .taste_contracts import taste_card_from_dict

SCHEMA_VERSION = "1.0.0"

DECISION_TYPES = {"promote", "reject", "supersede", "retract", "contest", "archive"}
STATE_STATUSES = {"active", "stale", "superseded", "retracted", "contested", "archived"}
PROJECT_STATUSES = {"active", "paused", "blocked", "archived"}
CONFIDENCE = {"low", "medium", "high"}
RISK_CLASSES = {"low", "medium", "high"}
CHECK_VALUES = {"pass", "fail", "unknown"}
CHECK_KEYS = {"provenance", "temporal_validity", "state_consistency", "privacy", "usefulness"}
LEDGER_PREFIXES = ("fact_", "pref_", "dec_")

FORBIDDEN_FIELDS = {
    "candidate_type",
    "ledger_type",
    "card_type",
    "context_pack_id",
    "work_item_id",
    "worker_run_id",
    "completion_artifact_id",
    "external_action_id",
    "gbrain_page_id",
    "gbrain_write_id",
    "model_call_id",
    "runtime_status",
    "fact_text",
    "preference_text",
    "decision_text",
    "taste_kind",
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


def _string_list(name: str, value: list[str], prefix: str | tuple[str, ...] | None = None, allow_empty: bool = True, max_items: int | None = None) -> None:
    _require(isinstance(value, list), f"{name} must be a list")
    if not allow_empty:
        _require(bool(value), f"{name} must not be empty")
    if max_items is not None:
        _require(len(value) <= max_items, f"{name} must contain at most {max_items} items")
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


def _assert_no_forbidden_fields(record: dict[str, Any]) -> None:
    found = sorted(FORBIDDEN_FIELDS.intersection(record.keys()))
    _require(not found, f"state record contains forbidden fields: {found}")
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        metadata_found = sorted(FORBIDDEN_FIELDS.intersection(metadata.keys()))
        _require(not metadata_found, f"state metadata contains forbidden fields: {metadata_found}")


def _validate_temporal_order(name: str, start: str | None, end: str | None) -> None:
    if start is not None and end is not None:
        _require(parse_iso8601(end) >= parse_iso8601(start), f"{name} must be >= valid_from")


@dataclass(frozen=True)
class StateReducerDecision:
    id: str
    schema_version: str
    decision_type: str
    target_project_state_ids: list[str]
    target_core_state_ids: list[str]
    source_record_ids: list[str]
    episode_record_ids: list[str]
    evidence_span_ids: list[str]
    ledger_entry_ids: list[str]
    taste_card_ids: list[str]
    rationale: str
    decided_by: dict[str, str | None]
    decided_at: str
    confidence: str
    risk_class: str
    checks: dict[str, str]
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StateReducerDecision":
        _assert_no_forbidden_fields(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "schema_version must be 1.0.0")
        _require(self.decision_type in DECISION_TYPES, "invalid decision_type")
        _string_list("target_project_state_ids", self.target_project_state_ids, "projstate_")
        _string_list("target_core_state_ids", self.target_core_state_ids, "corestate_")
        target_count = len(self.target_project_state_ids) + len(self.target_core_state_ids)
        if self.decision_type == "reject":
            _require(target_count == 0, "reject StateReducerDecisions must not target snapshots")
        if self.decision_type == "supersede":
            _require(
                (len(self.target_project_state_ids) >= 2 and not self.target_core_state_ids)
                or (len(self.target_core_state_ids) >= 2 and not self.target_project_state_ids),
                "supersede StateReducerDecisions must target old and new snapshots of one state family",
            )
        if self.decision_type in {"retract", "contest", "archive"}:
            _require(target_count > 0, f"{self.decision_type} StateReducerDecisions must target snapshots")
        _string_list("source_record_ids", self.source_record_ids, "src_")
        _string_list("episode_record_ids", self.episode_record_ids, "ep_")
        _string_list("evidence_span_ids", self.evidence_span_ids, "span_", allow_empty=False)
        _string_list("ledger_entry_ids", self.ledger_entry_ids, LEDGER_PREFIXES)
        _string_list("taste_card_ids", self.taste_card_ids, "taste_")
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
        _require(set(self.checks.keys()) == CHECK_KEYS, "checks must include provenance, temporal_validity, state_consistency, privacy, usefulness")
        for key, value in self.checks.items():
            _require(value in CHECK_VALUES, f"checks.{key} invalid")
        _object("metadata", self.metadata)
        _assert_no_forbidden_fields(self.__dict__)
        _require(self.id == self.expected_id(), f"state reducer decision id mismatch: expected {self.expected_id()}")

    def expected_id(self) -> str:
        return make_state_reducer_decision_id(
            self.decision_type,
            self.target_project_state_ids,
            self.target_core_state_ids,
            self.evidence_span_ids,
            self.ledger_entry_ids,
            self.taste_card_ids,
            self.rationale,
        )


@dataclass(frozen=True)
class StateSnapshotBase:
    id: str
    schema_version: str
    state_type: str
    status: str
    as_of: str
    summary: str
    active_fact_ids: list[str]
    active_preference_ids: list[str]
    active_decision_ids: list[str]
    active_taste_card_ids: list[str]
    source_record_ids: list[str]
    episode_record_ids: list[str]
    evidence_span_ids: list[str]
    reducer_decision_id: str
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
        _require(self.status in STATE_STATUSES, "invalid status")
        _require(_is_iso8601(self.as_of), "as_of must be ISO-8601")
        _require(isinstance(self.summary, str) and self.summary, "summary is required")
        _string_list("active_fact_ids", self.active_fact_ids, "fact_")
        _string_list("active_preference_ids", self.active_preference_ids, "pref_")
        _string_list("active_decision_ids", self.active_decision_ids, "dec_")
        _string_list("active_taste_card_ids", self.active_taste_card_ids, "taste_")
        _string_list("source_record_ids", self.source_record_ids, "src_")
        _string_list("episode_record_ids", self.episode_record_ids, "ep_")
        _string_list("evidence_span_ids", self.evidence_span_ids, "span_", allow_empty=False)
        _require(isinstance(self.reducer_decision_id, str) and self.reducer_decision_id.startswith("redstate_"), "reducer_decision_id is required")
        for name in ["valid_from", "valid_until", "stale_after"]:
            _require(_is_iso8601_or_none(getattr(self, name)), f"{name} must be ISO-8601 or null")
        _require(_is_iso8601(self.created_at), "created_at must be ISO-8601")
        _require(_is_iso8601(self.updated_at), "updated_at must be ISO-8601")
        _validate_temporal_order("valid_until", self.valid_from, self.valid_until)
        _validate_temporal_order("stale_after", self.valid_from, self.stale_after)
        if self.status == "superseded":
            _require(bool(self.superseded_by), "status superseded requires superseded_by")
            _require(self.valid_until is not None, "status superseded requires valid_until")
        if self.status == "active":
            _require(not self.superseded_by, "active snapshots must not have superseded_by")
        _object("metadata", self.metadata)
        _assert_no_forbidden_fields(self.__dict__)
        _require(self.id == self.expected_id(), f"state snapshot id mismatch: expected {self.expected_id()}")

    def expected_id(self) -> str:
        raise NotImplementedError


@dataclass(frozen=True)
class ProjectStateSnapshot(StateSnapshotBase):
    project_id: str = ""
    project_name: str = ""
    project_status: str = ""
    current_objective: str = ""
    current_strategy: str = ""
    current_priorities: list[str] = field(default_factory=list)
    active_blockers: list[str] = field(default_factory=list)
    open_questions: list[str] = field(default_factory=list)
    next_actions: list[str] = field(default_factory=list)
    candidate_task_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProjectStateSnapshot":
        _assert_no_forbidden_fields(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "project_status": self.project_status,
            "as_of": self.as_of,
            "current_objective": self.current_objective,
            "current_strategy": self.current_strategy,
            "current_priorities": self.current_priorities,
            "active_blockers": self.active_blockers,
            "open_questions": self.open_questions,
            "active_fact_ids": sorted(self.active_fact_ids),
            "active_preference_ids": sorted(self.active_preference_ids),
            "active_decision_ids": sorted(self.active_decision_ids),
            "active_taste_card_ids": sorted(self.active_taste_card_ids),
            "evidence_span_ids": sorted(self.evidence_span_ids),
        }

    def expected_id(self) -> str:
        return make_project_state_id(self.project_id, self.as_of, self.evidence_span_ids, self.semantic_payload())

    def validate(self) -> None:
        self.validate_base()
        _require(self.state_type == "project_state", "state_type must be project_state")
        for name in ["project_id", "project_name", "current_objective", "current_strategy"]:
            _require(isinstance(getattr(self, name), str) and getattr(self, name), f"{name} is required")
        _require(self.project_status in PROJECT_STATUSES, "invalid project_status")
        _string_list("current_priorities", self.current_priorities)
        _string_list("active_blockers", self.active_blockers)
        _string_list("open_questions", self.open_questions)
        _string_list("next_actions", self.next_actions)
        _string_list("candidate_task_ids", self.candidate_task_ids, "cand_task_")
        _string_list("supersedes", self.supersedes, "projstate_")
        _string_list("superseded_by", self.superseded_by, "projstate_")


@dataclass(frozen=True)
class CoreStateSnapshot(StateSnapshotBase):
    subject: str = ""
    current_priorities: list[str] = field(default_factory=list)
    active_project_state_ids: list[str] = field(default_factory=list)
    standing_principles: list[str] = field(default_factory=list)
    operating_style: list[str] = field(default_factory=list)
    approval_boundaries: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CoreStateSnapshot":
        _assert_no_forbidden_fields(data)
        record = _build_record(cls, data)
        record.validate()
        return record

    def semantic_payload(self) -> dict[str, Any]:
        return {
            "subject": self.subject,
            "as_of": self.as_of,
            "current_priorities": self.current_priorities,
            "active_project_state_ids": sorted(self.active_project_state_ids),
            "standing_principles": self.standing_principles,
            "operating_style": self.operating_style,
            "approval_boundaries": self.approval_boundaries,
            "constraints": self.constraints,
            "active_fact_ids": sorted(self.active_fact_ids),
            "active_preference_ids": sorted(self.active_preference_ids),
            "active_decision_ids": sorted(self.active_decision_ids),
            "active_taste_card_ids": sorted(self.active_taste_card_ids),
            "evidence_span_ids": sorted(self.evidence_span_ids),
        }

    def expected_id(self) -> str:
        return make_core_state_id(self.subject, self.as_of, self.evidence_span_ids, self.semantic_payload())

    def validate(self) -> None:
        self.validate_base()
        _require(self.state_type == "core_state", "state_type must be core_state")
        _require(self.subject == "Aditya", "subject must be Aditya")
        _string_list("current_priorities", self.current_priorities, max_items=7)
        _string_list("active_project_state_ids", self.active_project_state_ids, "projstate_", max_items=10)
        _string_list("standing_principles", self.standing_principles, max_items=10)
        _string_list("operating_style", self.operating_style, max_items=10)
        _string_list("approval_boundaries", self.approval_boundaries, max_items=10)
        _string_list("constraints", self.constraints, max_items=10)
        _string_list("supersedes", self.supersedes, "corestate_")
        _string_list("superseded_by", self.superseded_by, "corestate_")


def state_reducer_decision_from_dict(data: dict[str, Any]) -> StateReducerDecision:
    return StateReducerDecision.from_dict(data)


def project_state_from_dict(data: dict[str, Any]) -> ProjectStateSnapshot:
    return ProjectStateSnapshot.from_dict(data)


def core_state_from_dict(data: dict[str, Any]) -> CoreStateSnapshot:
    return CoreStateSnapshot.from_dict(data)


def _validate_span_refs(record: Any, span_ids: Iterable[str], spans_by_id: dict[str, dict[str, Any]]) -> None:
    for span_id in span_ids:
        _require(span_id in spans_by_id, f"dangling evidence_span_id: {span_id}")
        span = spans_by_id[span_id]
        if getattr(record, "source_record_ids", None):
            _require(span["source_id"] in record.source_record_ids, "span source not in source_record_ids")
        if getattr(record, "episode_record_ids", None) and span.get("episode_id") is not None:
            _require(span["episode_id"] in record.episode_record_ids, "span episode not in episode_record_ids")


def _ledger_refs(state: StateSnapshotBase) -> list[str]:
    return list(state.active_fact_ids) + list(state.active_preference_ids) + list(state.active_decision_ids)


def _state_targets(reducer: StateReducerDecision, state: StateSnapshotBase) -> list[str]:
    return reducer.target_project_state_ids if state.state_type == "project_state" else reducer.target_core_state_ids


def _validate_reducer_authorizes_state(state: StateSnapshotBase, reducer: StateReducerDecision) -> None:
    targets = _state_targets(reducer, state)
    _require(state.id in targets, "state snapshot is not targeted by reducer decision")
    _require(set(state.evidence_span_ids).issubset(set(reducer.evidence_span_ids)), "state evidence_span_ids not authorized by reducer decision")
    ledger_refs = _ledger_refs(state)
    if ledger_refs:
        _require(set(ledger_refs).issubset(set(reducer.ledger_entry_ids)), "state ledger refs not authorized by reducer decision")
    if state.active_taste_card_ids:
        _require(set(state.active_taste_card_ids).issubset(set(reducer.taste_card_ids)), "state TasteCard refs not authorized by reducer decision")
    if state.supersedes:
        _require(set(state.supersedes).issubset(set(targets)), "superseding state reducer does not target superseded snapshots")
    if state.superseded_by:
        _require(set(state.superseded_by).issubset(set(targets)), "superseded state reducer does not target successor snapshots")

    if state.status == "active" and not state.supersedes:
        expected = {"promote"}
    elif state.status == "active" and state.supersedes:
        expected = {"supersede"}
    elif state.status == "stale":
        expected = {"promote", "supersede"}
    elif state.status == "superseded":
        expected = {"supersede"}
    elif state.status == "retracted":
        expected = {"retract"}
    elif state.status == "contested":
        expected = {"contest"}
    elif state.status == "archived":
        expected = {"archive"}
    else:
        expected = set()
    _require(reducer.decision_type in expected, f"{state.status} state not authorized by {reducer.decision_type} reducer decision")


def _field(record: Any, name: str) -> Any:
    if isinstance(record, dict):
        return record.get(name)
    return getattr(record, name)


def _is_active_record_at(record: Any, query_time: str) -> bool:
    if _field(record, "status") != "active":
        return False
    if _field(record, "superseded_by"):
        return False
    query = parse_iso8601(query_time)
    valid_from = _field(record, "valid_from")
    if valid_from is not None and query < parse_iso8601(valid_from):
        return False
    valid_until = _field(record, "valid_until")
    if valid_until is not None and query >= parse_iso8601(valid_until):
        return False
    stale_after = _field(record, "stale_after")
    if stale_after is not None and query >= parse_iso8601(stale_after):
        return False
    return True


def _validate_active_ledger_ref_at(ledger_id: str, ledgers_by_id: dict[str, Any], expected_type: str, query_time: str) -> None:
    _require(ledger_id in ledgers_by_id, f"dangling ledger_entry_id: {ledger_id}")
    entry = ledgers_by_id[ledger_id]
    _require(entry.ledger_type == expected_type, f"{ledger_id} is not {expected_type} ledger entry")
    _require(_is_active_record_at(entry, query_time), f"{ledger_id} is not active at {query_time}")


def _validate_active_taste_ref_at(taste_id: str, taste_by_id: dict[str, Any], query_time: str) -> None:
    _require(taste_id in taste_by_id, f"dangling taste_card_id: {taste_id}")
    card = taste_by_id[taste_id]
    _require(_is_active_record_at(card, query_time), f"{taste_id} is not active at {query_time}")


def _validate_active_project_ref_at(project_id: str, projects_by_id: dict[str, "ProjectStateSnapshot"], query_time: str) -> None:
    _require(project_id in projects_by_id, f"dangling active_project_state_id: {project_id}")
    project = projects_by_id[project_id]
    _require(_is_active_record_at(project, query_time), f"{project_id} is not active at {query_time}")


def _active_interval(state: StateSnapshotBase) -> tuple[datetime | None, datetime | None] | None:
    if state.status != "active" or state.superseded_by:
        return None
    start = parse_iso8601(state.valid_from) if state.valid_from is not None else None
    ends = [
        parse_iso8601(value)
        for value in [state.valid_until, state.stale_after]
        if value is not None
    ]
    end = min(ends) if ends else None
    return start, end


def _intervals_overlap(
    left: tuple[datetime | None, datetime | None],
    right: tuple[datetime | None, datetime | None],
) -> bool:
    left_start, left_end = left
    right_start, right_end = right
    left_before_right_end = right_end is None or left_start is None or left_start < right_end
    right_before_left_end = left_end is None or right_start is None or right_start < left_end
    return left_before_right_end and right_before_left_end


def _validate_current_project_uniqueness(projects_by_id: dict[str, "ProjectStateSnapshot"]) -> None:
    projects = list(projects_by_id.values())
    for index, left in enumerate(projects):
        left_interval = _active_interval(left)
        if left_interval is None:
            continue
        for right in projects[index + 1 :]:
            if left.project_id != right.project_id:
                continue
            right_interval = _active_interval(right)
            if right_interval is None:
                continue
            _require(
                not _intervals_overlap(left_interval, right_interval),
                f"overlapping current ProjectStateSnapshots for project_id {left.project_id}",
            )


def _validate_current_core_uniqueness(cores_by_id: dict[str, "CoreStateSnapshot"]) -> None:
    cores = list(cores_by_id.values())
    for index, left in enumerate(cores):
        left_interval = _active_interval(left)
        if left_interval is None:
            continue
        for right in cores[index + 1 :]:
            if left.subject != right.subject:
                continue
            right_interval = _active_interval(right)
            if right_interval is None:
                continue
            _require(
                not _intervals_overlap(left_interval, right_interval),
                f"overlapping current CoreStateSnapshots for subject {left.subject}",
            )


def _validate_supersession_family(states_by_id: dict[str, StateSnapshotBase]) -> None:
    for state in states_by_id.values():
        for linked_id in state.supersedes + state.superseded_by:
            _require(linked_id in states_by_id, f"dangling supersession reference: {linked_id}")
        if state.status == "superseded":
            _require(state.valid_until is not None, "status superseded requires valid_until")
        if state.status == "active":
            _require(not state.superseded_by, "active snapshots must not have superseded_by")
        for older_id in state.supersedes:
            older = states_by_id[older_id]
            _require(state.id in older.superseded_by, "non-reciprocal supersession link")
        for newer_id in state.superseded_by:
            newer = states_by_id[newer_id]
            _require(state.id in newer.supersedes, "non-reciprocal supersession link")
            _require(newer.valid_from is not None, "supersession successor requires valid_from")
            _require(state.valid_until is not None, "superseded state requires valid_until")
            _require(parse_iso8601(state.valid_until) <= parse_iso8601(newer.valid_from), "superseded state valid_until must be <= successor valid_from")


def validate_state_bundle(
    source_records: Iterable[dict[str, Any]],
    episode_records: Iterable[dict[str, Any]],
    evidence_spans: Iterable[dict[str, Any]],
    candidate_records: Iterable[dict[str, Any]],
    ledger_entries: Iterable[dict[str, Any]],
    taste_cards: Iterable[dict[str, Any]],
    state_reducer_decisions: Iterable[dict[str, Any]],
    project_states: Iterable[dict[str, Any]],
    core_states: Iterable[dict[str, Any]],
) -> None:
    source_ids = {record["id"] for record in source_records}
    episode_ids = {record["id"] for record in episode_records}
    spans_by_id = {record["id"]: record for record in evidence_spans}
    candidates_by_id = {record["id"]: record for record in candidate_records}

    ledgers_by_id = {}
    for raw_entry in ledger_entries:
        entry = ledger_entry_from_dict(raw_entry)
        _require(entry.id not in ledgers_by_id, f"duplicate ledger entry id: {entry.id}")
        ledgers_by_id[entry.id] = entry

    taste_by_id = {}
    for raw_card in taste_cards:
        card = taste_card_from_dict(raw_card)
        _require(card.id not in taste_by_id, f"duplicate TasteCard id: {card.id}")
        taste_by_id[card.id] = card

    reducers_by_id: dict[str, StateReducerDecision] = {}
    for raw_reducer in state_reducer_decisions:
        reducer = state_reducer_decision_from_dict(raw_reducer)
        _require(reducer.id not in reducers_by_id, f"duplicate StateReducerDecision id: {reducer.id}")
        reducers_by_id[reducer.id] = reducer
        for source_id in reducer.source_record_ids:
            _require(source_id in source_ids, f"dangling source_record_id: {source_id}")
        for episode_id in reducer.episode_record_ids:
            _require(episode_id in episode_ids, f"dangling episode_record_id: {episode_id}")
        _validate_span_refs(reducer, reducer.evidence_span_ids, spans_by_id)
        for ledger_id in reducer.ledger_entry_ids:
            _require(ledger_id in ledgers_by_id, f"dangling ledger_entry_id: {ledger_id}")
        for taste_id in reducer.taste_card_ids:
            _require(taste_id in taste_by_id, f"dangling taste_card_id: {taste_id}")

    projects_by_id: dict[str, ProjectStateSnapshot] = {}
    for raw_project in project_states:
        _assert_no_forbidden_fields(raw_project)
        project = project_state_from_dict(raw_project)
        _require(project.id not in projects_by_id, f"duplicate ProjectStateSnapshot id: {project.id}")
        projects_by_id[project.id] = project

    cores_by_id: dict[str, CoreStateSnapshot] = {}
    for raw_core in core_states:
        _assert_no_forbidden_fields(raw_core)
        core = core_state_from_dict(raw_core)
        _require(core.id not in cores_by_id, f"duplicate CoreStateSnapshot id: {core.id}")
        cores_by_id[core.id] = core

    for project in projects_by_id.values():
        _validate_common_state_refs(project, source_ids, episode_ids, spans_by_id, reducers_by_id, ledgers_by_id, taste_by_id)
        for candidate_id in project.candidate_task_ids:
            _require(candidate_id in candidates_by_id, f"dangling candidate_task_id: {candidate_id}")
            _require(candidates_by_id[candidate_id].get("candidate_type") == "task", "candidate_task_id is not candidate_type task")

    for core in cores_by_id.values():
        _validate_common_state_refs(core, source_ids, episode_ids, spans_by_id, reducers_by_id, ledgers_by_id, taste_by_id)
        for project_id in core.active_project_state_ids:
            _validate_active_project_ref_at(project_id, projects_by_id, core.as_of)

    for reducer in reducers_by_id.values():
        for project_id in reducer.target_project_state_ids:
            _require(project_id in projects_by_id, f"dangling target_project_state_id: {project_id}")
            _require(
                projects_by_id[project_id].reducer_decision_id == reducer.id,
                "StateReducerDecision target_project_state_ids must match targeted snapshot reducer_decision_id",
            )
        for core_id in reducer.target_core_state_ids:
            _require(core_id in cores_by_id, f"dangling target_core_state_id: {core_id}")
            _require(
                cores_by_id[core_id].reducer_decision_id == reducer.id,
                "StateReducerDecision target_core_state_ids must match targeted snapshot reducer_decision_id",
            )

    _validate_supersession_family(projects_by_id)
    _validate_supersession_family(cores_by_id)
    _validate_current_project_uniqueness(projects_by_id)
    _validate_current_core_uniqueness(cores_by_id)


def _validate_common_state_refs(
    state: StateSnapshotBase,
    source_ids: set[str],
    episode_ids: set[str],
    spans_by_id: dict[str, dict[str, Any]],
    reducers_by_id: dict[str, StateReducerDecision],
    ledgers_by_id: dict[str, Any],
    taste_by_id: dict[str, Any],
) -> None:
    for source_id in state.source_record_ids:
        _require(source_id in source_ids, f"dangling source_record_id: {source_id}")
    for episode_id in state.episode_record_ids:
        _require(episode_id in episode_ids, f"dangling episode_record_id: {episode_id}")
    _validate_span_refs(state, state.evidence_span_ids, spans_by_id)
    _require(state.reducer_decision_id in reducers_by_id, f"dangling reducer_decision_id: {state.reducer_decision_id}")
    reducer = reducers_by_id[state.reducer_decision_id]
    _validate_reducer_authorizes_state(state, reducer)
    for fact_id in state.active_fact_ids:
        _validate_active_ledger_ref_at(fact_id, ledgers_by_id, "fact", state.as_of)
    for pref_id in state.active_preference_ids:
        _validate_active_ledger_ref_at(pref_id, ledgers_by_id, "preference", state.as_of)
    for dec_id in state.active_decision_ids:
        _validate_active_ledger_ref_at(dec_id, ledgers_by_id, "decision", state.as_of)
    for taste_id in state.active_taste_card_ids:
        _validate_active_taste_ref_at(taste_id, taste_by_id, state.as_of)
