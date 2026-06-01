"""Shared test fixtures for agent-memory-contracts tests.

These are tiny synthetic records that satisfy the contracts end-to-end.
They are not realistic (the source_uri is a placeholder) but they are
internally consistent: every cross-plane reference resolves.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agent_memory_contracts import (
    EvidenceSpan,
    SourceRecord,
    make_source_id,
    make_span_id,
)

SOURCE_URI = "https://example.com/transcript/42"
CONTENT_HASH = "a" * 64
SPAN_HASH = "b" * 64

# Fixed timestamps so the id derivations are deterministic.
T_CAPTURED = "2026-05-30T12:00:00Z"
T_EXTRACTED = "2026-05-30T12:30:00Z"
T_DECIDED = "2026-05-30T13:00:00Z"


def build_source_and_span() -> tuple[SourceRecord, EvidenceSpan]:
    """Build a SourceRecord and one EvidenceSpan that references it."""
    source_id = make_source_id("chatgpt_conversation", SOURCE_URI, CONTENT_HASH)
    source = SourceRecord.from_dict({
        "id": source_id,
        "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": "Memory kernel design review",
        "origin_uri": SOURCE_URI,
        "raw_ref": {"kind": "external_uri", "value": SOURCE_URI},
        "content_hash_sha256": CONTENT_HASH,
        "captured_at": T_CAPTURED,
        "observed_at": T_CAPTURED,
        "author_or_sender": "user@example.com",
        "participants": ["user@example.com", "gpt-5.5"],
        "privacy_class": "internal",
        "custody_status": "external_pointer",
        "parser_version": "v1.0",
        "metadata": {},
    })
    span_id = make_span_id(source_id, "line_range", "10-15")
    span = EvidenceSpan.from_dict({
        "id": span_id,
        "schema_version": "1.0.0",
        "source_id": source_id,
        "episode_id": None,
        "locator": {"kind": "line_range", "value": "10-15"},
        "text_excerpt": None,
        "excerpt_policy": "none",
        "span_hash_sha256": SPAN_HASH,
        "privacy_class": "internal",
        "metadata": {},
    })
    return source, span


def as_dicts(*records: Any) -> list[dict[str, Any]]:
    """Convert dataclass records to plain dicts (for bundle validators)."""
    return [asdict(record) for record in records]
