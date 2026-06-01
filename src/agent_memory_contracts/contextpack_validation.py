"""Bundle validation for ContextPack contracts."""

from __future__ import annotations

from typing import Any, Iterable

from .candidate_contracts import candidate_from_dict
from .contextpack_contracts import (
    ContextPack,
    ContextPackBuildReceipt,
    ContextPackValidationReport,
    context_pack_build_receipt_from_dict,
    context_pack_from_dict,
    context_pack_validation_report_from_dict,
    parse_iso8601,
)
from .evidence_contracts import EvidenceSpan, EpisodeRecord, SourceRecord
from .ledger_contracts import ledger_entry_from_dict
from .state_contracts import core_state_from_dict, project_state_from_dict
from .taste_contracts import taste_card_from_dict


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _index(records: Iterable[Any], label: str) -> dict[str, Any]:
    indexed: dict[str, Any] = {}
    for record in records:
        _require(record.id not in indexed, f"duplicate {label} id: {record.id}")
        indexed[record.id] = record
    return indexed


def _is_active_at(record: Any, query_time: str) -> bool:
    if record.status != "active":
        return False
    if getattr(record, "superseded_by", []):
        return False
    query = parse_iso8601(query_time)
    valid_from = getattr(record, "valid_from", None)
    valid_until = getattr(record, "valid_until", None)
    stale_after = getattr(record, "stale_after", None)
    if valid_from is not None and query < parse_iso8601(valid_from):
        return False
    if valid_until is not None and query >= parse_iso8601(valid_until):
        return False
    if stale_after is not None and query >= parse_iso8601(stale_after):
        return False
    return True


def _validate_span_refs(pack: ContextPack, spans_by_id: dict[str, Any], sources_by_id: dict[str, Any], episodes_by_id: dict[str, Any]) -> None:
    declared_source_ids = set(pack.evidence["source_record_ids"])
    declared_episode_ids = set(pack.evidence["episode_record_ids"])
    for source_id in pack.evidence["source_record_ids"]:
        _require(source_id in sources_by_id, f"ContextPack references missing SourceRecord: {source_id}")
    for episode_id in pack.evidence["episode_record_ids"]:
        _require(episode_id in episodes_by_id, f"ContextPack references missing EpisodeRecord: {episode_id}")

    def validate_span(span_id: str, label: str) -> None:
        span = spans_by_id.get(span_id)
        _require(span is not None, f"ContextPack references missing {label} EvidenceSpan: {span_id}")
        _require(span.source_id in declared_source_ids, f"EvidenceSpan source_id is not declared in ContextPack evidence sources: {span_id}")
        if span.episode_id is not None:
            _require(span.episode_id in declared_episode_ids, f"EvidenceSpan episode_id is not declared in ContextPack evidence episodes: {span_id}")

    for span_id in pack.evidence["evidence_span_ids"]:
        validate_span(span_id, "")
    for span_id in pack.evidence["primary_evidence_span_ids"]:
        validate_span(span_id, "primary")
        _require(span_id in pack.evidence["evidence_span_ids"], "primary evidence span is not included in evidence_span_ids")


def _candidate_ids(pack: ContextPack) -> list[str]:
    ids: list[str] = []
    for values in pack.candidate_context.values():
        ids.extend(values)
    return ids


def _ledger_ids(pack: ContextPack) -> list[str]:
    ids: list[str] = []
    ids.extend(pack.trusted_memory["fact_ids"])
    ids.extend(pack.trusted_memory["preference_ids"])
    ids.extend(pack.trusted_memory["decision_ids"])
    ids.extend(pack.stale_or_superseded["fact_ids"])
    ids.extend(pack.stale_or_superseded["preference_ids"])
    ids.extend(pack.stale_or_superseded["decision_ids"])
    return ids


def _taste_ids(pack: ContextPack) -> list[str]:
    return pack.trusted_memory["taste_card_ids"] + pack.stale_or_superseded["taste_card_ids"]


