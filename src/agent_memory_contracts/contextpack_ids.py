"""Stable identifiers for ContextPack records."""

from __future__ import annotations

import json
from typing import Any

from .evidence_ids import sha256_hex


def canonical_payload(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def make_context_pack_id(task_id: str, evidence_span_ids: list[str], normalized_payload: dict[str, Any]) -> str:
    payload = {
        "task_id": task_id,
        "evidence_span_ids": sorted(evidence_span_ids),
        "normalized_payload": normalized_payload,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"ctx_{digest}"


def make_context_pack_build_receipt_id(context_pack_id: str, input_refs: dict[str, Any], selection_policy: dict[str, Any]) -> str:
    payload = {
        "context_pack_id": context_pack_id,
        "input_refs": input_refs,
        "selection_policy": selection_policy,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"ctxreceipt_{digest}"


def make_context_pack_validation_report_id(
    context_pack_id: str,
    validated_at: str,
    status: str,
    checks: dict[str, Any],
    errors: list[dict[str, Any]],
) -> str:
    payload = {
        "context_pack_id": context_pack_id,
        "validated_at": validated_at,
        "status": status,
        "checks": checks,
        "errors": errors,
    }
    digest = sha256_hex(canonical_payload(payload))[:24]
    return f"ctxval_{digest}"
