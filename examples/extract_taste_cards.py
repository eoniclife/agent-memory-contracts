"""Worked example: extract taste cards from a synthetic transcript.

This is a longer end-to-end demonstration than examples/quickstart.py.
It takes a small synthetic transcript (a few turns of a user talking
about how they like to work), shows how the LLM-extracted candidate
taste signals would look, runs them through the reducer, and produces
the resulting TasteCards with positive/negative example grounding.

The transcript is synthetic; the extraction is hand-built (no LLM
call); the example exists to show the data flow and the validation
contract, not to be a real LLM extraction pipeline.

Run from the repo root:

    PYTHONPATH=src python examples/extract_taste_cards.py
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Iterable

from agent_memory_contracts import (
    CandidateTasteSignal,
    ContextPack,
    ContextPackBuildReceipt,
    ContextPackValidationReport,
    EvidenceSpan,
    SourceRecord,
    TasteCard,
    TasteReducerDecision,
    current_taste_cards,
    make_candidate_id,
    make_context_pack_id,
    make_reducer_decision_id,
    make_source_id,
    make_span_id,
    make_taste_card_id,
    make_taste_reducer_decision_id,
    make_taste_reducer_decision_id as _trid,  # noqa: F401
    validate_taste_bundle,
)

T_OBSERVED = "2026-05-15T10:00:00Z"
T_DECIDED = "2026-05-15T11:00:00Z"


# ---------------------------------------------------------------------------
# Step 0: a synthetic transcript
# ---------------------------------------------------------------------------
TRANSCRIPT = """
user: I'm reviewing the spec doc you wrote. It's drifting from what we
agreed on last week -- the new section contradicts the existing one
about how reducer decisions are scoped. Please stop and revert.

user: Spec drift is worse than no spec. If we can't keep a doc aligned
with what we actually decided, we should throw it out and start from
a real conversation.

user: Compare: the AVS memory kernel review packets always link back to
the locked architecture. That's the right pattern. Specs that drift
silently are a tax on every reader.

assistant: Understood -- reverting that section, and I'll make sure
future drafts flag contradictions explicitly rather than smoothing them
over.

user: Also: I want hard constraints on memory architecture, not soft
preferences. "Prefer" doesn't survive a sprint. Either it is a rule or
it is not a rule. Make it a rule.

