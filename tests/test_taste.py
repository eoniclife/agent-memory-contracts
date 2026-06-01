"""Tests for the taste plane: TasteCard, TasteReducerDecision,
current_taste_cards, taste_cards_as_of, taste_supersession_chain."""

from __future__ import annotations

import unittest
from dataclasses import asdict

from agent_memory_contracts import (
    TasteCard,
    TasteReducerDecision,
    current_taste_cards,
    is_taste_card_active_at,
    make_taste_card_id,
    make_taste_reducer_decision_id,
    taste_card_from_dict,
    taste_cards_as_of,
    taste_supersession_chain,
    validate_taste_bundle,
)

from .fixtures import T_DECIDED, build_source_and_span


def _card(source_id: str, span_id: str, principle: str, strength: str = "hard_constraint") -> TasteCard:
    # The id is content-derived from the same fields the validator uses
    # in semantic_payload(): subject, domain, scope, project_refs (sorted),
    # principle, taste_kind, valid_from, evidence_span_ids (sorted).
    # Note: the original kernel design redundantly carries evidence_span_ids
    # both as a top-level arg and inside the normalized payload -- both go
    # into the hash, so we have to mirror that.
    card_id = make_taste_card_id([span_id], {
        "subject": "memory architecture",
        "domain": "architecture",
        "scope": "global",
        "project_refs": [],
        "principle": principle,
        "taste_kind": "principle",
        "valid_from": T_DECIDED,
        "evidence_span_ids": [span_id],
    })
    return TasteCard.from_dict({
        "id": card_id,
        "schema_version": "1.0.0",
        "card_type": "taste_card",
        "status": "active",
        "subject": "memory architecture",
        "domain": "architecture",
        "scope": "global",
        "project_refs": [],
        "principle": principle,
        "rationale": "Observed across multiple design discussions.",
        "strength": strength,
        "confidence": "high",
        "taste_kind": "principle",
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "candidate_taste_signal_ids": [],
        "positive_example_span_ids": [span_id],
        "negative_example_span_ids": [],
        "contrast_pairs": [],
        "objection_patterns": [],
        "application_notes": [],
        "reducer_decision_id": "redtaste_" + "a" * 24,
        "observed_at": "2026-05-30T12:00:00Z",
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


def _reducer_for_card(card: TasteCard, span_id: str) -> TasteReducerDecision:
    rid = make_taste_reducer_decision_id(
        "promote", [], [card.id], [span_id], "ok"
    )
    return TasteReducerDecision.from_dict({
        "id": rid,
        "schema_version": "1.0.0",
        "decision_type": "promote",
        "target_taste_signal_ids": [],
        "target_taste_card_ids": [card.id],
        "evidence_span_ids": [span_id],
        "rationale": "ok",
        "decided_by": {"agent": "taste-reducer", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
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


class TasteIdTests(unittest.TestCase):
    def test_id_is_content_derived(self):
        a = make_taste_card_id(["span_aaa"], {"principle": "x"})
        b = make_taste_card_id(["span_aaa"], {"principle": "x"})
        c = make_taste_card_id(["span_bbb"], {"principle": "x"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("taste_"))


class TasteCardTests(unittest.TestCase):
    def test_card_requires_evidence_anchor(self):
        source, span = build_source_and_span()
        card = _card(source.id, span.id, "Spec-text drift > no spec")
        self.assertIn(span.id, card.evidence_span_ids)

    def test_card_rejects_missing_example_grounding(self):
        # Without positive, negative, or contrast pairs, validation fails
        source, span = build_source_and_span()
        card = _card(source.id, span.id, "Spec-text drift > no spec")
        d = asdict(card)
        d["positive_example_span_ids"] = []
        d["negative_example_span_ids"] = []
        d["contrast_pairs"] = []
        with self.assertRaises(ValueError):
            taste_card_from_dict(d)

    def test_superseded_card_requires_superseded_by(self):
        source, span = build_source_and_span()
        card = _card(source.id, span.id, "x")
        d = asdict(card)
        d["status"] = "superseded"
        # superseded_by is still empty -> should fail
        with self.assertRaises(ValueError):
            taste_card_from_dict(d)

    def test_active_card_forbids_superseded_by(self):
        source, span = build_source_and_span()
        card = _card(source.id, span.id, "x")
        d = asdict(card)
        d["superseded_by"] = [make_taste_card_id([span.id], {"principle": "newer"})]
        with self.assertRaises(ValueError):
            taste_card_from_dict(d)


class TasteQueryTests(unittest.TestCase):
    def test_current_taste_cards_filters_by_time(self):
        source, span = build_source_and_span()
        c1 = _card(source.id, span.id, "principle A")
        # valid_until must be >= valid_from; set it to a time after the card's
        # valid_from so the card is valid for a while, then query past it.
        d1 = asdict(c1)
        d1["valid_until"] = "2026-05-30T15:00:00Z"  # after valid_from, before query time
        d1["id"] = c1.id  # keep same id (content unchanged for the query)
        c1_past = taste_card_from_dict(d1)
        c2 = _card(source.id, span.id, "principle B")
        current = current_taste_cards([asdict(c1_past), asdict(c2)], "2026-05-30T18:00:00Z")
        self.assertEqual(len(current), 1)
        self.assertEqual(current[0]["principle"], "principle B")

    def test_is_taste_card_active_at(self):
        source, span = build_source_and_span()
        c = _card(source.id, span.id, "x")
        self.assertTrue(is_taste_card_active_at(asdict(c), "2026-05-30T18:00:00Z"))
        self.assertFalse(is_taste_card_active_at(asdict(c), "2026-05-29T18:00:00Z"))  # before valid_from

    def test_taste_cards_as_of_includes_superseded_until_validity(self):
        source, span = build_source_and_span()
        c = _card(source.id, span.id, "x")
        d = asdict(c)
        d["valid_until"] = "2026-06-01T00:00:00Z"
        d["status"] = "superseded"
        d["superseded_by"] = [make_taste_card_id([span.id], {"principle": "newer"})]
        past = taste_card_from_dict(d)
        # Before valid_until, even a superseded card is visible "as of"
        self.assertEqual(len(taste_cards_as_of([asdict(past)], "2026-05-31T00:00:00Z")), 1)
        # After valid_until, it is filtered out
        self.assertEqual(len(taste_cards_as_of([asdict(past)], "2026-06-02T00:00:00Z")), 0)

    def test_supersession_chain(self):
        source, span = build_source_and_span()
        c1 = _card(source.id, span.id, "v1")
        # c2 is the active successor; its id is content-derived from the
        # full semantic payload, so we have to use c2.id, not a separately
        # computed placeholder.
        c2 = _card(source.id, span.id, "v2")
        # c1 is the older card; mark it superseded and link to c2.
        d1 = asdict(c1)
        d1["status"] = "superseded"
        d1["superseded_by"] = [c2.id]
        d1["valid_until"] = "2026-05-30T15:00:00Z"
        c1 = taste_card_from_dict(d1)
        chain = taste_supersession_chain(c1.id, [asdict(c1), asdict(c2)])
        self.assertEqual(chain, [c1.id, c2.id])


class TasteBundleTests(unittest.TestCase):
    def test_taste_bundle_validates(self):
        source, span = build_source_and_span()
        card = _card(source.id, span.id, "Spec drift > no spec")
        # Compute the reducer id from the card id and span id, then
        # attach it to both the reducer dict and the card's
        # reducer_decision_id so the bundle validator can find it.
        rid = make_taste_reducer_decision_id(
            "promote", [], [card.id], [span.id], "ok"
        )
        card_dict = asdict(card)
        card_dict["reducer_decision_id"] = rid
        reducer_dict = {
            "id": rid,
            "schema_version": "1.0.0",
            "decision_type": "promote",
            "target_taste_signal_ids": [],
            "target_taste_card_ids": [card.id],
            "evidence_span_ids": [span.id],
            "rationale": "ok",
            "decided_by": {"agent": "taste-reducer", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
            "decided_at": T_DECIDED,
            "confidence": "high",
            "risk_class": "low",
            "checks": {
                "provenance": "pass", "specificity": "pass",
                "example_grounding": "pass", "contrast_grounding": "pass",
                "privacy": "pass", "usefulness": "pass",
            },
            "metadata": {},
        }
        validate_taste_bundle(
            source_records=[asdict(source)],
            episode_records=[],
            evidence_spans=[asdict(span)],
            candidate_records=[],
            taste_reducer_decisions=[reducer_dict],
            taste_cards=[card_dict],
        )

    def test_taste_bundle_rejects_card_not_in_reducer_target(self):
        source, span = build_source_and_span()
        card = _card(source.id, span.id, "x")
        # Build a reducer with empty target_taste_card_ids
        rid = make_taste_reducer_decision_id(
            "promote", [], [], [span.id], "ok"
        )
        reducer_dict = {
            "id": rid,
            "schema_version": "1.0.0",
            "decision_type": "promote",
            "target_taste_signal_ids": [],
            "target_taste_card_ids": [],  # does NOT include card.id
            "evidence_span_ids": [span.id],
            "rationale": "ok",
            "decided_by": {"agent": "taste-reducer", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
            "decided_at": T_DECIDED,
            "confidence": "high",
            "risk_class": "low",
            "checks": {
                "provenance": "pass", "specificity": "pass",
                "example_grounding": "pass", "contrast_grounding": "pass",
                "privacy": "pass", "usefulness": "pass",
            },
            "metadata": {},
        }
        with self.assertRaises(ValueError):
            validate_taste_bundle(
                source_records=[asdict(source)],
                episode_records=[],
                evidence_spans=[asdict(span)],
                candidate_records=[],
                taste_reducer_decisions=[reducer_dict],
                taste_cards=[asdict(card)],
            )


if __name__ == "__main__":
    unittest.main()