def _project_state_ids(pack: ContextPack) -> list[str]:
    return pack.state["project_state_ids"] + pack.stale_or_superseded["project_state_ids"]


def _core_state_ids(pack: ContextPack) -> list[str]:
    current = [pack.state["core_state_id"]] if pack.state["core_state_id"] else []
    return current + pack.stale_or_superseded["core_state_ids"]


def _material_pack_ids(pack: ContextPack) -> set[str]:
    material_ids: set[str] = set()
    material_ids.update(_core_state_ids(pack))
    material_ids.update(_project_state_ids(pack))
    material_ids.update(_ledger_ids(pack))
    material_ids.update(_taste_ids(pack))
    material_ids.update(_candidate_ids(pack))
    material_ids.update(pack.evidence["evidence_span_ids"])
    material_ids.update(pack.evidence["primary_evidence_span_ids"])
    return material_ids


def _validate_candidate_context(pack: ContextPack, candidates_by_id: dict[str, Any]) -> None:
    expected_types = {
        "candidate_task_ids": "task",
        "candidate_claim_ids": "claim",
        "candidate_preference_ids": "preference",
        "candidate_decision_ids": "decision",
        "candidate_taste_signal_ids": "taste_signal",
    }
    for field, candidate_type in expected_types.items():
        for candidate_id in pack.candidate_context[field]:
            candidate = candidates_by_id.get(candidate_id)
            _require(candidate is not None, f"ContextPack references missing candidate: {candidate_id}")
            _require(candidate.candidate_type == candidate_type, f"candidate {candidate_id} is in wrong candidate_context section")


def _validate_current_memory(pack: ContextPack, ledgers_by_id: dict[str, Any], taste_by_id: dict[str, Any], projects_by_id: dict[str, Any], cores_by_id: dict[str, Any]) -> None:
    query_time = pack.authority["created_at"]
    if pack.task["task_type"] in {"writing", "architecture_review", "review"}:
        _require(bool(pack.trusted_memory["taste_card_ids"]), f"taste-sensitive task requires active TasteCards: {pack.id}")
    expected_ledgers = {
        "fact_ids": "fact",
        "preference_ids": "preference",
        "decision_ids": "decision",
    }
    for field, ledger_type in expected_ledgers.items():
        for ledger_id in pack.trusted_memory[field]:
            record = ledgers_by_id.get(ledger_id)
            _require(record is not None, f"ContextPack references missing ledger entry: {ledger_id}")
            _require(record.ledger_type == ledger_type, f"ledger {ledger_id} is in wrong trusted_memory section")
            _require(_is_active_at(record, query_time), f"trusted ledger entry is not active at pack creation: {ledger_id}")
    for taste_id in pack.trusted_memory["taste_card_ids"]:
        record = taste_by_id.get(taste_id)
        _require(record is not None, f"ContextPack references missing TasteCard: {taste_id}")
        _require(_is_active_at(record, query_time), f"trusted TasteCard is not active at pack creation: {taste_id}")
    for project_state_id in pack.state["project_state_ids"]:
        record = projects_by_id.get(project_state_id)
        _require(record is not None, f"ContextPack references missing ProjectStateSnapshot: {project_state_id}")
        _require(_is_active_at(record, query_time), f"ProjectStateSnapshot is not active at pack creation: {project_state_id}")
    core_state_id = pack.state["core_state_id"]
    if core_state_id:
        record = cores_by_id.get(core_state_id)
        _require(record is not None, f"ContextPack references missing CoreStateSnapshot: {core_state_id}")
        _require(_is_active_at(record, query_time), f"CoreStateSnapshot is not active at pack creation: {core_state_id}")


