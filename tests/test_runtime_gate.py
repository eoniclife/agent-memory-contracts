"""Tests for agent_memory_contracts.runtime.gate.

ADR-1 (server-side id authority + idempotency), ADR-4 (closure
validation, errors verbatim), ADR-5 (single transactional
promote(), structured conflicts, first-commit-wins). Every
rejection path has a test; the concurrency race lives in
tests/invariants/.
"""

from __future__ import annotations

import unittest

from agent_memory_contracts import validate_ledger_bundle
from agent_memory_contracts.runtime.anchors import (
    verify_chain,
    verify_coverage,
)
from agent_memory_contracts.runtime.gate import MemoryGate
from agent_memory_contracts.runtime.store import (
    ConflictError,
    IdMismatchError,
    MemoryStore,
    ValidationRejectedError,
)

from .runtime_seed import (
    T2,
    T_CREATED,
    _decision,
    _fact,
    build_universe,
    seed_via_gate,
)


class _GateCase(unittest.TestCase):
    def setUp(self):
        self.universe = build_universe()
        self.store = MemoryStore()
        self.gate = MemoryGate(self.store)

    def tearDown(self):
        self.store.close()


class IngestionTests(_GateCase):
    def test_submit_source_then_replay_is_idempotent(self):
        first = self.gate.submit_source(self.universe.source,
                                        created_at=T_CREATED)
        self.assertTrue(first.created)
        self.assertIsNotNone(first.anchor_seq)
        replay = self.gate.submit_source(self.universe.source,
                                         created_at=T_CREATED)
        self.assertFalse(replay.created)
        self.assertIsNone(replay.anchor_seq)
        self.assertEqual(self.store.counts()["audit_anchors"], 1)

    def test_client_id_mismatch_is_structured(self):
        forged = dict(self.universe.source)
        forged["id"] = "src_" + "0" * 24
        with self.assertRaises(IdMismatchError) as ctx:
            self.gate.submit_source(forged)
        self.assertEqual(ctx.exception.expected,
                         str(self.universe.source["id"]))
        self.assertEqual(ctx.exception.got, "src_" + "0" * 24)
        self.assertEqual(self.store.counts()["sources"], 0)

    def test_same_id_different_content_conflicts(self):
        self.gate.submit_source(self.universe.source, created_at=T_CREATED)
        variant = dict(self.universe.source)
        variant["title"] = "A different title (id-invariant field)"
        with self.assertRaises(ConflictError) as ctx:
            self.gate.submit_source(variant)
        self.assertEqual(ctx.exception.conflict_kind, "payload_mismatch")

    def test_invalid_payload_rejected_verbatim(self):
        broken = dict(self.universe.source)
        broken["privacy_class"] = "totally-private"
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.submit_source(broken)
        self.assertIn("invalid privacy_class", ctx.exception.errors[0])

    def test_span_requires_its_source(self):
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.submit_span(self.universe.spans[0])
        self.assertIn("dangling source_id", ctx.exception.errors[0])

    def test_candidate_requires_its_evidence(self):
        self.gate.submit_source(self.universe.source, created_at=T_CREATED)
        # Spans deliberately not submitted.
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.submit_candidate(self.universe.candidates["weekly"])
        self.assertIn("dangling evidence_span_id", ctx.exception.errors[0])

    def test_ledger_payload_through_ingestion_is_rejected(self):
        # The fuzz invariant covers this exhaustively; this is the
        # canonical case: a trusted-plane payload cannot enter
        # through any ingestion method.
        entry = self.universe.entries["weekly"]
        for method in (self.gate.submit_source, self.gate.submit_span,
                       self.gate.submit_candidate):
            with self.assertRaises((ValidationRejectedError,
                                    IdMismatchError)):
                method(entry)
        self.assertEqual(self.store.counts()["ledger_entries"], 0)


