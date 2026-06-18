# INVARIANT 5 — Grounded or refused.
#
# Product language: "The assistant never states a fact it cannot
# cite, and when its trusted memory cannot support an answer it
# says 'I don't know' — and shows you the unreviewed candidates
# that might. A poisoned document can reach the review queue; it
# can never reach an answer."
#
# Executable form: every sentence of every answer carries a
# citation that resolves to a pack fact (checked mechanically, not
# by prompt discipline); empty or below-threshold support yields a
# structured refusal with related candidate ids; the poison
# candidate's content never appears in any answer text across a
# battery of questions.

from __future__ import annotations

import re
import unittest

from agent_memory_contracts.runtime import grounding
from agent_memory_contracts.runtime.gate import MemoryGate
from agent_memory_contracts.runtime.store import MemoryStore

from ..runtime_seed import T_CREATED, build_universe, seed_via_gate

#: A battery of questions, on- and off-topic, including ones that
#: aim straight at the poisoned content.
QUESTION_BATTERY = [
    "what is the deploy cadence?",
    "what is the error budget?",
    "is the error budget 50 percent?",
    "tell me about the API error budget",
    "what colour is the bikeshed painted?",
    "summarize the deployment policy",
    "what is it?",
]

#: The poison's distinctive content (forged claim text tokens that
#: appear in NO trusted fact).
POISON_MARKERS = ("50 percent", "50% (forged)", "forged")


class GroundedOrRefusedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.universe = build_universe()
        cls.store = MemoryStore()
        cls.gate = MemoryGate(cls.store)
        seed_via_gate(cls.gate, cls.universe)
        cls.results = [
            grounding.answer(cls.store, question=question,
                             created_at=T_CREATED)
            for question in QUESTION_BATTERY
        ]

    @classmethod
    def tearDownClass(cls):
        cls.store.close()

    def test_no_uncited_factual_sentence_ever(self):
        answered = [r for r in self.results if r.status == "answered"]
        self.assertGreaterEqual(len(answered), 2)
        for result in answered:
            violations = grounding.verify_grounding(
                result.answer_text, len(result.citations))
            self.assertEqual(violations, (),
                             f"{result.question!r}: {violations}")
            # Every citation resolves to a real ledger entry with
            # real evidence.
            for citation in result.citations:
                entry = self.store.get_ledger_entry(citation.fact_id)
                self.assertIsNotNone(entry)
                self.assertTrue(citation.span_ids)
                for span_id in citation.span_ids:
                    self.assertIsNotNone(self.store.get_span(span_id))

    def test_every_question_is_answered_or_refused_never_neither(self):
        for result in self.results:
            self.assertIn(result.status, ("answered", "refused"))
            if result.status == "refused":
                self.assertIsNone(result.answer_text)
                self.assertEqual(result.reason, "not_in_trusted_memory")
            else:
                self.assertIsNone(result.reason)
                self.assertTrue(result.citations)

    def test_empty_support_refuses(self):
        empty = MemoryStore()
        result = grounding.answer(empty, question=QUESTION_BATTERY[0],
                                  created_at=T_CREATED)
        self.assertEqual(result.status, "refused")
        self.assertEqual(result.reason, "not_in_trusted_memory")
        empty.close()

    def test_refusal_surfaces_unreviewed_candidates(self):
        # Candidates ingested, nothing promoted: the refusal names
        # the unreviewed candidates that might answer.
        pending = MemoryStore()
        pending_gate = MemoryGate(pending)
        universe = build_universe()
        pending_gate.submit_source(universe.source, created_at=T_CREATED)
        for span in universe.spans:
            pending_gate.submit_span(span, created_at=T_CREATED)
        for candidate in universe.candidates.values():
            pending_gate.submit_candidate(candidate, created_at=T_CREATED)
        result = grounding.answer(pending,
                                  question="what is the deploy cadence?",
                                  created_at=T_CREATED)
        self.assertEqual(result.status, "refused")
        self.assertTrue(result.related_candidate_ids)
        pending.close()

    def test_poisoned_content_never_appears_in_any_answer(self):
        poison_id = str(self.universe.candidates["poison"]["id"])
        for result in self.results:
            text = result.answer_text or ""
            for marker in POISON_MARKERS:
                self.assertNotIn(marker, text,
                                 f"poison surfaced for "
                                 f"{result.question!r}")
            # ... and the quarantined candidate is never suggested
            # for review either: it WAS reviewed, and rejected.
            self.assertNotIn(poison_id, result.related_candidate_ids)
        # The trusted answer to the poisoned subject is the 1%
        # fact, cited.
        budget_answers = [r for r in self.results
                          if "error budget" in r.question
                          and r.status == "answered"]
        self.assertTrue(budget_answers)
        for result in budget_answers:
            self.assertRegex(result.answer_text, r"1%")
            self.assertNotRegex(result.answer_text,
                                r"(?<!\d)50\s*(%|percent)")

    def test_the_checker_is_not_a_tautology(self):
        # Adversarial control: hand-built uncited or miscited text
        # is caught by the same mechanical check the answers pass.
        self.assertNotEqual(grounding.verify_grounding(
            "The deploy cadence is daily.", 1), ())
        self.assertNotEqual(grounding.verify_grounding(
            "The deploy cadence is daily [F3].", 1), ())
        self.assertNotEqual(grounding.verify_grounding(
            "A cited claim [F1]. An uncited rider.", 1), ())
        self.assertEqual(grounding.verify_grounding(
            "A cited claim [F1].", 1), ())

    def test_refusals_are_persisted_as_first_class_records(self):
        # Refusing is an auditable act: every refusal lands in the
        # answers table with the pack fingerprint it was judged
        # against.
        refused = [r for r in self.results if r.status == "refused"]
        self.assertTrue(refused)
        for result in refused:
            stored = self.store.get_answer(result.answer_id)
            self.assertIsNotNone(stored)
            self.assertEqual(stored["status"], "refused")
            self.assertEqual(stored["reason"], "not_in_trusted_memory")


if __name__ == "__main__":
    unittest.main()
