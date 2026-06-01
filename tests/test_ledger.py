"""Tests for the ledger plane: FactLedgerEntry, PreferenceLedgerEntry,
DecisionLedgerEntry, MemoryReducerDecision, and validate_ledger_bundle."""

from __future__ import annotations

import unittest
from dataclasses import asdict

from agent_memory_contracts import (
    DecisionLedgerEntry,
    FactLedgerEntry,
    MemoryReducerDecision,
    PreferenceLedgerEntry,
    ledger_entry_from_dict,
    make_ledger_entry_id,
    make_reducer_decision_id,
    reducer_decision_from_dict,
    validate_ledger_bundle,
)

from .fixtures import T_DECIDED, T_EXTRACTED, as_dicts, build_source_and_span


def _build_candidate_record(span_id: str) -> dict:
    """A pre-built candidate dict that a reducer can target."""
    return {
        "id": "cand_taste_" + "a" * 24,
        "schema_version": "1.0.0",
        "candidate_type": "taste_signal",
        "source_record_ids": [],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": "Strong principle",
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
        "example_span_ids": [span_id],
        "contrast_span_ids": [],
        "strength_hint": "strong",
    }


def _reducer(target_candidate_ids: list[str], target_ledger_ids: list[str], span_ids: list[str], rationale: str = "ok") -> MemoryReducerDecision:
    reducer_id = make_reducer_decision_id("promote", target_candidate_ids, target_ledger_ids, span_ids, rationale)
    return MemoryReducerDecision.from_dict({
        "id": reducer_id,
        "schema_version": "1.0.0",
        "decision_type": "promote",
        "target_candidate_ids": target_candidate_ids,
        "target_ledger_entry_ids": target_ledger_ids,
        "evidence_span_ids": span_ids,
        "rationale": rationale,
        "decided_by": {"agent": "reducer", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
        "decided_at": T_DECIDED,
        "confidence": "high",
        "risk_class": "low",
        "checks": {
            "provenance": "pass", "temporal_validity": "pass",
            "contradiction_scan": "pass", "privacy": "pass", "usefulness": "pass",
        },
        "metadata": {},
    })


def _preference_entry(source_id: str, span_id: str, candidate_id: str, reducer_id: str) -> PreferenceLedgerEntry:
    entry_id = make_ledger_entry_id("preference", [span_id], {
        "ledger_type": "preference", "subject": "memory architecture",
        "preference_text": "Spec-text drift is worse than no spec",
        "domain": "architecture", "scope": "global",
        "valid_from": T_DECIDED, "evidence_span_ids": [span_id],
    })
    return PreferenceLedgerEntry.from_dict({
        "id": entry_id,
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
        "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED,
        "valid_until": None,
        "stale_after": None,
        "created_at": T_DECIDED,
        "updated_at": T_DECIDED,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {},
    })


class LedgerIdTests(unittest.TestCase):
    def test_id_prefix_matches_ledger_type(self):
        a = make_ledger_entry_id("fact", ["span_x"], {"k": "v"})
        b = make_ledger_entry_id("preference", ["span_x"], {"k": "v"})
        c = make_ledger_entry_id("decision", ["span_x"], {"k": "v"})
        self.assertTrue(a.startswith("fact_"))
        self.assertTrue(b.startswith("pref_"))
        self.assertTrue(c.startswith("dec_"))

    def test_unsupported_ledger_type_raises(self):
        with self.assertRaises(ValueError):
            make_ledger_entry_id("bogus", ["span_x"], {})


class LedgerEntryTests(unittest.TestCase):
    def test_preference_entry_validates(self):
        source, span = build_source_and_span()
        reducer = _reducer(["cand_taste_aaa"], [], [span.id])
        entry = _preference_entry(source.id, span.id, "cand_taste_aaa", reducer.id)
        self.assertEqual(entry.ledger_type, "preference")
        self.assertEqual(entry.strength, "hard_constraint")

    def test_ledger_entry_rejects_candidate_only_field_leakage(self):
        # candidate_id is OK, but candidate_type is not.
        source, span = build_source_and_span()
        reducer = _reducer(["cand_taste_aaa"], [], [span.id])
        entry = _preference_entry(source.id, span.id, "cand_taste_aaa", reducer.id)
        d = asdict(entry)
        d["candidate_type"] = "taste_signal"  # FORBIDDEN
        with self.assertRaises(ValueError):
            ledger_entry_from_dict(d)

    def test_ledger_entry_dispatch_by_type(self):
        # ledger_entry_from_dict dispatches to the right class.
        source, span = build_source_and_span()
        reducer = _reducer(["cand_taste_aaa"], [], [span.id])
        pref = _preference_entry(source.id, span.id, "cand_taste_aaa", reducer.id)
        got = ledger_entry_from_dict(asdict(pref))
        self.assertIsInstance(got, PreferenceLedgerEntry)


class ReducerDecisionTests(unittest.TestCase):
    def test_reducer_decision_validates(self):
        r = _reducer(["cand_taste_aaa"], ["pref_zzz"], ["span_x"])
        self.assertEqual(r.decision_type, "promote")

    def test_reducer_decision_rejects_missing_required_checks(self):
        rid = make_reducer_decision_id("promote", ["cand_x"], ["fact_y"], ["span_z"], "x")
        with self.assertRaises(ValueError):
            MemoryReducerDecision.from_dict({
                "id": rid,
                "schema_version": "1.0.0",
                "decision_type": "promote",
                "target_candidate_ids": ["cand_x"],
                "target_ledger_entry_ids": ["fact_y"],
                "evidence_span_ids": ["span_z"],
                "rationale": "x",
                "decided_by": {"agent": "a", "model": "gpt", "tool": None, "prompt_ref": None},
                "decided_at": T_DECIDED,
                "confidence": "high",
                "risk_class": "low",
                "checks": {"provenance": "pass"},  # missing required keys
                "metadata": {},
            })

    def test_reducer_decision_factory(self):
        r = _reducer(["cand_x"], ["fact_y"], ["span_z"], "ok")
        self.assertEqual(r.id, make_reducer_decision_id("promote", ["cand_x"], ["fact_y"], ["span_z"], "ok"))
        # roundtrip
        r2 = reducer_decision_from_dict(asdict(r))
        self.assertEqual(r.id, r2.id)


class BundleValidationTests(unittest.TestCase):
    def test_full_bundle_validates(self):
        source, span = build_source_and_span()
        candidate_dict = _build_candidate_record(span.id)
        candidate_id = candidate_dict["id"]
        # Build the entry first, then the reducer (so the reducer targets
        # the actual entry id), then update the entry's reducer_decision_id
        # to match.
        entry = _preference_entry(source.id, span.id, candidate_id, "redmem_pending")
        reducer = _reducer([candidate_id], [entry.id], [span.id])
        entry_dict = asdict(entry)
        entry_dict["reducer_decision_id"] = reducer.id
        # Round-trip everything through asdict to make sure the bundle
        # validator receives plain dicts.
        validate_ledger_bundle(
            source_records=[asdict(source)],
            episode_records=[],
            evidence_spans=[asdict(span)],
            candidate_records=[candidate_dict],
            reducer_decisions=[asdict(reducer)],
            ledger_entries=[entry_dict],
        )

    def test_bundle_rejects_dangling_candidate_reference(self):
        source, span = build_source_and_span()
        reducer = _reducer(["cand_taste_does_not_exist"], [], [span.id])
        candidate_dict = _build_candidate_record(span.id)
        with self.assertRaises(ValueError):
            validate_ledger_bundle(
                source_records=[asdict(source)],
                episode_records=[],
                evidence_spans=[asdict(span)],
                candidate_records=[candidate_dict],
                reducer_decisions=[asdict(reducer)],
                ledger_entries=[],
            )

    def test_bundle_rejects_ledger_entry_not_authorized_by_reducer(self):
        source, span = build_source_and_span()
        candidate_dict = _build_candidate_record(span.id)
        candidate_id = candidate_dict["id"]
        # Reducer does not target the entry
        reducer = _reducer([candidate_id], [], [span.id])
        entry = _preference_entry(source.id, span.id, candidate_id, reducer.id)
        with self.assertRaises(ValueError):
            validate_ledger_bundle(
                source_records=[asdict(source)],
                episode_records=[],
                evidence_spans=[asdict(span)],
                candidate_records=[candidate_dict],
                reducer_decisions=[asdict(reducer)],
                ledger_entries=[asdict(entry)],
            )

    def test_supersession_must_be_reciprocal(self):
        source, span = build_source_and_span()
        candidate_dict = _build_candidate_record(span.id)
        candidate_id = candidate_dict["id"]
        entry1 = _preference_entry(source.id, span.id, candidate_id, "redmem_x")
        entry2 = _preference_entry(source.id, span.id, candidate_id, "redmem_x")
        # Make entry2 supersede entry1, but don't reciprocate.
        e1 = asdict(entry1)
        e2 = asdict(entry2)
        e2["supersedes"] = [e1["id"]]
        e2["valid_from"] = "2026-06-01T00:00:00Z"
        e2["status"] = "active"
        # e1.superseded_by is still empty -> non-reciprocal -> should fail
        reducer_id = make_reducer_decision_id("supersede", [], [e1["id"], e2["id"]], [span.id], "ok")
        reducer = MemoryReducerDecision.from_dict({
            "id": reducer_id,
            "schema_version": "1.0.0",
            "decision_type": "supersede",
            "target_candidate_ids": [],
            "target_ledger_entry_ids": [e1["id"], e2["id"]],
            "evidence_span_ids": [span.id],
            "rationale": "ok",
            "decided_by": {"agent": "reducer", "model": "gpt", "tool": None, "prompt_ref": None},
            "decided_at": T_DECIDED,
            "confidence": "high",
            "risk_class": "low",
            "checks": {"provenance": "pass", "temporal_validity": "pass", "contradiction_scan": "pass", "privacy": "pass", "usefulness": "pass"},
            "metadata": {},
        })
        with self.assertRaises(ValueError):
            validate_ledger_bundle(
                source_records=[asdict(source)],
                episode_records=[],
                evidence_spans=[asdict(span)],
                candidate_records=[candidate_dict],
                reducer_decisions=[asdict(reducer)],
                ledger_entries=[e1, e2],
            )


if __name__ == "__main__":
    unittest.main()