class PromoteTests(_GateCase):
    def setUp(self):
        super().setUp()
        self.gate.submit_source(self.universe.source, created_at=T_CREATED)
        for span in self.universe.spans:
            self.gate.submit_span(span, created_at=T_CREATED)
        for candidate in self.universe.candidates.values():
            self.gate.submit_candidate(candidate, created_at=T_CREATED)

    def test_promote_commits_decision_entry_and_anchor(self):
        receipt = self.gate.promote(
            self.universe.decisions["promote_weekly"],
            [self.universe.entries["weekly"]], created_at=T_CREATED)
        self.assertTrue(receipt.created)
        self.assertGreater(receipt.closure_size, 3)
        entry = self.store.get_ledger_entry(
            str(self.universe.entries["weekly"]["id"]))
        self.assertEqual(entry["status"], "active")
        self.assertTrue(verify_chain(self.store).ok)
        self.assertTrue(verify_coverage(self.store).ok)

    def test_promote_replay_is_idempotent(self):
        self.gate.promote(self.universe.decisions["promote_weekly"],
                          [self.universe.entries["weekly"]],
                          created_at=T_CREATED)
        before = self.store.counts()
        replay = self.gate.promote(self.universe.decisions["promote_weekly"],
                                   [self.universe.entries["weekly"]],
                                   created_at=T_CREATED)
        self.assertFalse(replay.created)
        self.assertIsNone(replay.anchor_seq)
        self.assertEqual(self.store.counts(), before)

    def test_decision_id_mismatch_is_structured(self):
        forged = dict(self.universe.decisions["promote_weekly"])
        forged["id"] = "redmem_" + "0" * 24
        with self.assertRaises(IdMismatchError) as ctx:
            self.gate.promote(forged, [self.universe.entries["weekly"]])
        self.assertEqual(ctx.exception.expected,
                         str(self.universe.decisions["promote_weekly"]["id"]))

    def test_entry_id_mismatch_is_structured(self):
        forged_entry = dict(self.universe.entries["weekly"])
        forged_entry["id"] = "fact_" + "0" * 24
        with self.assertRaises(IdMismatchError):
            self.gate.promote(self.universe.decisions["promote_weekly"],
                              [forged_entry])

    def test_entry_not_targeted_by_decision_is_rejected(self):
        # The canonical "you forgot to wire the new entry" bug.
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.promote(self.universe.decisions["promote_weekly"],
                              [self.universe.entries["budget"]])
        self.assertTrue(any("not targeted by the decision" in e
                            for e in ctx.exception.errors))

    def test_unauthorized_evidence_is_rejected_by_the_library(self):
        # Entry cites span s2; the decision only authorizes s1.
        # The closure validator rejects with the library message.
        universe = self.universe
        bad_entry = _fact(universe.candidates["budget"],
                          source_id=str(universe.source["id"]),
                          decision_id="redmem_x", valid_from=T_CREATED)
        bad_decision = _decision(
            "promote", [str(universe.candidates["budget"]["id"])],
            [str(bad_entry["id"])], [universe.span_ids[0]],
            "decision authorizes the wrong span")
        bad_entry["reducer_decision_id"] = str(bad_decision["id"])
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.promote(bad_decision, [bad_entry])
        self.assertIn("not authorized by reducer decision",
                      ctx.exception.errors[0])

    def test_dangling_candidate_is_rejected_by_the_library(self):
        fresh_store = MemoryStore()
        fresh_gate = MemoryGate(fresh_store)
        fresh_gate.submit_source(self.universe.source, created_at=T_CREATED)
        for span in self.universe.spans:
            fresh_gate.submit_span(span, created_at=T_CREATED)
        # Candidate never submitted.
        with self.assertRaises(ValidationRejectedError) as ctx:
            fresh_gate.promote(self.universe.decisions["promote_weekly"],
                               [self.universe.entries["weekly"]])
        self.assertIn("dangling target_candidate_id",
                      ctx.exception.errors[0])
        fresh_store.close()

    def test_double_promotion_first_commit_wins(self):
        self.gate.promote(self.universe.decisions["promote_weekly"],
                          [self.universe.entries["weekly"]],
                          created_at=T_CREATED)
        # A different decision (different rationale => different id)
        # promoting the same candidate.
        universe = self.universe
        rival_entry = dict(universe.entries["weekly"])
        rival = _decision(
            "promote", [str(universe.candidates["weekly"]["id"])],
            [str(rival_entry["id"])], [universe.span_ids[0]],
            "a second reducer promotes the same candidate")
        rival_entry["reducer_decision_id"] = str(rival["id"])
        with self.assertRaises(ConflictError) as ctx:
            self.gate.promote(rival, [rival_entry])
        self.assertEqual(ctx.exception.conflict_kind,
                         "candidate_already_promoted")
        self.assertEqual(ctx.exception.winning_decision_id,
                         str(universe.decisions["promote_weekly"]["id"]))

    def test_same_decision_id_different_content_conflicts(self):
        self.gate.promote(self.universe.decisions["promote_weekly"],
                          [self.universe.entries["weekly"]],
                          created_at=T_CREATED)
        variant = dict(self.universe.decisions["promote_weekly"])
        variant["confidence"] = "medium"  # id-invariant field
        with self.assertRaises(ConflictError) as ctx:
            self.gate.promote(variant, [self.universe.entries["weekly"]])
        self.assertEqual(ctx.exception.conflict_kind, "decision_id_exists")

    def test_reject_decision_quarantines_without_writing_entries(self):
        receipt = self.gate.promote(self.universe.decisions["reject"],
                                    created_at=T_CREATED)
        self.assertTrue(receipt.created)
        self.assertEqual(receipt.entry_ids, ())
        self.assertEqual(self.store.counts()["ledger_entries"], 0)
        self.assertEqual(
            self.store.rejected_candidate_ids(),
            [str(self.universe.candidates["poison"]["id"])])
        # The rejected candidate is still promotable later (a
        # rejection is a recorded decision, not a tombstone).
        self.assertIsNone(self.store.promoting_decision_for(
            str(self.universe.candidates["poison"]["id"])))

    def test_reject_carrying_entries_is_rejected(self):
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.promote(self.universe.decisions["reject"],
                              [self.universe.entries["weekly"]])
        self.assertTrue(any("must not carry ledger entries" in e
                            for e in ctx.exception.errors))

    def test_promote_without_entries_is_rejected(self):
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.promote(self.universe.decisions["promote_weekly"])
        self.assertTrue(any("at least one ledger entry" in e
                            for e in ctx.exception.errors))


