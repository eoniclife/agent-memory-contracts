"""Worked example for the embedding input primitive.

Run from the repository root::

    PYTHONPATH=src python examples/embedding.py

This example builds a small synthetic bundle with one record
per type and demonstrates the four headline use cases of the
embedding input primitive:

1. :func:`record_to_embedding_input` — render a record as a
   deterministic :class:`EmbeddingInput`.
2. Per-type text rendering — the 12 hand-crafted renderers
   plus the generic fallback.
3. Truncation — long text is cut at the sentence boundary
   with a marker.
4. Privacy class surfacing — the product applies
   :func:`scope_bundle` (v0.9.0) before deciding what to
   embed.

The example does NOT call an embedding model. The library
stops at the input boundary. The product feeds
``EmbeddingInput.text`` to its model of choice and stores the
vector alongside the metadata.

.. versionadded:: 1.0.0-alpha.1
"""

from __future__ import annotations

import dataclasses
import sys
from typing import Any

from agent_memory_contracts import (
    EmbeddingInput,
    record_to_embedding_input,
    scope_bundle,
    team_scope,
    text_for_record_type,
)

T_CAPTURED = "2026-06-06T12:00:00Z"
T_DECIDED = "2026-06-06T13:00:00Z"


def _src_dict() -> dict[str, Any]:
    return {
        "id": "src_aaaa" + "a" * 19,
        "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": "Memory kernel design review",
        "origin_uri": "https://example.com/transcript/42",
        "raw_ref": {"kind": "external_uri", "value": "https://example.com/transcript/42"},
        "content_hash_sha256": "a" * 64,
        "captured_at": T_CAPTURED, "observed_at": T_CAPTURED,
        "author_or_sender": "user@example.com",
        "participants": ["user@example.com", "gpt-5.5"],
        "privacy_class": "internal",
        "custody_status": "external_pointer",
        "parser_version": "v1", "metadata": {},
    }


def _span_dict(src_id: str) -> dict[str, Any]:
    return {
        "id": "span_" + "b" * 24,
        "schema_version": "1.0.0",
        "source_id": src_id, "episode_id": None,
        "locator": {"kind": "line_range", "value": "10-15"},
        "text_excerpt": "We should ship spec-first, not vibe-code.",
        "excerpt_policy": "short_quote_allowed",
        "span_hash_sha256": "b" * 64,
        "privacy_class": "internal", "metadata": {},
    }


def _fact_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "fact_" + "a" * 24,
        "schema_version": "1.0.0",
        "ledger_type": "fact", "status": "active",
        "confidence": "high", "scope": "global",
        "source_record_ids": [src_id], "episode_record_ids": [],
        "evidence_span_ids": [span_id], "candidate_ids": [],
        "reducer_decision_id": "redmem_aaaa" + "a" * 18,
        "observed_at": None, "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED, "valid_until": None, "stale_after": None,
        "created_at": T_DECIDED, "updated_at": T_DECIDED,
        "supersedes": [], "superseded_by": [], "metadata": {},
        "subject": "memory architecture",
        "predicate": "approach",
        "object": "spec-first",
        "fact_text": "Spec-first beats no spec.",
    }


def _taste_card_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "taste_" + "a" * 24,
        "schema_version": "1.0.0",
        "card_type": "taste_card", "status": "active",
        "subject": "spec-first", "domain": "architecture",
        "scope": "global", "project_refs": [],
        "principle": "Spec-first beats no spec.",
        "rationale": "Falsifiable specs catch drift.",
        "strength": "strong", "confidence": "high",
        "taste_kind": "principle",
        "source_record_ids": [src_id], "episode_record_ids": [],
        "evidence_span_ids": [span_id], "candidate_taste_signal_ids": [],
        "positive_example_span_ids": [span_id], "negative_example_span_ids": [],
        "contrast_pairs": [], "objection_patterns": [], "application_notes": [],
        "reducer_decision_id": "redtaste_aaaa" + "a" * 17,
        "observed_at": None, "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED, "valid_until": None, "stale_after": None,
        "created_at": T_DECIDED, "updated_at": T_DECIDED,
        "supersedes": [], "superseded_by": [], "metadata": {},
    }