user: One thing I do like: how TasteCards force you to anchor a
principle in at least one positive or negative example. That keeps
the rules honest.
"""


# ---------------------------------------------------------------------------
# Step 1: build a SourceRecord and the evidence spans that anchor each turn
# ---------------------------------------------------------------------------
def build_source() -> SourceRecord:
    source_id = make_source_id(
        "manual_note",
        "synthetic://transcripts/taste-extraction-demo-2026-05-15",
        "f" * 64,
    )
    return SourceRecord.from_dict({
        "id": source_id,
        "schema_version": "1.0.0",
        "source_type": "manual_note",
        "title": "Transcript: taste extraction demo (2026-05-15)",
        "origin_uri": "synthetic://transcripts/taste-extraction-demo-2026-05-15",
        "raw_ref": {
            "kind": "synthetic_fixture",
            "value": "synthetic://transcripts/taste-extraction-demo-2026-05-15",
        },
        "content_hash_sha256": "f" * 64,
        "captured_at": T_OBSERVED,
        "observed_at": T_OBSERVED,
        "author_or_sender": "user@example.com",
        "participants": ["user@example.com", "assistant"],
        "privacy_class": "internal",
        "custody_status": "synthetic",
        "parser_version": "v1.0-demo",
        "metadata": {"demo": True},
    })


def build_spans(source_id: str) -> list[EvidenceSpan]:
    """One EvidenceSpan per user turn we want to anchor a taste card to."""
    spans: list[EvidenceSpan] = []
    spans_data = [
        ("line_range", "2-6", "Spec-drift complaint"),
        ("line_range", "8-10", "Spec drift > no spec principle"),
        ("line_range", "12-14", "AVS kernel review packets as positive example"),
        ("line_range", "18-20", "Hard constraints, not soft preferences"),
        ("line_range", "22-24", "TasteCards force example-grounding as positive"),
    ]
    for kind, value, summary in spans_data:
        span_id = make_span_id(source_id, kind, value)
        spans.append(EvidenceSpan.from_dict({
            "id": span_id,
            "schema_version": "1.0.0",
            "source_id": source_id,
            "episode_id": None,
            "locator": {"kind": kind, "value": value},
            "text_excerpt": summary,
            "excerpt_policy": "synthetic",
            "span_hash_sha256": sha256_of(f"{source_id}:{kind}:{value}"),
            "privacy_class": "internal",
            "metadata": {"summary": summary},
        }))
    return spans


def sha256_of(value: str) -> str:
    """Fake but deterministic 64-char hex."""
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Step 2: LLM-extracted candidate taste signals (hand-built, not real LLM)
# ---------------------------------------------------------------------------
def build_candidate_taste_signals(
    source_id: str, spans: list[EvidenceSpan]
) -> list[CandidateTasteSignal]:
    """Hand-build the candidate taste signals a real LLM extractor would produce."""
    cands = []
    # Signal 1: principle "spec drift > no spec", with positive example
    s0 = spans[0]  # line 2-6 (drift complaint)
    s1 = spans[1]  # line 8-10 (the principle itself)
    s2 = spans[2]  # line 12-14 (AVS positive example)
    cands.append(CandidateTasteSignal.from_dict({
        "id": make_candidate_id("taste_signal", [s0.id, s1.id, s2.id], {
            "domain": "writing",
            "signal_kind": "principle",
            "taste_text": "Spec-text drift is worse than no spec.",
            "example_span_ids": sorted([s1.id, s2.id]),
            "contrast_span_ids": [],
            "strength_hint": "hard_constraint",
        }),
        "schema_version": "1.0.0",
        "candidate_type": "taste_signal",
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": [s0.id, s1.id, s2.id],
        "natural_language_summary": (
            "User expressed a hard-constraint principle against spec-text "
            "drift and praised the AVS kernel review packets as a positive "
            "example of the right pattern."
        ),
        "extracted_by": {"agent": "extractor-v1-demo", "model": "gpt-5.5", "tool": None, "prompt_ref": "demo/taste-extraction"},
        "extracted_at": T_OBSERVED,
        "confidence": "high",
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "domain": "writing",
        "signal_kind": "principle",
        "taste_text": "Spec-text drift is worse than no spec.",
        "example_span_ids": [s1.id, s2.id],
        "contrast_span_ids": [],
        "strength_hint": "hard_constraint",
    }))
    # Signal 2: principle "hard constraints, not soft preferences"
    s2 = spans[3]
    cands.append(CandidateTasteSignal.from_dict({
        "id": make_candidate_id("taste_signal", [s2.id], {
            "domain": "architecture",
            "signal_kind": "principle",
            "taste_text": "Memory architecture rules are hard constraints, not soft preferences.",
            "example_span_ids": sorted([s2.id]),
            "contrast_span_ids": [],
            "strength_hint": "hard_constraint",
        }),
        "schema_version": "1.0.0",
        "candidate_type": "taste_signal",
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": [s2.id],
        "natural_language_summary": "User wants hard constraints on memory architecture, not soft preferences.",
        "extracted_by": {"agent": "extractor-v1-demo", "model": "gpt-5.5", "tool": None, "prompt_ref": "demo/taste-extraction"},
        "extracted_at": T_OBSERVED,
        "confidence": "high",
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "domain": "architecture",
        "signal_kind": "principle",
        "taste_text": "Memory architecture rules are hard constraints, not soft preferences.",
        "example_span_ids": [s2.id],
        "contrast_span_ids": [],
        "strength_hint": "hard_constraint",
    }))
    # Signal 3: positive example -- TasteCards force example-grounding
    s3 = spans[4]
    cands.append(CandidateTasteSignal.from_dict({
        "id": make_candidate_id("taste_signal", [s3.id], {
            "domain": "architecture",
            "signal_kind": "positive_example",
            "taste_text": "TasteCards must anchor principles in positive or negative examples.",
            "example_span_ids": sorted([s3.id]),
            "contrast_span_ids": [],
            "strength_hint": "strong",
        }),
        "schema_version": "1.0.0",
        "candidate_type": "taste_signal",
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": [s3.id],
        "natural_language_summary": "User explicitly praised the TasteCard example-grounding requirement.",
        "extracted_by": {"agent": "extractor-v1-demo", "model": "gpt-5.5", "tool": None, "prompt_ref": "demo/taste-extraction"},
        "extracted_at": T_OBSERVED,
        "confidence": "high",
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "domain": "architecture",
        "signal_kind": "positive_example",
        "taste_text": "TasteCards must anchor principles in positive or negative examples.",
        "example_span_ids": [s3.id],
        "contrast_span_ids": [],
        "strength_hint": "strong",
    }))
    return cands


# ---------------------------------------------------------------------------
# Step 3: the reducer promotes each candidate to a TasteCard
# ---------------------------------------------------------------------------
def build_taste_cards(
    source_id: str,
    candidates: list[CandidateTasteSignal],
    spans: list[EvidenceSpan],
) -> list[tuple[TasteCard, TasteReducerDecision]]:
    """For each candidate, build a corresponding TasteCard + reducer decision.

    For the spec-drift candidate, we additionally build a contrast pair
    between the drift turn (spans[0]) and the AVS-kernel-praises turn
    (spans[2]). The candidate's example_span_ids are strings (ids), so
    we look up the actual EvidenceSpan objects via the spans list.
    """
    out = []
    spans_by_id = {s.id: s for s in spans}
    for c in candidates:
        span_ids = list(c.evidence_span_ids)
        positive = list(c.example_span_ids)
        negative = []
        contrast_pairs: list[dict] = []
        # For the first candidate, the user explicitly contrasted "drift"
        # (line 2-6) with "AVS kernel review packets" (line 12-14). Capture that.
        if "drift" in c.taste_text.lower():
            drift_span_id = spans_by_id[spans[0].id].id
            avs_span_id = spans_by_id[spans[2].id].id
            positive = [avs_span_id]
            negative = [drift_span_id]
            contrast_pairs = [{
                "preferred_span_id": avs_span_id,
                "rejected_span_id": drift_span_id,
                "reason": "AVS review packets stay locked to the architecture; drift breaks that link.",
            }]
        card_id = make_taste_card_id(span_ids, {
            "subject": "writing" if c.domain == "writing" else "memory architecture",
            "domain": c.domain,
            "scope": "global",
            "project_refs": [],
            "principle": c.taste_text,
            "taste_kind": c.signal_kind,
            "valid_from": T_DECIDED,
            "evidence_span_ids": sorted(span_ids),
        })
        reducer_id = make_taste_reducer_decision_id(
            "promote", [c.id], [card_id], span_ids, "demo promotion"
        )
        card = TasteCard.from_dict({
            "id": card_id,
            "schema_version": "1.0.0",
            "card_type": "taste_card",
            "status": "active",
            "subject": "writing" if c.domain == "writing" else "memory architecture",
            "domain": c.domain,
            "scope": "global",
            "project_refs": [],
            "principle": c.taste_text,
            "rationale": "Clear principle with grounded example spans.",
            "strength": c.strength_hint,
            "confidence": c.confidence,
            "taste_kind": c.signal_kind,
            "source_record_ids": [source_id],
            "episode_record_ids": [],
            "evidence_span_ids": span_ids,
            "candidate_taste_signal_ids": [c.id],
            "positive_example_span_ids": positive,
            "negative_example_span_ids": negative,
            "contrast_pairs": contrast_pairs,
            "objection_patterns": [],
            "application_notes": [],
            "reducer_decision_id": reducer_id,
            "observed_at": T_OBSERVED,
            "asserted_at": T_DECIDED,
            "valid_from": T_DECIDED,
            "valid_until": None,
            "stale_after": None,
            "created_at": T_DECIDED,
            "updated_at": T_DECIDED,
            "supersedes": [],
            "superseded_by": [],
            "metadata": {"human_asserted": True},
        })
        reducer = TasteReducerDecision.from_dict({
            "id": reducer_id,
            "schema_version": "1.0.0",
            "decision_type": "promote",
            "target_taste_signal_ids": [c.id],
            "target_taste_card_ids": [card.id],
            "evidence_span_ids": span_ids,
            "rationale": "demo promotion",
            "decided_by": {"agent": "taste-reducer-v1-demo", "model": "gpt-5.5", "tool": None, "prompt_ref": "demo/promote"},
            "decided_at": T_DECIDED,
            "confidence": "high",
            "risk_class": "low",
            "checks": {
                "provenance": "pass", "specificity": "pass",
                "example_grounding": "pass", "contrast_grounding": "pass",
                "privacy": "pass", "usefulness": "pass",
            },
            "metadata": {},
        })
        out.append((card, reducer))
    return out


# ---------------------------------------------------------------------------
# Step 4: validate the full taste bundle, then query
# ---------------------------------------------------------------------------
def main() -> None:
    source = build_source()
    spans = build_spans(source.id)
    candidates = build_candidate_taste_signals(source.id, spans)
    cards_and_reducers = build_taste_cards(source.id, candidates, spans)
    cards = [c for c, _ in cards_and_reducers]
    reducers = [r for _, r in cards_and_reducers]

    print("=== Extracted taste bundle ===\n")
    print(f"  source: {source.id}")
    print(f"  spans:  {len(spans)}")
    print(f"  candidates (untrusted): {len(candidates)}")
    print(f"  taste cards (after reducer): {len(cards)}")
    print()

    print("=== Taste cards (current as of 2026-05-15T18:00:00Z) ===\n")
    current = current_taste_cards([asdict(c) for c in cards], "2026-05-15T18:00:00Z")
    for c in current:
        principle = c["principle"]
        strength = c["strength"]
        domain = c["domain"]
        pos = len(c["positive_example_span_ids"])
        neg = len(c["negative_example_span_ids"])
        con = len(c["contrast_pairs"])
        print(f"  [{domain}] ({strength}) {principle}")
        print(f"    -> {pos} positive, {neg} negative, {con} contrast pair(s)")
    print()

    # Validate the full bundle (sources + spans + reducers + cards).
    validate_taste_bundle(
        source_records=[asdict(source)],
        episode_records=[],
        evidence_spans=[asdict(s) for s in spans],
        candidate_records=[asdict(c) for c in candidates],
        taste_reducer_decisions=[asdict(r) for r in reducers],
        taste_cards=[asdict(c) for c in cards],
    )
    print("Bundle validated. Cross-plane references resolve;")
    print("reducer decisions authorize the taste cards.")


if __name__ == "__main__":
    main()
