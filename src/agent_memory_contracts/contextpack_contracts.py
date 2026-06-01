"""Contracts for task-ready ContextPacks and audit receipts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .contextpack_ids import (
    canonical_payload,
    make_context_pack_build_receipt_id,
    make_context_pack_id,
    make_context_pack_validation_report_id,
)
from .evidence_ids import sha256_hex

SCHEMA_VERSION = "1.0.0"

TASK_TYPES = {"architecture_review", "writing", "research", "decision", "implementation", "review", "other"}
RISK_CLASSES = {"low", "medium", "high"}
SENSITIVITY = {"public", "internal", "private", "sensitive", "highly_sensitive"}
AUTHORITY_SOURCES = {"synthetic_fixture", "manual", "compiler", "other"}
ALLOWED_ACTIONS = {"read", "draft", "analyze", "propose", "code", "test"}
FORBIDDEN_ACTIONS = {"send", "publish", "deploy", "delete", "mutate_trusted_memory", "write_gbrain", "run_workers"}
REQUIRED_FORBIDDEN_ACTIONS = {"mutate_trusted_memory", "write_gbrain", "run_workers"}
CONFLICT_KINDS = {"conflict", "uncertainty", "missing_info", "contested_memory", "stale_memory"}
BUILDER_MODES = {"synthetic_fixture", "manual", "compiler"}
REPORT_STATUSES = {"pass", "fail"}
CHECK_VALUES = {"pass", "fail", "not_applicable"}
VALIDATION_CHECK_KEYS = {
    "evidence_present",
    "primary_evidence_present",
    "current_memory_active",
    "stale_memory_separated",
    "taste_present_for_taste_sensitive_task",
    "state_present",
    "permission_policy_present",
    "forbidden_actions_enforced",
    "no_worker_runtime_fields",
    "no_gbrain_write_authority",
    "receipt_consistent",
}

CONTEXT_FORBIDDEN_FIELDS = {
    "work_item_id",
    "worker_run_id",
    "completion_artifact_id",
    "runtime_status",
    "external_action_id",
    "gbrain_write_id",
    "gbrain_page_id",
    "model_call_id",
    "worker_id",
    "queue_id",
    "lease_id",
    "synthesized_page_id",
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


def _assert_no_forbidden_fields(record: dict[str, Any], label: str = "context pack") -> None:
    found = sorted(CONTEXT_FORBIDDEN_FIELDS.intersection(record.keys()))
    _require(not found, f"{label} contains forbidden fields: {found}")
    metadata = record.get("metadata")
    if isinstance(metadata, dict):
        metadata_found = sorted(CONTEXT_FORBIDDEN_FIELDS.intersection(metadata.keys()))
        _require(not metadata_found, f"{label} metadata contains forbidden fields: {metadata_found}")


def _validate_conflicts(value: list[dict[str, Any]]) -> None:
    _require(isinstance(value, list), "conflicts_and_uncertainties must be a list")
    for item in value:
        _object("conflicts_and_uncertainties entries", item)
        _require(set(item.keys()) == {"kind", "summary", "related_ids"}, "conflict entries must have kind, summary, related_ids")
        _require(item["kind"] in CONFLICT_KINDS, "invalid conflict kind")
        _require(isinstance(item["summary"], str) and item["summary"], "conflict summary is required")
        _string_list("conflict.related_ids", item["related_ids"])


def _validate_excluded(value: list[dict[str, Any]], name: str = "excluded") -> None:
    _require(isinstance(value, list), f"{name} must be a list")
    for item in value:
        _object(f"{name} entries", item)
        _require(set(item.keys()) == {"id", "reason"}, f"{name} entries must have id and reason")
        _require(isinstance(item["id"], str) and item["id"], f"{name}.id is required")
        _require(isinstance(item["reason"], str) and item["reason"], f"{name}.reason is required")


def _validate_error_list(value: list[dict[str, Any]]) -> None:
    _require(isinstance(value, list), "errors must be a list")
    for item in value:
        _object("errors entries", item)
        _require(set(item.keys()) == {"code", "message", "related_ids"}, "error entries must have code, message, related_ids")
        _require(isinstance(item["code"], str) and item["code"], "error code is required")
        _require(isinstance(item["message"], str) and item["message"], "error message is required")
        _string_list("error.related_ids", item["related_ids"])


@dataclass(frozen=True)
class ContextPack:
    id: str
    schema_version: str
    pack_type: str
    task: dict[str, Any]
    authority: dict[str, Any]
    state: dict[str, Any]
    trusted_memory: dict[str, list[str]]
    candidate_context: dict[str, list[str]]
    evidence: dict[str, list[str]]
    stale_or_superseded: dict[str, list[str]]
    conflicts_and_uncertainties: list[dict[str, Any]]
    constraints: dict[str, Any]
    retrieval_trace: dict[str, Any]
    pack_hash_sha256: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextPack":
        _assert_no_forbidden_fields(data, "ContextPack")
        record = _build_record(cls, data)
        record.validate()
        return record

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "schema_version must be 1.0.0")
        _require(self.pack_type == "context_pack", "pack_type must be context_pack")
        self._validate_task()
        self._validate_authority()
        self._validate_state()
        self._validate_trusted_memory()
        self._validate_candidate_context()
        self._validate_evidence()
        self._validate_stale_or_superseded()
        _validate_conflicts(self.conflicts_and_uncertainties)
        self._validate_constraints()
        self._validate_retrieval_trace()
        _require(isinstance(self.pack_hash_sha256, str) and len(self.pack_hash_sha256) == 64, "pack_hash_sha256 must be sha256 hex")
        _object("metadata", self.metadata)
        _assert_no_forbidden_fields(self.__dict__, "ContextPack")
        _require(self.evidence["primary_evidence_span_ids"], "primary_evidence_span_ids must not be empty")
        _require(
            set(self.evidence["primary_evidence_span_ids"]).issubset(set(self.evidence["evidence_span_ids"])),
            "primary_evidence_span_ids must be subset of evidence_span_ids",
        )
        _require(self.pack_hash_sha256 == self.expected_pack_hash(), "pack_hash_sha256 mismatch")
        _require(self.id == self.expected_id(), f"ContextPack id mismatch: expected {self.expected_id()}")

    def _validate_task(self) -> None:
        _object("task", self.task)
        _require(
            set(self.task.keys()) == {"task_id", "task_title", "task_type", "task_summary", "project_id", "risk_class", "sensitivity"},
            "task keys invalid",
        )
        for key in ["task_id", "task_title", "task_summary"]:
            _require(isinstance(self.task[key], str) and self.task[key], f"task.{key} is required")
        _require(self.task["task_type"] in TASK_TYPES, "invalid task_type")
        _require(self.task["project_id"] is None or isinstance(self.task["project_id"], str), "task.project_id invalid")
        _require(self.task["risk_class"] in RISK_CLASSES, "invalid task risk_class")
        _require(self.task["sensitivity"] in SENSITIVITY, "invalid task sensitivity")

    def _validate_authority(self) -> None:
        _object("authority", self.authority)
        _require(
            set(self.authority.keys()) == {"created_by", "created_at", "source", "schema_pack_version", "kernel_commit"},
            "authority keys invalid",
        )
        _require(isinstance(self.authority["created_by"], str) and self.authority["created_by"], "authority.created_by is required")
        _require(_is_iso8601(self.authority["created_at"]), "authority.created_at must be ISO-8601")
        _require(self.authority["source"] in AUTHORITY_SOURCES, "invalid authority.source")
        _require(isinstance(self.authority["schema_pack_version"], str) and self.authority["schema_pack_version"], "schema_pack_version required")
        _require(self.authority["kernel_commit"] is None or isinstance(self.authority["kernel_commit"], str), "kernel_commit invalid")

    def _validate_state(self) -> None:
        _object("state", self.state)
        _require(set(self.state.keys()) == {"core_state_id", "project_state_ids"}, "state keys invalid")
        core_state_id = self.state["core_state_id"]
        _require(core_state_id is None or (isinstance(core_state_id, str) and core_state_id.startswith("corestate_")), "core_state_id invalid")
        _string_list("state.project_state_ids", self.state["project_state_ids"], "projstate_")
        _require(core_state_id is not None or bool(self.state["project_state_ids"]), "ContextPack must include at least one current state reference")

    def _validate_trusted_memory(self) -> None:
        _object("trusted_memory", self.trusted_memory)
        expected = {"fact_ids", "preference_ids", "decision_ids", "taste_card_ids"}
        _require(set(self.trusted_memory.keys()) == expected, "trusted_memory keys invalid")
        _string_list("trusted_memory.fact_ids", self.trusted_memory["fact_ids"], "fact_")
        _string_list("trusted_memory.preference_ids", self.trusted_memory["preference_ids"], "pref_")
        _string_list("trusted_memory.decision_ids", self.trusted_memory["decision_ids"], "dec_")
        _string_list("trusted_memory.taste_card_ids", self.trusted_memory["taste_card_ids"], "taste_")

    def _validate_candidate_context(self) -> None:
        _object("candidate_context", self.candidate_context)
        expected = {"candidate_task_ids", "candidate_claim_ids", "candidate_preference_ids", "candidate_decision_ids", "candidate_taste_signal_ids"}
        _require(set(self.candidate_context.keys()) == expected, "candidate_context keys invalid")
        _string_list("candidate_context.candidate_task_ids", self.candidate_context["candidate_task_ids"], "cand_task_")
        _string_list("candidate_context.candidate_claim_ids", self.candidate_context["candidate_claim_ids"], "cand_claim_")
        _string_list("candidate_context.candidate_preference_ids", self.candidate_context["candidate_preference_ids"], "cand_pref_")
        _string_list("candidate_context.candidate_decision_ids", self.candidate_context["candidate_decision_ids"], "cand_decision_")
        _string_list("candidate_context.candidate_taste_signal_ids", self.candidate_context["candidate_taste_signal_ids"], "cand_taste_")

    def _validate_evidence(self) -> None:
        _object("evidence", self.evidence)
        expected = {"source_record_ids", "episode_record_ids", "evidence_span_ids", "primary_evidence_span_ids"}
        _require(set(self.evidence.keys()) == expected, "evidence keys invalid")
        _string_list("evidence.source_record_ids", self.evidence["source_record_ids"], "src_")
        _string_list("evidence.episode_record_ids", self.evidence["episode_record_ids"], "ep_")
        _string_list("evidence.evidence_span_ids", self.evidence["evidence_span_ids"], "span_", allow_empty=False)
        _string_list("evidence.primary_evidence_span_ids", self.evidence["primary_evidence_span_ids"], "span_", allow_empty=False)

    def _validate_stale_or_superseded(self) -> None:
        _object("stale_or_superseded", self.stale_or_superseded)
        expected = {"fact_ids", "preference_ids", "decision_ids", "taste_card_ids", "project_state_ids", "core_state_ids"}
        _require(set(self.stale_or_superseded.keys()) == expected, "stale_or_superseded keys invalid")
        _string_list("stale_or_superseded.fact_ids", self.stale_or_superseded["fact_ids"], "fact_")
        _string_list("stale_or_superseded.preference_ids", self.stale_or_superseded["preference_ids"], "pref_")
        _string_list("stale_or_superseded.decision_ids", self.stale_or_superseded["decision_ids"], "dec_")
        _string_list("stale_or_superseded.taste_card_ids", self.stale_or_superseded["taste_card_ids"], "taste_")
        _string_list("stale_or_superseded.project_state_ids", self.stale_or_superseded["project_state_ids"], "projstate_")
        _string_list("stale_or_superseded.core_state_ids", self.stale_or_superseded["core_state_ids"], "corestate_")

    def _validate_constraints(self) -> None:
        _object("constraints", self.constraints)
        expected = {"permission_policy_id", "allowed_actions", "forbidden_actions", "privacy_notes", "token_budget"}
        _require(set(self.constraints.keys()) == expected, "constraints keys invalid")
        _require(isinstance(self.constraints["permission_policy_id"], str) and self.constraints["permission_policy_id"], "permission_policy_id required")
        _string_list("constraints.allowed_actions", self.constraints["allowed_actions"])
        _string_list("constraints.forbidden_actions", self.constraints["forbidden_actions"])
        _string_list("constraints.privacy_notes", self.constraints["privacy_notes"])
        _require(set(self.constraints["allowed_actions"]).issubset(ALLOWED_ACTIONS), "allowed_actions contains unsupported action")
        _require(not set(self.constraints["allowed_actions"]).intersection({"send", "publish", "deploy", "delete"}), "allowed_actions must not include external actions")
        _require(set(self.constraints["forbidden_actions"]).issubset(FORBIDDEN_ACTIONS), "forbidden_actions contains unsupported action")
        _require(REQUIRED_FORBIDDEN_ACTIONS.issubset(set(self.constraints["forbidden_actions"])), "forbidden_actions missing required memory/runtime guards")
        _require(isinstance(self.constraints["token_budget"], int) and self.constraints["token_budget"] >= 0, "token_budget must be non-negative integer")

    def _validate_retrieval_trace(self) -> None:
        _object("retrieval_trace", self.retrieval_trace)
        expected = {"queries", "included_ids", "excluded", "build_inputs_hash"}
        _require(set(self.retrieval_trace.keys()) == expected, "retrieval_trace keys invalid")
        _string_list("retrieval_trace.queries", self.retrieval_trace["queries"])
        _string_list("retrieval_trace.included_ids", self.retrieval_trace["included_ids"])
        _validate_excluded(self.retrieval_trace["excluded"], "retrieval_trace.excluded")
        _require(isinstance(self.retrieval_trace["build_inputs_hash"], str) and len(self.retrieval_trace["build_inputs_hash"]) == 64, "build_inputs_hash must be sha256 hex")

    def identity_payload(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "state": self.state,
            "trusted_memory": self.trusted_memory,
            "candidate_context": self.candidate_context,
            "evidence": self.evidence,
            "stale_or_superseded": self.stale_or_superseded,
            "conflicts_and_uncertainties": self.conflicts_and_uncertainties,
            "constraints": self.constraints,
            "retrieval_trace": {"build_inputs_hash": self.retrieval_trace["build_inputs_hash"]},
        }

    def expected_pack_hash(self) -> str:
        return sha256_hex(canonical_payload(self.identity_payload()))

    def expected_id(self) -> str:
        return make_context_pack_id(self.task["task_id"], self.evidence["evidence_span_ids"], self.identity_payload())


@dataclass(frozen=True)
class ContextPackBuildReceipt:
    id: str
    schema_version: str
    context_pack_id: str
    created_at: str
    builder: dict[str, str | None]
    input_refs: dict[str, list[str]]
    selection_policy: dict[str, Any]
    excluded: list[dict[str, str]]
    warnings: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextPackBuildReceipt":
        _assert_no_forbidden_fields(data, "ContextPackBuildReceipt")
        record = _build_record(cls, data)
        record.validate()
        return record

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "schema_version must be 1.0.0")
        _require(isinstance(self.context_pack_id, str) and self.context_pack_id.startswith("ctx_"), "context_pack_id is required")
        _require(_is_iso8601(self.created_at), "created_at must be ISO-8601")
        self._validate_builder()
        self._validate_input_refs()
        self._validate_selection_policy()
        _validate_excluded(self.excluded)
        _string_list("warnings", self.warnings)
        _object("metadata", self.metadata)
        _assert_no_forbidden_fields(self.__dict__, "ContextPackBuildReceipt")
        _require(self.id == self.expected_id(), f"ContextPackBuildReceipt id mismatch: expected {self.expected_id()}")

    def _validate_builder(self) -> None:
        _object("builder", self.builder)
        _require(set(self.builder.keys()) == {"agent", "model", "tool", "mode"}, "builder keys invalid")
        _require(isinstance(self.builder["agent"], str) and self.builder["agent"], "builder.agent is required")
        for key in ["model", "tool"]:
            _require(self.builder[key] is None or isinstance(self.builder[key], str), f"builder.{key} invalid")
        _require(self.builder["mode"] in BUILDER_MODES, "invalid builder.mode")

    def _validate_input_refs(self) -> None:
        _object("input_refs", self.input_refs)
        expected = {"core_state_ids", "project_state_ids", "ledger_entry_ids", "taste_card_ids", "candidate_ids", "evidence_span_ids"}
        _require(set(self.input_refs.keys()) == expected, "input_refs keys invalid")
        _string_list("input_refs.core_state_ids", self.input_refs["core_state_ids"], "corestate_")
        _string_list("input_refs.project_state_ids", self.input_refs["project_state_ids"], "projstate_")
        _string_list("input_refs.ledger_entry_ids", self.input_refs["ledger_entry_ids"], ("fact_", "pref_", "dec_"))
        _string_list("input_refs.taste_card_ids", self.input_refs["taste_card_ids"], "taste_")
        _string_list("input_refs.candidate_ids", self.input_refs["candidate_ids"], "cand_")
        _string_list("input_refs.evidence_span_ids", self.input_refs["evidence_span_ids"], "span_")

    def _validate_selection_policy(self) -> None:
        _object("selection_policy", self.selection_policy)
        expected = {"current_cutoff", "include_stale", "include_contested", "include_candidates", "token_budget"}
        _require(set(self.selection_policy.keys()) == expected, "selection_policy keys invalid")
        _require(_is_iso8601(self.selection_policy["current_cutoff"]), "selection_policy.current_cutoff must be ISO-8601")
        for key in ["include_stale", "include_contested", "include_candidates"]:
            _require(isinstance(self.selection_policy[key], bool), f"selection_policy.{key} must be bool")
        _require(isinstance(self.selection_policy["token_budget"], int) and self.selection_policy["token_budget"] >= 0, "selection_policy.token_budget invalid")

    def expected_id(self) -> str:
        return make_context_pack_build_receipt_id(self.context_pack_id, self.input_refs, self.selection_policy)


@dataclass(frozen=True)
class ContextPackValidationReport:
    id: str
    schema_version: str
    context_pack_id: str
    validated_at: str
    validator: dict[str, str]
    status: str
    checks: dict[str, str]
    errors: list[dict[str, Any]]
    warnings: list[str]
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ContextPackValidationReport":
        _assert_no_forbidden_fields(data, "ContextPackValidationReport")
        record = _build_record(cls, data)
        record.validate()
        return record

    def validate(self) -> None:
        _require(self.schema_version == SCHEMA_VERSION, "schema_version must be 1.0.0")
        _require(isinstance(self.context_pack_id, str) and self.context_pack_id.startswith("ctx_"), "context_pack_id is required")
        _require(_is_iso8601(self.validated_at), "validated_at must be ISO-8601")
        _object("validator", self.validator)
        _require(set(self.validator.keys()) == {"agent", "tool", "version"}, "validator keys invalid")
        for key in ["agent", "tool", "version"]:
            _require(isinstance(self.validator[key], str) and self.validator[key], f"validator.{key} is required")
        _require(self.status in REPORT_STATUSES, "invalid validation report status")
        _object("checks", self.checks)
        _require(set(self.checks.keys()) == VALIDATION_CHECK_KEYS, "validation checks keys invalid")
        for key, value in self.checks.items():
            _require(value in CHECK_VALUES, f"checks.{key} invalid")
        _validate_error_list(self.errors)
        if self.status == "pass":
            failing = {key: value for key, value in self.checks.items() if value not in {"pass", "not_applicable"}}
            _require(not failing and not self.errors, "pass validation report cannot have failing checks or errors")
        if self.status == "fail":
            _require(bool(self.errors), "fail validation report must include errors")
        _string_list("warnings", self.warnings)
        _object("metadata", self.metadata)
        _assert_no_forbidden_fields(self.__dict__, "ContextPackValidationReport")
        _require(self.id == self.expected_id(), f"ContextPackValidationReport id mismatch: expected {self.expected_id()}")

    def expected_id(self) -> str:
        return make_context_pack_validation_report_id(self.context_pack_id, self.validated_at, self.status, self.checks, self.errors)


def context_pack_from_dict(data: dict[str, Any]) -> ContextPack:
    return ContextPack.from_dict(data)


def context_pack_build_receipt_from_dict(data: dict[str, Any]) -> ContextPackBuildReceipt:
    return ContextPackBuildReceipt.from_dict(data)


def context_pack_validation_report_from_dict(data: dict[str, Any]) -> ContextPackValidationReport:
    return ContextPackValidationReport.from_dict(data)