def _claim_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "cand_claim_" + "a" * 19,
        "schema_version": "1.0.0",
        "candidate_type": "claim",
        "source_record_ids": [src_id], "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": "A claim about spec-first.",
        "extracted_by": {"agent": "ext", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
        "extracted_at": "2026-06-06T12:30:00Z",
        "confidence": "high", "risk_class": "low",
        "status": "candidate", "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "subject": "memory", "predicate": "approach", "object": "spec-first",
        "claim_text": "Spec-first beats no spec.",
        "claim_scope": "global", "temporal_hint": {},
    }


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def demo_per_type_rendering() -> None:
    """Render one record per type, print the text and metadata."""
    section("1. Per-type text rendering (12 hand-crafted renderers)")

    src = _src_dict()
    span = _span_dict(src["id"])
    fact = _fact_dict(src["id"], span["id"])
    claim = _claim_dict(src["id"], span["id"])
    taste = _taste_card_dict(src["id"], span["id"])

    for record in [src, span, fact, claim, taste]:
        ei = record_to_embedding_input(record)
        print(f"\n--- {ei.record_type} ({ei.plane}) ---")
        print(f"  privacy_class: {ei.privacy_class}")
        print(f"  char_count:    {ei.char_count}")
        print(f"  hash:          {ei.content_hash_sha256[:16]}...")
        print(f"  text:")
        for line in ei.text.split("\n"):
            print(f"    {line}")


def demo_determinism() -> None:
    """Same record content -> same text and hash."""
    section("2. Determinism")

    src = _src_dict()
    e1 = record_to_embedding_input(src)
    e2 = record_to_embedding_input(src)
    print(f"e1.hash == e2.hash: {e1.content_hash_sha256 == e2.content_hash_sha256}")
    print(f"e1.text  == e2.text:  {e1.text == e2.text}")

    # And: dict-form == dataclass-form (when using the
    # same content).
    import dataclasses
    src_dc = dataclasses.make_dataclass("SourceRecord", [], bases=())  # placeholder
    # The dict-form test is in the test suite; we just
    # print a note here.
    print("\n(dict-form == dataclass-form parity is covered by the test suite.)")


def demo_truncation() -> None:
    """Long text is truncated at a sentence boundary."""
    section("3. Truncation (long text -> cut at sentence boundary)")

    long_text = ". ".join(["This is a long sentence about memory integrity."] * 50)
    fact = _fact_dict("src_x", "span_x")
    fact["fact_text"] = long_text
    ei = record_to_embedding_input(fact, max_chars=300)
    print(f"truncated: {ei.truncated}")
    print(f"char_count: {ei.char_count}")
    print(f"text ends with: ...{ei.text[-50:]!r}")


def demo_privacy_surfacing() -> None:
    """Apply BundleScope from v0.9.0, then render EmbeddingInput."""
    section("4. Privacy surfacing (BundleScope -> EmbeddingInput)")

    public = _src_dict()
    public["privacy_class"] = "public"
    sensitive = _src_dict()
    sensitive["id"] = "src_" + "b" * 24
    sensitive["title"] = "Sensitive source"
    sensitive["privacy_class"] = "highly_sensitive"

    bundle = [public, sensitive]
    filtered, _ = scope_bundle(bundle, team_scope())
    print(f"team_scope filtered: {len(filtered)} of {len(bundle)} records")

    for record in filtered:
        ei = record_to_embedding_input(record)
        print(f"\n  {ei.record_type} (privacy_class={ei.privacy_class})")
        print(f"    text preview: {ei.text.split(chr(10))[0]}")
        print(f"    metadata: {dict(ei.metadata)}")


def demo_round_trip() -> None:
    """to_dict / from_dict round-trip is JSON-clean."""
    section("5. Round-trip via to_dict / from_dict (JSON-clean)")
    import json
    from agent_memory_contracts import embedding_input_from_dict, embedding_input_to_dict

    src = _src_dict()
    ei = record_to_embedding_input(src)
    d = embedding_input_to_dict(ei)
    s = json.dumps(d, sort_keys=True)
    print(f"serialized length: {len(s)} bytes")
    d2 = json.loads(s)
    ei2 = embedding_input_from_dict(d2)
    print(f"round-trip equal: {ei == ei2}")


def main(argv: list[str] | None = None) -> int:
    demo_per_type_rendering()
    demo_determinism()
    demo_truncation()
    demo_privacy_surfacing()
    demo_round_trip()
    print()
    print("=" * 70)
    print("Embedding input example complete.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
