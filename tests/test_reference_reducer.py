"""Tests for examples/reference_reducer.py.

These tests exercise the public reducer
``reduce_candidates_to_trusted_memory`` end-to-end, including the
three rejection reasons the reference reducer produces
(``no_evidence``, ``low_confidence``, ``stale``) and the
"target_ledger_entry_ids" invariant the library's bundle
validator enforces.

They also verify the example runs cleanly as a script -- the
script is the documentation; if the example ever drifts from
the contract, the script test will fail before users do.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from dataclasses import asdict
from pathlib import Path

from agent_memory_contracts import (
    CandidateTasteSignal,
    MemoryReducerDecision,
    PreferenceLedgerEntry,
    make_candidate_id,
)

from examples.reference_reducer import (
    REJECT_LOW_CONFIDENCE,
    REJECT_NO_EVIDENCE,
    reduce_candidates_to_trusted_memory,
    _build_preference_entry,
    _build_source_and_spans,
    _claim_candidate,
    _taste_candidate,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


def _build_ungrounded_candidate(source_id: str) -> CandidateTasteSignal:
    """A CandidateTasteSignal with empty evidence_span_ids.

    Bypasses ``from_dict`` validation -- the contract requires at
    least one evidence_span_id, so building one with an empty
    list is the failure mode the reducer is designed to catch.
    """
    cid = make_candidate_id("taste_signal", [], {
        "domain": "architecture",
        "signal_kind": "principle",
        "taste_text": "No-evidence principle",
        "example_span_ids": [],
        "contrast_span_ids": [],
        "strength_hint": "strong",
    })
    return CandidateTasteSignal(
        id=cid,
        schema_version="1.0.0",
        candidate_type="taste_signal",
        source_record_ids=[source_id],
        episode_record_ids=[],
        evidence_span_ids=[],
        natural_language_summary="No-evidence principle",
        extracted_by={"agent": "ext", "model": "gpt", "tool": None, "prompt_ref": None},
        extracted_at="2026-05-30T12:30:00Z",
        confidence="high",
        risk_class="low",
        status="candidate",
        review={"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        metadata={},
        domain="architecture",
        signal_kind="principle",
        taste_text="No-evidence principle",
        example_span_ids=[],
        contrast_span_ids=[],
        strength_hint="strong",
    )


class HappyPathTests(unittest.TestCase):
    def test_happy_path_produces_authorized_ledger_entries(self):
        source, spans = _build_source_and_spans()
        candidates = [
            _taste_candidate(source.id, spans[0].id,
                             taste_text="Hard-constraint preference for memory architecture."),
            _taste_candidate(source.id, spans[1].id,
                             taste_text="TasteCards must anchor principles in examples."),
            _claim_candidate(source.id, spans[2].id,
                             claim_text="The user prefers hard constraints.",
                             subject="user", predicate="prefers", obj="hard constraints"),
        ]
        result = reduce_candidates_to_trusted_memory(candidates, spans, [source])
        # All three pass; all three promoted.
        self.assertEqual(len(result.ledger_entries), 3)
        self.assertGreaterEqual(len(result.decisions), 1)
        self.assertEqual(result.rejected, [])
        # Library validator must accept the bundle -- this is the
        # authorization invariant.
        validate_ledger_bundle_local(result, source, spans, candidates)

    def test_rejection_for_missing_evidence(self):
        source, spans = _build_source_and_spans()
        ungrounded = _build_ungrounded_candidate(source.id)
        result = reduce_candidates_to_trusted_memory([ungrounded], spans, [source])
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(result.rejected[0].reason_code, REJECT_NO_EVIDENCE)
        self.assertEqual(result.rejected[0].candidate_id, ungrounded.id)

    def test_rejection_for_low_confidence(self):
        source, spans = _build_source_and_spans()
        low_conf = _taste_candidate(
            source.id, spans[0].id,
            taste_text="A weak guess.", confidence="low",
        )
        result = reduce_candidates_to_trusted_memory(
            [low_conf], spans, [source], min_confidence="medium",
        )
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(result.rejected[0].reason_code, REJECT_LOW_CONFIDENCE)
        self.assertEqual(result.rejected[0].candidate_id, low_conf.id)

    def test_partial_promotion_keeps_promoted_valid(self):
        source, spans = _build_source_and_spans()
        # 1 ungrounded + 2 clean
        ungrounded = _build_ungrounded_candidate(source.id)
        cand_b = _taste_candidate(source.id, spans[0].id,
                                  taste_text="Promoted candidate 1.")
        cand_c = _claim_candidate(source.id, spans[1].id,
                                  claim_text="Promoted candidate 2.",
                                  subject="team", predicate="uses", obj="TasteCards")
        candidates = [ungrounded, cand_b, cand_c]
        result = reduce_candidates_to_trusted_memory(candidates, spans, [source])
        self.assertEqual(len(result.rejected), 1)
        self.assertEqual(len(result.ledger_entries), 2)
        # The promoted subset must validate clean.
        promoted_ids = {r.candidate_id for r in result.rejected}
        promoted_candidates = [c for c in candidates if c.id not in promoted_ids]
        validate_ledger_bundle_local(result, source, spans, promoted_candidates)

    def test_decision_must_authorize_ledger_entry(self):
        """Mirrors Scenario C: a reducer that doesn't list the entry id
        in target_ledger_entry_ids is rejected by the library."""
        from agent_memory_contracts import (
            make_reducer_decision_id,
            validate_ledger_bundle,
        )
        source, spans = _build_source_and_spans()
        span = spans[0]
        candidate = _taste_candidate(source.id, span.id,
                                     taste_text="Orphan-candidate for the auth test.")
        ledger = _build_preference_entry(
            subject="memory architecture",
            preference_text=candidate.taste_text,
            domain="architecture",
            strength="hard_constraint",
            confidence="high",
            scope="global",
            source_id=source.id,
            span_id=span.id,
            decided_at="2026-05-30T13:00:00Z",
            candidate_id=candidate.id,
        )
        # Build a reducer that targets a fake (non-existent) ledger id
        # instead of the one we just built.
        wrong_target = "pref_" + "f" * 24
        rationale = "broken link"
        reducer_id = make_reducer_decision_id(
            "promote", [candidate.id], [wrong_target], [span.id], rationale,
        )
        reducer = MemoryReducerDecision.from_dict({
            "id": reducer_id,
            "schema_version": "1.0.0",
            "decision_type": "promote",
            "target_candidate_ids": [candidate.id],
            "target_ledger_entry_ids": [wrong_target],
            "evidence_span_ids": [span.id],
            "rationale": rationale,
            "decided_by": {"agent": "test", "model": "gpt", "tool": None, "prompt_ref": None},
            "decided_at": "2026-05-30T13:00:00Z",
            "confidence": "high",
            "risk_class": "low",
            "checks": {"provenance": "pass", "temporal_validity": "pass",
                       "contradiction_scan": "pass", "privacy": "pass", "usefulness": "pass"},
            "metadata": {},
        })
        # Patch the ledger entry to point at the reducer (so the
        # reverse direction is consistent), then the forward
        # direction (reducer -> entry) is the broken one.
        entry_dict = asdict(ledger)
        entry_dict["reducer_decision_id"] = reducer_id
        entry = PreferenceLedgerEntry.from_dict(entry_dict)
        with self.assertRaises(ValueError):
            validate_ledger_bundle(
                source_records=[asdict(source)],
                episode_records=[],
                evidence_spans=[asdict(span)],
                candidate_records=[asdict(candidate)],
                reducer_decisions=[asdict(reducer)],
                ledger_entries=[asdict(entry)],
            )


class ScriptSmokeTests(unittest.TestCase):
    def test_example_runs_as_script(self):
        env = {**os.environ, "PYTHONPATH": "src"}
        proc = subprocess.run(
            [sys.executable, "examples/reference_reducer.py"],
            env=env, cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(
            proc.returncode, 0,
            msg=f"reference_reducer.py exited {proc.returncode}\n"
                f"stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}",
        )
        # All three scenarios must have printed.
        self.assertIn("Scenario A", proc.stdout)
        self.assertIn("Scenario B", proc.stdout)
        self.assertIn("Scenario C", proc.stdout)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def validate_ledger_bundle_local(result, source, spans, candidates) -> None:
    """Wrap validate_ledger_bundle so the test bodies stay short."""
    from agent_memory_contracts import validate_ledger_bundle
    validate_ledger_bundle(
        source_records=[asdict(source)],
        episode_records=[],
        evidence_spans=[asdict(s) for s in spans],
        candidate_records=[asdict(c) for c in candidates],
        reducer_decisions=[asdict(d) for d in result.decisions],
        ledger_entries=[asdict(e) for e in result.ledger_entries],
    )


if __name__ == "__main__":
    unittest.main()