def _validate_stale_or_superseded(pack: ContextPack, ledgers_by_id: dict[str, Any], taste_by_id: dict[str, Any], projects_by_id: dict[str, Any], cores_by_id: dict[str, Any]) -> None:
    allowed = {"stale", "superseded", "contested"}
    for field in ["fact_ids", "preference_ids", "decision_ids"]:
        for ledger_id in pack.stale_or_superseded[field]:
            record = ledgers_by_id.get(ledger_id)
            _require(record is not None, f"ContextPack references missing stale ledger entry: {ledger_id}")
            _require(record.status in allowed, f"stale_or_superseded ledger has invalid status: {ledger_id}")
    for taste_id in pack.stale_or_superseded["taste_card_ids"]:
        record = taste_by_id.get(taste_id)
        _require(record is not None, f"ContextPack references missing stale TasteCard: {taste_id}")
        _require(record.status in allowed, f"stale_or_superseded TasteCard has invalid status: {taste_id}")
    for project_state_id in pack.stale_or_superseded["project_state_ids"]:
        record = projects_by_id.get(project_state_id)
        _require(record is not None, f"ContextPack references missing stale ProjectStateSnapshot: {project_state_id}")
        _require(record.status in allowed, f"stale_or_superseded ProjectStateSnapshot has invalid status: {project_state_id}")
    for core_state_id in pack.stale_or_superseded["core_state_ids"]:
        record = cores_by_id.get(core_state_id)
        _require(record is not None, f"ContextPack references missing stale CoreStateSnapshot: {core_state_id}")
        _require(record.status in allowed, f"stale_or_superseded CoreStateSnapshot has invalid status: {core_state_id}")


def _receipt_covers_pack(pack: ContextPack, receipt: ContextPackBuildReceipt) -> None:
    _require(receipt.selection_policy["current_cutoff"] == pack.authority["created_at"], "receipt current_cutoff must match ContextPack authority.created_at")
    refs = receipt.input_refs
    if pack.state["core_state_id"]:
        _require(pack.state["core_state_id"] in refs["core_state_ids"], "receipt missing pack CoreState ID")
    _require(set(pack.state["project_state_ids"]).issubset(set(refs["project_state_ids"])), "receipt missing pack ProjectState IDs")
    _require(set(_ledger_ids(pack)).issubset(set(refs["ledger_entry_ids"])), "receipt missing pack ledger IDs")
    _require(set(_taste_ids(pack)).issubset(set(refs["taste_card_ids"])), "receipt missing pack TasteCard IDs")
    _require(set(_candidate_ids(pack)).issubset(set(refs["candidate_ids"])), "receipt missing pack candidate IDs")
    _require(set(pack.evidence["evidence_span_ids"]).issubset(set(refs["evidence_span_ids"])), "receipt missing pack EvidenceSpan IDs")


def _stale_or_superseded_records(pack: ContextPack, ledgers_by_id: dict[str, Any], taste_by_id: dict[str, Any], projects_by_id: dict[str, Any], cores_by_id: dict[str, Any]) -> list[Any]:
    records: list[Any] = []
    for field in ["fact_ids", "preference_ids", "decision_ids"]:
        records.extend(ledgers_by_id[record_id] for record_id in pack.stale_or_superseded[field])
    records.extend(taste_by_id[record_id] for record_id in pack.stale_or_superseded["taste_card_ids"])
    records.extend(projects_by_id[record_id] for record_id in pack.stale_or_superseded["project_state_ids"])
    records.extend(cores_by_id[record_id] for record_id in pack.stale_or_superseded["core_state_ids"])
    return records


def _validate_receipt_policy_honesty(
    pack: ContextPack,
    receipt: ContextPackBuildReceipt,
    ledgers_by_id: dict[str, Any],
    taste_by_id: dict[str, Any],
    projects_by_id: dict[str, Any],
    cores_by_id: dict[str, Any],
) -> None:
    records = _stale_or_superseded_records(pack, ledgers_by_id, taste_by_id, projects_by_id, cores_by_id)
    if records:
        _require(receipt.selection_policy["include_stale"], "receipt include_stale must be true when stale_or_superseded IDs are included")
    if any(record.status == "contested" for record in records):
        _require(receipt.selection_policy["include_contested"], "receipt include_contested must be true when contested records are included")


