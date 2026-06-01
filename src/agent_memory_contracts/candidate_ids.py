"""Stable identifiers for untrusted candidate interpretation records."""

from __future__ import annotations

import json
from typing import Any

from .evidence_ids import sha256_hex

PREFIX_BY_TYPE = {
    "claim": "cand_claim",
    "preference": "cand_pref",
    "decision": "cand_decision",
    "task": "cand_task",
    "taste_signal": "cand_taste",
}


def canonical_payload(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_candidate_id(candidate_type: str, evidence_span_ids: list[str], normalized_payload: dict[str, Any]) -> str:
    if candidate_type not in PREFIX_BY_TYPE:
        raise ValueError(f"unsupported candidate_type: {candidate_type}")
    payload = {
        "candidate_type": candidate_type,
        "evidence_span_ids": sorted(evidence_span_ids),
        "normalized_payload": normalized_payload,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"{PREFIX_BY_TYPE[candidate_type]}_{digest}"

