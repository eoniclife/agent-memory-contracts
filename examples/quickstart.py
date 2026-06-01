"""Quickstart example: extract, reduce, validate.

This is a runnable end-to-end demonstration of the library. It builds
a SourceRecord, an EvidenceSpan, a CandidateTasteSignal, a
MemoryReducerDecision, and a PreferenceLedgerEntry, then runs the
bundle validator to confirm the whole graph is internally consistent.

Run from the repo root:

    PYTHONPATH=src python examples/quickstart.py
"""

from __future__ import annotations

from dataclasses import asdict

from agent_memory_contracts import (
    CandidateTasteSignal,
    EvidenceSpan,
    MemoryReducerDecision,
    PreferenceLedgerEntry,
    SourceRecord,
    make_candidate_id,
    make_ledger_entry_id,
    make_reducer_decision_id,
    make_source_id,
    make_span_id,
    validate_ledger_bundle,
)


def _now() -> str:
    return "2026-05-30T13:00:00Z"


def build_evidence_plane() -> tuple[SourceRecord, EvidenceSpan]:
    """Build one SourceRecord and one EvidenceSpan covering it."""
    source_id = make_source_id(
        "chatgpt_conversation",
        "https://example.com/transcript/123",
        "a" * 64,  # fake sha256 for the demo
    )
    source = SourceRecord.from_dict({
        "id": source_id,
        "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": "Design review on memory kernel contracts",
        "origin_uri": "https://example.com/transcript/123",
        "raw_ref": {
            "kind": "external_uri",
            "value": "https://example.com/transcript/123",
        },
        "content_hash_sha256": "a" * 64,
        "captured_at": "2026-05-30T12:00:00Z",
        "observed_at": "2026-05-30T12:00:00Z",
        "author_or_sender": "user@example.com",
        "participants": ["user@example.com", "gpt-5.5"],
        "privacy_class": "internal",
        "custody_status": "external_pointer",
        "parser_version": "v1.0",
        "metadata": {"lang": "en"},
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
        "span_hash_sha256": "b" * 64,
        "privacy_class": "internal",
        "metadata": {},
    })
    return source, span


def build_candidate_plane(span_id: str) -> CandidateTasteSignal:
    """An LLM extracted this; it is untrusted until the reducer approves it.

    Note: the candidate id is content-derived from the full semantic
    payload, so the id helper must be called with the same fields the
    validator uses (domain, signal_kind, taste_text, example/contrast
    span ids, strength_hint).
    """
    candidate_id = make_candidate_id(
        "taste_signal",
        [span_id],
        {
            "domain": "architecture",
            "signal_kind": "principle",
            "taste_text": "Spec-text drift is worse than no spec",
            "example_span_ids": [span_id],
            "contrast_span_ids": [],
            "strength_hint": "strong",
        },
    )
    return CandidateTasteSignal.from_dict({
        "id": candidate_id,
        "schema_version": "1.0.0",
        "candidate_type": "taste_signal",
        "source_record_ids": [],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": (
            "User expressed a strong preference against spec-text drift: "
            "drifting specs are worse than having no spec at all."
        ),
        "extracted_by": {
            "agent": "extractor-v1",
            "model": "gpt-5.5",
            "tool": None,
            "prompt_ref": None,
        },
        "extracted_at": "2026-05-30T12:30:00Z",
        "confidence": "high",
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "domain": "architecture",
        "signal_kind": "principle",
        "taste_text": "Spec-text drift is worse than no spec",
        "example_span_ids": [span_id],
        "contrast_span_ids": [],
        "strength_hint": "strong",
    })


def reduce_to_trusted_memory(
    source_id: str, span_id: str, candidate_id: str
) -> tuple[MemoryReducerDecision, PreferenceLedgerEntry]:
    """The reducer promotes the candidate to a trusted ledger entry."""
    ledger_id = make_ledger_entry_id("preference", [span_id], {
        "ledger_type": "preference",
        "subject": "memory architecture",
        "preference_text": "Spec-text drift is worse than no spec",
        "domain": "architecture",
        "scope": "global",
        "valid_from": _now(),
        "evidence_span_ids": [span_id],
    })
    reducer_id = make_reducer_decision_id(
        decision_type="promote",
        target_candidate_ids=[candidate_id],
        target_ledger_entry_ids=[ledger_id],
        evidence_span_ids=[span_id],
        rationale=(
            "Clear principle with grounded evidence span. "
            "Useful as a hard constraint for memory architecture decisions."
        ),
    )
    reducer = MemoryReducerDecision.from_dict({
        "id": reducer_id,
        "schema_version": "1.0.0",
        "decision_type": "promote",
        "target_candidate_ids": [candidate_id],
        "target_ledger_entry_ids": [ledger_id],
        "evidence_span_ids": [span_id],
        "rationale": (
            "Clear principle with grounded evidence span. "
            "Useful as a hard constraint for memory architecture decisions."
        ),
        "decided_by": {
            "agent": "memory-reducer-v1",
            "model": "gpt-5.5",
            "tool": None,
            "prompt_ref": None,
        },
        "decided_at": _now(),
        "confidence": "high",
        "risk_class": "low",
        "checks": {
            "provenance": "pass",
            "temporal_validity": "pass",
            "contradiction_scan": "pass",
            "privacy": "pass",
            "usefulness": "pass",
        },
        "metadata": {},
    })
    ledger = PreferenceLedgerEntry.from_dict({
        "id": ledger_id,
        "schema_version": "1.0.0",
        "ledger_type": "preference",
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "candidate_ids": [candidate_id],
        "reducer_decision_id": reducer_id,
        "subject": "memory architecture",
        "preference_text": "Spec-text drift is worse than no spec",
        "domain": "architecture",
        "strength": "hard_constraint",
        "observed_at": "2026-05-30T12:00:00Z",
        "asserted_at": _now(),
        "valid_from": _now(),
        "valid_until": None,
        "stale_after": None,
        "created_at": _now(),
        "updated_at": _now(),
        "supersedes": [],
        "superseded_by": [],
        "metadata": {},
    })
    return reducer, ledger


def main() -> None:
    source, span = build_evidence_plane()
    candidate = build_candidate_plane(span.id)
    reducer, ledger = reduce_to_trusted_memory(source.id, span.id, candidate.id)

    print(f"  source:       {source.id}")
    print(f"  span:         {span.id}")
    print(f"  candidate:    {candidate.id}  (untrusted)")
    print(f"  reducer:      {reducer.id}  (authorizes the promotion)")
    print(f"  ledger entry: {ledger.id}  (now trusted)")

    # The full bundle graph: validates that every cross-plane reference
    # resolves and the reducer actually authorizes the entry.
    validate_ledger_bundle(
        source_records=[asdict(source)],
        episode_records=[],
        evidence_spans=[asdict(span)],
        candidate_records=[asdict(candidate)],
        reducer_decisions=[asdict(reducer)],
        ledger_entries=[asdict(ledger)],
    )
    print()
    print("Bundle validated. Cross-plane references resolve;")
    print("reducer decision authorizes the ledger entry.")


if __name__ == "__main__":
    main()
