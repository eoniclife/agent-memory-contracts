"""Stable identifiers for trusted memory ledger records."""

from __future__ import annotations

import json
from typing import Any

from .evidence_ids import sha256_hex

PREFIX_BY_LEDGER_TYPE = {
    "fact": "fact",
    "preference": "pref",
    "decision": "dec",
}


def canonical_payload(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_reducer_decision_id(
    decision_type: str,
    target_candidate_ids: list[str],
    target_ledger_entry_ids: list[str],
    evidence_span_ids: list[str],
    rationale: str,
) -> str:
    payload = {
        "decision_type": decision_type,
        "target_candidate_ids": sorted(target_candidate_ids),
        "target_ledger_entry_ids": sorted(target_ledger_entry_ids),
        "evidence_span_ids": sorted(evidence_span_ids),
        "rationale": rationale,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"redmem_{digest}"


def make_ledger_entry_id(ledger_type: str, evidence_span_ids: list[str], normalized_payload: dict[str, Any]) -> str:
    if ledger_type not in PREFIX_BY_LEDGER_TYPE:
        raise ValueError(f"unsupported ledger_type: {ledger_type}")
    payload = {
        "ledger_type": ledger_type,
        "evidence_span_ids": sorted(evidence_span_ids),
        "normalized_payload": normalized_payload,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"{PREFIX_BY_LEDGER_TYPE[ledger_type]}_{digest}"