class SupersessionTests(_GateCase):
    def setUp(self):
        super().setUp()
        self.universe2 = self.universe
        self.gate.submit_source(self.universe.source, created_at=T_CREATED)
        for span in self.universe.spans:
            self.gate.submit_span(span, created_at=T_CREATED)
        for candidate in self.universe.candidates.values():
            self.gate.submit_candidate(candidate, created_at=T_CREATED)
        self.gate.promote(self.universe.decisions["promote_weekly"],
                          [self.universe.entries["weekly"]],
                          created_at=T_CREATED)

    def _supersede(self):
        return self.gate.promote(
            self.universe.decisions["supersede"],
            [self.universe.entries["daily"]],
            supersessions=[(str(self.universe.entries["weekly"]["id"]),
                            str(self.universe.entries["daily"]["id"]))],
            created_at=T_CREATED)

    def test_late_supersession_materializes_and_validates(self):
        receipt = self._supersede()
        self.assertTrue(receipt.created)
        weekly = self.store.get_ledger_entry(
            str(self.universe.entries["weekly"]["id"]))
        self.assertEqual(weekly["status"], "superseded")
        self.assertEqual(weekly["valid_until"], T2)
        self.assertEqual(weekly["reducer_decision_id"],
                         str(self.universe.decisions["supersede"]["id"]))
        records = self.store.all_records()
        validate_ledger_bundle(
            source_records=records["sources"],
            episode_records=records["episodes"],
            evidence_spans=records["spans"],
            candidate_records=records["candidates"],
            reducer_decisions=records["decisions"],
            ledger_entries=records["entries"],
        )
        self.assertTrue(verify_chain(self.store).ok)
        self.assertTrue(verify_coverage(self.store).ok)

    def test_double_supersession_first_commit_wins(self):
        self._supersede()
        universe = self.universe
        # A rival successor for the same target (different object
        # => different entry id and decision id).
        rival_candidate = universe.candidates["daily"]
        rival_entry = _fact(rival_candidate,
                            source_id=str(universe.source["id"]),
                            decision_id="redmem_x",
                            valid_from="2026-06-03T10:00:00Z",
                            supersedes=[str(universe.entries["weekly"]["id"])])
        rival_decision = _decision(
            "supersede",
            sorted([str(universe.candidates["weekly"]["id"]),
                    str(rival_candidate["id"])]),
            sorted([str(universe.entries["weekly"]["id"]),
                    str(rival_entry["id"])]),
            sorted([universe.span_ids[0], universe.span_ids[2]]),
            "a rival supersession of the same target")
        rival_entry["reducer_decision_id"] = str(rival_decision["id"])
        with self.assertRaises(ConflictError) as ctx:
            self.gate.promote(
                rival_decision, [rival_entry],
                supersessions=[(str(universe.entries["weekly"]["id"]),
                                str(rival_entry["id"]))])
        self.assertEqual(ctx.exception.conflict_kind,
                         "entry_already_superseded")
        self.assertEqual(ctx.exception.winning_decision_id,
                         str(universe.decisions["supersede"]["id"]))

    def test_supersession_successor_must_be_carried(self):
        ghost = "fact_" + "9" * 24
        decision = _decision(
            "supersede", [str(self.universe.candidates["weekly"]["id"])],
            [str(self.universe.entries["weekly"]["id"]), ghost],
            [self.universe.span_ids[0]], "successor not carried")
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.promote(
                decision,
                supersessions=[(str(self.universe.entries["weekly"]["id"]),
                                ghost)])
        self.assertTrue(any("not carried by this batch" in e
                            for e in ctx.exception.errors))

    def test_self_supersession_is_rejected(self):
        weekly_id = str(self.universe.entries["weekly"]["id"])
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.promote(self.universe.decisions["supersede"],
                              [self.universe.entries["daily"]],
                              supersessions=[(weekly_id, weekly_id)])
        self.assertTrue(any("by itself" in e for e in ctx.exception.errors))

    def test_supersession_cycle_is_structured_rejection(self):
        universe = self.universe
        entry_a = _fact(universe.candidates["budget"],
                        source_id=str(universe.source["id"]),
                        decision_id="redmem_x", valid_from=T2)
        entry_b = _fact(universe.candidates["daily"],
                        source_id=str(universe.source["id"]),
                        decision_id="redmem_x", valid_from=T2)
        decision = _decision(
            "supersede",
            sorted([str(universe.candidates["budget"]["id"]),
                    str(universe.candidates["daily"]["id"])]),
            sorted([str(entry_a["id"]), str(entry_b["id"])]),
            sorted([universe.span_ids[1], universe.span_ids[2]]),
            "mutual supersession cycle should stay structured")
        entry_a["reducer_decision_id"] = str(decision["id"])
        entry_b["reducer_decision_id"] = str(decision["id"])

        before = self.store.counts()
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.promote(
                decision, [entry_a, entry_b],
                supersessions=[(str(entry_a["id"]), str(entry_b["id"])),
                                (str(entry_b["id"]), str(entry_a["id"]))])

        self.assertTrue(any(
            "ledger supersession cycle detected" in error
            for error in ctx.exception.errors))
        self.assertEqual(self.store.counts(), before)

    def test_supersede_replay_is_idempotent(self):
        self._supersede()
        before = self.store.counts()
        replay = self._supersede()
        self.assertFalse(replay.created)
        self.assertEqual(self.store.counts(), before)


