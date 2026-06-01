"""Tests for the candidate plane: CandidateClaim, CandidatePreference,
CandidateDecision, CandidateTask, CandidateTasteSignal."""

from __future__ import annotations

import unittest

from agent_memory_contracts import (
    CandidateClaim,
    CandidatePreference,
    CandidateTasteSignal,
    make_candidate_id,
)

from .fixtures import build_source_and_span, T_EXTRACTED


class CandidateIdTests(unittest.TestCase):
    def test_id_is_content_derived_and_prefixed_by_type(self):
        a = make_candidate_id("claim", ["span_aaa"], {"claim_text": "x"})
        b = make_candidate_id("claim", ["span_aaa"], {"claim_text": "x"})
        c = make_candidate_id("claim", ["span_bbb"], {"claim_text": "x"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("cand_claim_"))
        # different types have different prefixes
        pref = make_candidate_id("preference", ["span_aaa"], {"preference_text": "x"})
        self.assertTrue(pref.startswith("cand_pref_"))

    def test_unsupported_type_raises(self):
        with self.assertRaises(ValueError):
            make_candidate_id("bogus_type", ["span_aaa"], {})


class CandidateClaimTests(unittest.TestCase):
    def test_claim_validates(self):
        _, span = build_source_and_span()
        claim_id = make_candidate_id("claim", [span.id], {
            "subject": "memory architecture",
            "predicate": "prefers",
            "object": "hard constraints",
            "claim_text": "The user prefers hard constraints on memory architecture",
            "claim_scope": "global",
            "temporal_hint": {
                "observed_at": None,
                "asserted_at": None,
                "valid_from_hint": None,
                "valid_until_hint": None,
            },
        })
        claim = CandidateClaim.from_dict({
            "id": claim_id,
            "schema_version": "1.0.0",
            "candidate_type": "claim",
            "source_record_ids": [],
            "episode_record_ids": [],
            "evidence_span_ids": [span.id],
            "natural_language_summary": "User expressed hard-constraint preference for memory architecture",
            "extracted_by": {"agent": "ext", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
            "extracted_at": T_EXTRACTED,
            "confidence": "high",
            "risk_class": "low",
            "status": "candidate",
            "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
            "metadata": {},
            "subject": "memory architecture",
            "predicate": "prefers",
            "object": "hard constraints",
            "claim_text": "The user prefers hard constraints on memory architecture",
            "claim_scope": "global",
            "temporal_hint": {
                "observed_at": None,
                "asserted_at": None,
                "valid_from_hint": None,
                "valid_until_hint": None,
            },
        })
        self.assertEqual(claim.claim_scope, "global")

    def test_claim_rejects_candidate_only_field_leakage_to_metadata(self):
        _, span = build_source_and_span()
        claim_id = make_candidate_id("claim", [span.id], {
            "subject": "x", "predicate": "x", "object": "x", "claim_text": "x",
            "claim_scope": "global",
            "temporal_hint": {
                "observed_at": None, "asserted_at": None,
                "valid_from_hint": None, "valid_until_hint": None,
            },
        })
        with self.assertRaises(ValueError):
            CandidateClaim.from_dict({
                "id": claim_id,
                "schema_version": "1.0.0",
                "candidate_type": "claim",
                "source_record_ids": [],
                "episode_record_ids": [],
                "evidence_span_ids": [span.id],
                "natural_language_summary": "x",
                "extracted_by": {"agent": "ext", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
                "extracted_at": T_EXTRACTED,
                "confidence": "high",
                "risk_class": "low",
                "status": "candidate",
                "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
                "metadata": {"fact_id": "fact_xyz"},  # FORBIDDEN
                "subject": "x", "predicate": "x", "object": "x", "claim_text": "x",
                "claim_scope": "global",
                "temporal_hint": {
                    "observed_at": None, "asserted_at": None,
                    "valid_from_hint": None, "valid_until_hint": None,
                },
            })


class CandidatePreferenceTests(unittest.TestCase):
    def test_preference_validates(self):
        _, span = build_source_and_span()
        pref_id = make_candidate_id("preference", [span.id], {
            "subject": "memory architecture",
            "preference_text": "Spec-text drift is worse than no spec",
            "domain": "architecture",
            "scope": "project_specific",
            "strength_hint": "strong",
            "counterevidence_span_ids": [],
        })
        pref = CandidatePreference.from_dict({
            "id": pref_id,
            "schema_version": "1.0.0",
            "candidate_type": "preference",
            "source_record_ids": [],
            "episode_record_ids": [],
            "evidence_span_ids": [span.id],
            "natural_language_summary": "Strong preference against spec drift",
            "extracted_by": {"agent": "ext", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
            "extracted_at": T_EXTRACTED,
            "confidence": "high",
            "risk_class": "low",
            "status": "candidate",
            "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
            "metadata": {},
            "subject": "memory architecture",
            "preference_text": "Spec-text drift is worse than no spec",
            "domain": "architecture",
            "scope": "project_specific",
            "strength_hint": "strong",
            "counterevidence_span_ids": [],
        })
        self.assertEqual(pref.strength_hint, "strong")


class CandidateTasteSignalTests(unittest.TestCase):
    def test_taste_signal_requires_span_anchor(self):
        # Either example_span_ids or contrast_span_ids must be non-empty.
        _, _ = build_source_and_span()
        bad_id = make_candidate_id("taste_signal", ["span_x"], {
            "domain": "architecture",
            "signal_kind": "principle",
            "taste_text": "x",
            "example_span_ids": [],
            "contrast_span_ids": [],
            "strength_hint": "weak",
        })
        with self.assertRaises(ValueError):
            CandidateTasteSignal.from_dict({
                "id": bad_id,
                "schema_version": "1.0.0",
                "candidate_type": "taste_signal",
                "source_record_ids": [],
                "episode_record_ids": [],
                "evidence_span_ids": ["span_x"],
                "natural_language_summary": "x",
                "extracted_by": {"agent": "ext", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
                "extracted_at": T_EXTRACTED,
                "confidence": "high",
                "risk_class": "low",
                "status": "candidate",
                "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
                "metadata": {},
                "domain": "architecture",
                "signal_kind": "principle",
                "taste_text": "x",
                "example_span_ids": [],
                "contrast_span_ids": [],
                "strength_hint": "weak",
            })


if __name__ == "__main__":
    unittest.main()
