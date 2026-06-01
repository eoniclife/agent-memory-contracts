"""Stable identifiers for trusted taste memory records."""

from __future__ import annotations

import json
from typing import Any

from .evidence_ids import sha256_hex


def canonical_payload(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_taste_reducer_decision_id(
    decision_type: str,
    target_taste_signal_ids: list[str],
    target_taste_card_ids: list[str],
    evidence_span_ids: list[str],
    rationale: str,
) -> str:
    payload = {
        "decision_type": decision_type,
        "target_taste_signal_ids": sorted(target_taste_signal_ids),
        "target_taste_card_ids": sorted(target_taste_card_ids),
        "evidence_span_ids": sorted(evidence_span_ids),
        "rationale": rationale,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"redtaste_{digest}"


def make_taste_card_id(evidence_span_ids: list[str], normalized_payload: dict[str, Any]) -> str:
    payload = {
        "evidence_span_ids": sorted(evidence_span_ids),
        "normalized_payload": normalized_payload,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"taste_{digest}"