class StatusOverrideTests(_GateCase):
    def setUp(self):
        super().setUp()
        seed_via_gate(self.gate, self.universe)

    def test_retract_flips_status_via_override_edge(self):
        receipt = self.gate.promote(self.universe.decisions["retract"],
                                    created_at=T_CREATED)
        self.assertTrue(receipt.created)
        self.assertEqual(receipt.status_overrides,
                         ((str(self.universe.entries["budget"]["id"]),
                           "retracted"),))
        budget = self.store.get_ledger_entry(
            str(self.universe.entries["budget"]["id"]))
        self.assertEqual(budget["status"], "retracted")
        active_ids = {e["id"] for e in self.store.active_entries()}
        self.assertNotIn(str(self.universe.entries["budget"]["id"]),
                         active_ids)
        self.assertTrue(verify_chain(self.store).ok)
        self.assertTrue(verify_coverage(self.store).ok)

    def test_retract_on_superseded_entry_conflicts(self):
        universe = self.universe
        decision = _decision("retract",
                             [str(universe.candidates["weekly"]["id"])],
                             [str(universe.entries["weekly"]["id"])],
                             [universe.span_ids[0]],
                             "retracting an already superseded entry")
        with self.assertRaises(ConflictError) as ctx:
            self.gate.promote(decision)
        self.assertEqual(ctx.exception.conflict_kind,
                         "entry_already_superseded")

    def test_double_retract_conflicts_with_winner(self):
        self.gate.promote(self.universe.decisions["retract"],
                          created_at=T_CREATED)
        universe = self.universe
        rival = _decision("retract",
                          [str(universe.candidates["budget"]["id"])],
                          [str(universe.entries["budget"]["id"])],
                          [universe.span_ids[1]],
                          "a rival retraction of the same entry")
        with self.assertRaises(ConflictError) as ctx:
            self.gate.promote(rival)
        self.assertEqual(ctx.exception.conflict_kind,
                         "entry_status_overridden")
        self.assertEqual(ctx.exception.winning_decision_id,
                         str(universe.decisions["retract"]["id"]))

    def test_retract_must_target_an_entry(self):
        universe = self.universe
        decision = _decision("retract",
                             [str(universe.candidates["budget"]["id"])],
                             [], [universe.span_ids[1]],
                             "retract with no target")
        with self.assertRaises(ValidationRejectedError) as ctx:
            self.gate.promote(decision)
        self.assertTrue(any("must target at least one" in e
                            for e in ctx.exception.errors))