def _validate_reports(pack: ContextPack, reports: list[ContextPackValidationReport]) -> None:
    _require(reports, f"ContextPack has no validation report: {pack.id}")
    _require(any(report.status == "pass" for report in reports), f"ContextPack must have at least one passing validation report: {pack.id}")
    for report in reports:
        if report.status == "pass":
            failing = {key: value for key, value in report.checks.items() if value not in {"pass", "not_applicable"}}
            _require(not failing and not report.errors, f"pass validation report has failing checks or errors: {report.id}")
        else:
            _require(bool(report.errors), f"fail validation report has no errors: {report.id}")


def validate_contextpack_bundle(
    source_records: Iterable[dict[str, Any]],
    episode_records: Iterable[dict[str, Any]],
    evidence_spans: Iterable[dict[str, Any]],
    candidate_records: Iterable[dict[str, Any]],
    ledger_entries: Iterable[dict[str, Any]],
    taste_cards: Iterable[dict[str, Any]],
    project_states: Iterable[dict[str, Any]],
    core_states: Iterable[dict[str, Any]],
    context_packs: Iterable[dict[str, Any]],
    build_receipts: Iterable[dict[str, Any]],
    validation_reports: Iterable[dict[str, Any]],
) -> None:
    sources_by_id = _index((SourceRecord.from_dict(record) for record in source_records), "SourceRecord")
    episodes_by_id = _index((EpisodeRecord.from_dict(record) for record in episode_records), "EpisodeRecord")
    spans_by_id = _index((EvidenceSpan.from_dict(record) for record in evidence_spans), "EvidenceSpan")
    candidates_by_id = _index((candidate_from_dict(record) for record in candidate_records), "candidate")
    ledgers_by_id = _index((ledger_entry_from_dict(record) for record in ledger_entries), "ledger")
    taste_by_id = _index((taste_card_from_dict(record) for record in taste_cards), "TasteCard")
    projects_by_id = _index((project_state_from_dict(record) for record in project_states), "ProjectStateSnapshot")
    cores_by_id = _index((core_state_from_dict(record) for record in core_states), "CoreStateSnapshot")
    packs_by_id = _index((context_pack_from_dict(record) for record in context_packs), "ContextPack")
    receipts_by_id = _index((context_pack_build_receipt_from_dict(record) for record in build_receipts), "ContextPackBuildReceipt")
    reports_by_id = _index((context_pack_validation_report_from_dict(record) for record in validation_reports), "ContextPackValidationReport")

    receipts_by_pack: dict[str, list[ContextPackBuildReceipt]] = {}
    for receipt in receipts_by_id.values():
        _require(receipt.context_pack_id in packs_by_id, f"receipt references missing ContextPack: {receipt.context_pack_id}")
        receipts_by_pack.setdefault(receipt.context_pack_id, []).append(receipt)

    reports_by_pack: dict[str, list[ContextPackValidationReport]] = {}
    for report in reports_by_id.values():
        _require(report.context_pack_id in packs_by_id, f"validation report references missing ContextPack: {report.context_pack_id}")
        reports_by_pack.setdefault(report.context_pack_id, []).append(report)

    for pack in packs_by_id.values():
        _validate_span_refs(pack, spans_by_id, sources_by_id, episodes_by_id)
        _require(_material_pack_ids(pack).issubset(set(pack.retrieval_trace["included_ids"])), "retrieval_trace.included_ids must cover material ContextPack IDs")
        _validate_candidate_context(pack, candidates_by_id)
        _validate_current_memory(pack, ledgers_by_id, taste_by_id, projects_by_id, cores_by_id)
        _validate_stale_or_superseded(pack, ledgers_by_id, taste_by_id, projects_by_id, cores_by_id)
        receipts = receipts_by_pack.get(pack.id, [])
        _require(receipts, f"ContextPack has no build receipt: {pack.id}")
        for receipt in receipts:
            _receipt_covers_pack(pack, receipt)
            _validate_receipt_policy_honesty(pack, receipt, ledgers_by_id, taste_by_id, projects_by_id, cores_by_id)
        _validate_reports(pack, reports_by_pack.get(pack.id, []))
