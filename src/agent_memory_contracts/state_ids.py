"""Stable identifiers for reducer-approved state snapshots."""

from __future__ import annotations

import json
from typing import Any

from .evidence_ids import sha256_hex


def canonical_payload(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_state_reducer_decision_id(
    decision_type: str,
    target_project_state_ids: list[str],
    target_core_state_ids: list[str],
    evidence_span_ids: list[str],
    ledger_entry_ids: list[str],
    taste_card_ids: list[str],
    rationale: str,
) -> str:
    payload = {
        "decision_type": decision_type,
        "target_project_state_ids": sorted(target_project_state_ids),
        "target_core_state_ids": sorted(target_core_state_ids),
        "evidence_span_ids": sorted(evidence_span_ids),
        "ledger_entry_ids": sorted(ledger_entry_ids),
        "taste_card_ids": sorted(taste_card_ids),
        "rationale": rationale,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"redstate_{digest}"


def make_project_state_id(project_id: str, as_of: str, evidence_span_ids: list[str], normalized_payload: dict[str, Any]) -> str:
    payload = {
        "project_id": project_id,
        "as_of": as_of,
        "evidence_span_ids": sorted(evidence_span_ids),
        "normalized_payload": normalized_payload,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"projstate_{digest}"


def make_core_state_id(subject: str, as_of: str, evidence_span_ids: list[str], normalized_payload: dict[str, Any]) -> str:
    payload = {
        "subject": subject,
        "as_of": as_of,
        "evidence_span_ids": sorted(evidence_span_ids),
        "normalized_payload": normalized_payload,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"corestate_{digest}"