class FullSessionTests(_GateCase):
    def test_end_to_end_session_validates_chains_and_covers(self):
        receipts = seed_via_gate(self.gate, self.universe,
                                 include_retract=True)
        self.assertTrue(all(r.created for r in receipts.values()))
        counts = self.store.counts()
        self.assertEqual(counts["ledger_entries"], 3)
        self.assertEqual(counts["supersessions"], 1)
        self.assertEqual(counts["status_overrides"], 1)
        # One anchor per write batch: 1 source + 3 spans +
        # 4 candidates + 4 promote/supersede/reject batches +
        # 1 retract.
        self.assertEqual(counts["audit_anchors"], 13)
        self.assertTrue(verify_chain(self.store).ok)
        self.assertTrue(verify_coverage(self.store).ok)
        self.assertEqual(self.store.check_extracted_columns(), [])

    def test_full_revalidation_appends_verified_anchor(self):
        seed_via_gate(self.gate, self.universe)
        anchor = self.gate.full_revalidation(created_at=T_CREATED)
        self.assertEqual(anchor.kind, "verified")
        result = verify_chain(self.store)
        self.assertTrue(result.ok, result.divergence)
        kinds = [a["kind"] for a in self.store.list_anchors()]
        self.assertEqual(kinds[-1], "verified")

    def test_full_revalidation_rejects_a_tampered_bundle(self):
        import sqlite3
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.sqlite3"
            store = MemoryStore(path)
            gate = MemoryGate(store)
            seed_via_gate(gate, self.universe)
            conn = sqlite3.connect(path)
            try:
                for name in [str(r[0]) for r in conn.execute(
                        "SELECT name FROM sqlite_master"
                        " WHERE type = 'trigger'")]:
                    conn.execute(f"DROP TRIGGER {name}")
                conn.execute(
                    "UPDATE ledger_entries SET payload ="
                    " replace(payload, '1%', '90%')")
                conn.commit()
            finally:
                conn.close()
            with self.assertRaises(ValidationRejectedError):
                gate.full_revalidation(created_at=T_CREATED)
            store.close()


if __name__ == "__main__":
    unittest.main()
