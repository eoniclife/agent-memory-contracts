"""Tests for agent_memory_contracts.runtime.grounding.

ADR-7 (deterministic, receipted pack builds: filters, ranking with
logged weights, token budget, candidate segregation, persistence)
and ADR-8 (cite-or-refuse: the mechanical grounding check, the
structured refusal with related candidates, answer persistence,
time travel). The cross-cutting invariants live in
tests/invariants/.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_memory_contracts import (
    make_candidate_id,
    make_span_id,
)
from agent_memory_contracts.runtime import grounding
from agent_memory_contracts.runtime.anchors import (
    verify_chain,
    verify_coverage,
)
from agent_memory_contracts.runtime.gate import MemoryGate
from agent_memory_contracts.runtime.store import MemoryStore

from .runtime_seed import (
    T1,
    T2,
    T_CREATED,
    _decision,
    _fact,
    build_universe,
    seed_via_gate,
)

QUESTION = "what is the deploy cadence?"


class _GroundedCase(unittest.TestCase):
    def setUp(self):
        self.universe = build_universe()
        self.store = MemoryStore()
        self.gate = MemoryGate(self.store)
        seed_via_gate(self.gate, self.universe)

    def tearDown(self):
        self.store.close()


class PackBuildTests(_GroundedCase):
    def test_pack_is_deterministic_and_receipted(self):
        first = grounding.build_context_pack(self.store, task=QUESTION,
                                             created_at=T_CREATED)
        second = grounding.build_context_pack(self.store, task=QUESTION,
                                              created_at=T_CREATED)
        self.assertEqual(first.pack_id, second.pack_id)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(first.receipt.ranking, second.receipt.ranking)
        # The weights are logged in the receipt (ADR-7), taste
        # stubbed at 0 per OPEN item O-2.
        self.assertIn(("text_match", 0.6), first.receipt.weights)
        self.assertIn(("decision_recency", 0.2), first.receipt.weights)
        self.assertIn(("taste", 0.0), first.receipt.weights)
        self.assertTrue(first.persisted)
        self.assertFalse(second.persisted)  # replay never rewrites
        self.assertEqual(first.compilation.validation_report.status,
                         "pass")

    def test_pack_persists_envelope_and_state_snapshot(self):
        result = grounding.build_context_pack(self.store, task=QUESTION,
                                              created_at=T_CREATED)
        envelope = self.store.get_context_pack(result.pack_id)
        self.assertIsNotNone(envelope)
        self.assertEqual(envelope["fingerprint"], result.fingerprint)
        self.assertEqual(envelope["runtime_receipt"]["selected_entry_ids"],
                         list(result.selected_entry_ids))
        snapshot = self.store.get_state_snapshot(
            result.receipt.state_snapshot_id)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot["state_type"], "project_state")
        self.assertTrue(verify_chain(self.store).ok)
        self.assertTrue(verify_coverage(self.store).ok)

    def test_ranking_prefers_keyword_support(self):
        result = grounding.build_context_pack(self.store, task=QUESTION,
                                              created_at=T_CREATED)
        daily_id = str(self.universe.entries["daily"]["id"])
        self.assertEqual(result.receipt.ranking[0].entry_id, daily_id)
        self.assertGreater(result.receipt.ranking[0].text_score, 0.5)

    def test_time_travel_pack_selects_the_truth_of_that_moment(self):
        weekly_id = str(self.universe.entries["weekly"]["id"])
        daily_id = str(self.universe.entries["daily"]["id"])
        before = grounding.build_context_pack(
            self.store, task=QUESTION, as_of="2026-06-01T12:00:00Z",
            created_at=T_CREATED)
        self.assertIn(weekly_id, before.selected_entry_ids)
        self.assertNotIn(daily_id, before.selected_entry_ids)
        after = grounding.build_context_pack(self.store, task=QUESTION,
                                             as_of=T2, created_at=T_CREATED)
        self.assertIn(daily_id, after.selected_entry_ids)
        self.assertNotIn(weekly_id, after.selected_entry_ids)
        self.assertNotEqual(before.fingerprint, after.fingerprint)

    def test_scope_subjects_filter(self):
        result = grounding.build_context_pack(
            self.store, task=QUESTION,
            scope_subjects=["slo.error_budget"], created_at=T_CREATED)
        self.assertEqual(result.selected_entry_ids,
                         (str(self.universe.entries["budget"]["id"]),))

    def test_token_budget_limits_selection(self):
        # Budget exactly the top-ranked entry's cost: only it fits.
        daily = self.store.get_ledger_entry(
            str(self.universe.entries["daily"]["id"]))
        cost = max(len(MemoryStore._entry_text(daily))
                   // grounding._CHARS_PER_TOKEN, 1)
        result = grounding.build_context_pack(
            self.store, task=QUESTION, token_budget=cost,
            created_at=T_CREATED)
        self.assertEqual(result.selected_entry_ids,
                         (str(self.universe.entries["daily"]["id"]),))

    def test_candidates_are_segregated_never_trusted(self):
        result = grounding.build_context_pack(
            self.store, task=QUESTION, include_candidates=True,
            created_at=T_CREATED)
        self.assertTrue(result.candidate_ids_unverified)
        pack = result.compilation.context_pack
        trusted = (set(pack.trusted_memory["fact_ids"])
                   | set(pack.trusted_memory["preference_ids"])
                   | set(pack.trusted_memory["decision_ids"]))
        for candidate_id in result.candidate_ids_unverified:
            self.assertNotIn(candidate_id, trusted)
            self.assertIn(candidate_id,
                          pack.candidate_context["candidate_claim_ids"])

    def test_empty_store_builds_empty_deterministic_result(self):
        empty = MemoryStore()
        result = grounding.build_context_pack(empty, task=QUESTION,
                                              created_at=T_CREATED)
        self.assertIsNone(result.compilation)
        self.assertIsNone(result.pack_id)
        self.assertEqual(result.selected_entry_ids, ())
        again = grounding.build_context_pack(empty, task=QUESTION,
                                             created_at=T_CREATED)
        self.assertEqual(result.fingerprint, again.fingerprint)
        self.assertEqual(empty.counts()["context_packs"], 0)
        empty.close()

    def test_persist_false_writes_nothing(self):
        before = self.store.counts()
        grounding.build_context_pack(self.store, task=QUESTION,
                                     persist=False, created_at=T_CREATED)
        self.assertEqual(self.store.counts(), before)


class PrivacyClearanceTests(unittest.TestCase):
    """ADR-13: a low-clearance pack can never contain a high-tier
    fact. Ledger entries carry no privacy_class in the contracts,
    so the tier derives from the cited evidence (fail-closed)."""

    def setUp(self):
        self.universe = build_universe()
        self.store = MemoryStore()
        self.gate = MemoryGate(self.store)
        u = self.universe
        self.gate.submit_source(u.source, created_at=T_CREATED)
        for span in u.spans:
            self.gate.submit_span(span, created_at=T_CREATED)
        # A *private* span and a fact grounded only in it.
        source_id = str(u.source["id"])
        private_span = {
            "id": make_span_id(source_id, "line_range", "40-45"),
            "schema_version": "1.0.0",
            "source_id": source_id,
            "episode_id": None,
            "locator": {"kind": "line_range", "value": "40-45"},
            "text_excerpt": "The acquisition deploy target is secret.",
            "excerpt_policy": "short_quote_allowed",
            "span_hash_sha256": "e" * 64,
            "privacy_class": "private",
            "metadata": {},
        }
        self.gate.submit_span(private_span, created_at=T_CREATED)
        temporal_hint = {"observed_at": None, "asserted_at": None,
                         "valid_from_hint": None, "valid_until_hint": None}
        secret = {
            "id": make_candidate_id("claim", [private_span["id"]], {
                "subject": "deploy.secret_target", "predicate": "is",
                "object": "acquisition cutover",
                "claim_text": "The secret deploy target is the "
                              "acquisition cutover.",
                "claim_scope": "project", "temporal_hint": temporal_hint}),
            "schema_version": "1.0.0",
            "candidate_type": "claim",
            "source_record_ids": [source_id],
            "episode_record_ids": [],
            "evidence_span_ids": [private_span["id"]],
            "natural_language_summary": "Secret deploy target.",
            "extracted_by": {"agent": "runtime-test-extractor",
                             "model": "deterministic", "tool": None,
                             "prompt_ref": None},
            "extracted_at": "2026-05-30T12:30:00Z",
            "confidence": "high", "risk_class": "low",
            "status": "candidate",
            "review": {"reviewed_by": None, "reviewed_at": None,
                       "review_notes": None},
            "metadata": {},
            "subject": "deploy.secret_target", "predicate": "is",
            "object": "acquisition cutover",
            "claim_text": "The secret deploy target is the acquisition "
                          "cutover.",
            "claim_scope": "project", "temporal_hint": temporal_hint,
        }
        self.gate.submit_candidate(secret, created_at=T_CREATED)
        entry = _fact(secret, source_id=source_id, decision_id="redmem_x",
                      valid_from=T1)
        decision = _decision("promote", [str(secret["id"])],
                             [str(entry["id"])], [private_span["id"]],
                             "promote the private fact")
        entry["reducer_decision_id"] = str(decision["id"])
        self.gate.promote(decision, [entry], created_at=T_CREATED)
        self.private_entry_id = str(entry["id"])

    def tearDown(self):
        self.store.close()

    def test_low_clearance_pack_never_contains_a_high_tier_fact(self):
        low = grounding.build_context_pack(
            self.store, task="what is the secret deploy target?",
            max_privacy="internal", created_at=T_CREATED)
        self.assertNotIn(self.private_entry_id, low.selected_entry_ids)
        high = grounding.build_context_pack(
            self.store, task="what is the secret deploy target?",
            max_privacy="private", created_at=T_CREATED)
        self.assertIn(self.private_entry_id, high.selected_entry_ids)

    def test_low_clearance_answer_refuses_rather_than_leaks(self):
        result = grounding.answer(
            self.store, question="what is the secret deploy target?",
            max_privacy="internal", created_at=T_CREATED)
        self.assertEqual(result.status, "refused")
        result_high = grounding.answer(
            self.store, question="what is the secret deploy target?",
            max_privacy="private", created_at=T_CREATED)
        self.assertEqual(result_high.status, "answered")
        self.assertIn("[F1]", result_high.answer_text)


class AnswerTests(_GroundedCase):
    def test_answer_cites_every_sentence(self):
        result = grounding.answer(self.store, question=QUESTION,
                                  created_at=T_CREATED)
        self.assertEqual(result.status, "answered")
        self.assertEqual(result.grounding_violations, ())
        self.assertEqual(
            grounding.verify_grounding(result.answer_text,
                                       len(result.citations)), ())
        self.assertEqual(result.citations[0].fact_id,
                         str(self.universe.entries["daily"]["id"]))
        self.assertTrue(result.citations[0].span_ids)

    def test_answer_is_persisted_with_pack_fingerprint(self):
        result = grounding.answer(self.store, question=QUESTION,
                                  created_at=T_CREATED)
        stored = self.store.get_answer(result.answer_id)
        self.assertEqual(stored["status"], "answered")
        self.assertEqual(stored["pack_fingerprint"],
                         result.pack.fingerprint)
        self.assertEqual(stored["answerer_version"],
                         grounding.ANSWERER_VERSION)
        # Idempotent re-answer: no duplicate rows or anchors.
        before = self.store.counts()
        grounding.answer(self.store, question=QUESTION,
                         created_at=T_CREATED)
        self.assertEqual(self.store.counts(), before)
        self.assertTrue(verify_chain(self.store).ok)
        self.assertTrue(verify_coverage(self.store).ok)

    def test_unsupported_question_refuses_structurally(self):
        result = grounding.answer(
            self.store, question="what colour is the bikeshed painted?",
            created_at=T_CREATED)
        self.assertEqual(result.status, "refused")
        self.assertIsNone(result.answer_text)
        self.assertEqual(result.reason, "not_in_trusted_memory")
        stored = self.store.get_answer(result.answer_id)
        self.assertEqual(stored["status"], "refused")

    def test_refusal_surfaces_related_unreviewed_candidates(self):
        # A store with candidates but no promotions: the honest
        # "I don't know, but these unreviewed candidates may be
        # relevant" screen.
        store = MemoryStore()
        gate = MemoryGate(store)
        u = build_universe()
        gate.submit_source(u.source, created_at=T_CREATED)
        for span in u.spans:
            gate.submit_span(span, created_at=T_CREATED)
        for candidate in u.candidates.values():
            gate.submit_candidate(candidate, created_at=T_CREATED)
        result = grounding.answer(store, question=QUESTION,
                                  created_at=T_CREATED)
        self.assertEqual(result.status, "refused")
        self.assertIn(str(u.candidates["weekly"]["id"]),
                      result.related_candidate_ids)
        self.assertIn(str(u.candidates["daily"]["id"]),
                      result.related_candidate_ids)
        store.close()

    def test_rejected_candidates_never_surface_as_related(self):
        result = grounding.answer(
            self.store, question="is the error budget 50 percent?",
            created_at=T_CREATED)
        poison_id = str(self.universe.candidates["poison"]["id"])
        self.assertNotIn(poison_id, result.related_candidate_ids)

    def test_time_travel_answer(self):
        result = grounding.answer(self.store, question=QUESTION,
                                  as_of="2026-06-01T12:00:00Z",
                                  created_at=T_CREATED)
        self.assertEqual(result.status, "answered")
        self.assertIn("weekly", result.answer_text)
        self.assertNotIn("daily", result.answer_text)

    def test_stopword_only_question_refuses(self):
        result = grounding.answer(self.store, question="what is it?",
                                  created_at=T_CREATED)
        self.assertEqual(result.status, "refused")


class GroundingCheckTests(unittest.TestCase):
    """The mechanical cite-or-refuse checker, adversarially."""

    def test_uncited_sentence_is_a_violation(self):
        violations = grounding.verify_grounding(
            "The rate is 12.50% [F1]. Trust me on the rest.", 1)
        self.assertEqual(len(violations), 1)
        self.assertIn("uncited factual sentence", violations[0])

    def test_unresolvable_citation_is_a_violation(self):
        violations = grounding.verify_grounding(
            "The rate is 12.50% [F4].", 2)
        self.assertEqual(len(violations), 1)
        self.assertIn("[F4]", violations[0])

    def test_empty_answer_is_a_violation(self):
        self.assertEqual(grounding.verify_grounding("   ", 3),
                         ("answer text is empty",))

    def test_clean_answer_passes(self):
        self.assertEqual(grounding.verify_grounding(
            "A fact [F1]. Another [F2]! A third [F1][F2]?", 2), ())

    def test_cited_block_cites_every_internal_sentence(self):
        block = grounding._cited_block(
            "First clause ends here. Second clause follows! Third?", 7)
        self.assertEqual(grounding.verify_grounding(block, 7), ())
        self.assertEqual(block.count("[F7]"), 3)


class DeterminismAcrossHandlesTests(unittest.TestCase):
    def test_same_file_fresh_handle_same_fingerprint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.sqlite3"
            store = MemoryStore(path)
            gate = MemoryGate(store)
            seed_via_gate(gate, build_universe())
            first = grounding.build_context_pack(store, task=QUESTION,
                                                 created_at=T_CREATED)
            store.close()
            reopened = MemoryStore(path)
            second = grounding.build_context_pack(reopened, task=QUESTION,
                                                  created_at=T_CREATED)
            self.assertEqual(first.pack_id, second.pack_id)
            self.assertEqual(first.fingerprint, second.fingerprint)
            self.assertFalse(second.persisted)
            reopened.close()


if __name__ == "__main__":
    unittest.main()
