# INVARIANT 4 — Deterministic receipts.
#
# Product language: "Ask the same question of the same memory and
# you get the byte-identical context pack, with a receipt —
# fingerprinted, replayable, forever. And the audit chain under it
# is tamper-evident: edit one byte of one fact, delete one row, or
# reorder history, and verification names the exact point of
# divergence."
#
# Executable form: same store state + same pack request => same
# pack id and byte-identical fingerprint (in-process, across fresh
# handles; the arthashila --runtime smoke test proves it across
# processes); the anchor chain verifies end-to-end after a full
# session including grounding writes; all three ADR-6 tamper
# scenarios are detected.

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_memory_contracts.runtime import grounding
from agent_memory_contracts.runtime.anchors import (
    verify_chain,
    verify_coverage,
)
from agent_memory_contracts.runtime.gate import MemoryGate
from agent_memory_contracts.runtime.store import MemoryStore

from ..runtime_seed import T_CREATED, build_universe, seed_via_gate

QUESTION = "what is the deploy cadence?"


class _SessionCase(unittest.TestCase):
    """A file-backed store with a full gate session + grounding."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "store.sqlite3"
        self.universe = build_universe()
        self.store = MemoryStore(self.db_path)
        self.gate = MemoryGate(self.store)
        seed_via_gate(self.gate, self.universe, include_retract=True)
        self.pack = grounding.build_context_pack(
            self.store, task=QUESTION, created_at=T_CREATED)
        self.answer = grounding.answer(self.store, question=QUESTION,
                                       created_at=T_CREATED)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def hostile(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        for name in [str(r[0]) for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger'")]:
            conn.execute(f"DROP TRIGGER {name}")
        conn.commit()
        return conn


class DeterministicReceiptTests(_SessionCase):
    def test_same_state_same_request_identical_fingerprint(self):
        again = grounding.build_context_pack(self.store, task=QUESTION,
                                             created_at=T_CREATED)
        self.assertEqual(self.pack.pack_id, again.pack_id)
        self.assertEqual(self.pack.fingerprint, again.fingerprint)
        self.assertEqual(self.pack.receipt.ranking, again.receipt.ranking)
        self.assertEqual(self.pack.receipt.weights, again.receipt.weights)
        # A fresh handle on the same file agrees byte for byte.
        reopened = MemoryStore(self.db_path)
        third = grounding.build_context_pack(reopened, task=QUESTION,
                                             created_at=T_CREATED)
        self.assertEqual(self.pack.fingerprint, third.fingerprint)
        self.assertEqual(self.pack.pack_id, third.pack_id)
        reopened.close()

    def test_different_state_different_fingerprint(self):
        # The fingerprint is sensitive: any change to the selected
        # memory changes it (a receipt that never changes is not a
        # receipt).
        other = grounding.build_context_pack(
            self.store, task=QUESTION, as_of="2026-06-01T12:00:00Z",
            created_at=T_CREATED)
        self.assertNotEqual(self.pack.fingerprint, other.fingerprint)

    def test_receipts_are_persisted_and_replayable(self):
        envelope = self.store.get_context_pack(self.pack.pack_id)
        self.assertEqual(envelope["fingerprint"], self.pack.fingerprint)
        stored_answer = self.store.get_answer(self.answer.answer_id)
        self.assertEqual(stored_answer["pack_fingerprint"],
                         self.answer.pack.fingerprint)

    def test_chain_verifies_end_to_end_after_the_full_session(self):
        result = verify_chain(self.store)
        self.assertTrue(result.ok, result.divergence)
        self.assertGreaterEqual(result.anchors_checked, 13)
        coverage = verify_coverage(self.store)
        self.assertTrue(coverage.ok, (coverage.unanchored,
                                      coverage.derived_index_drift))


class TamperDetectionTests(_SessionCase):
    def test_edited_payload_is_named_at_the_exact_anchor(self):
        conn = self.hostile()
        try:
            conn.execute(
                "UPDATE ledger_entries SET payload ="
                " replace(payload, '1%', '90%') WHERE id = ?",
                (str(self.universe.entries["budget"]["id"]),))
            conn.commit()
        finally:
            conn.close()
        result = verify_chain(self.store)
        self.assertFalse(result.ok)
        self.assertEqual(result.divergence.kind, "fingerprint_mismatch")

    def test_deleted_row_is_detected(self):
        conn = self.hostile()
        try:
            conn.execute("DELETE FROM reducer_decisions WHERE id = ?",
                         (str(self.universe.decisions["reject"]["id"]),))
            conn.commit()
        finally:
            conn.close()
        result = verify_chain(self.store)
        self.assertFalse(result.ok)
        self.assertEqual(result.divergence.kind, "missing_record")
        self.assertIn(str(self.universe.decisions["reject"]["id"]),
                      result.divergence.detail)

    def test_reordered_chain_is_detected(self):
        conn = self.hostile()
        try:
            rows = conn.execute(
                "SELECT seq, scope, fingerprint, prev_fingerprint"
                " FROM audit_anchors ORDER BY seq LIMIT 2").fetchall()
            (seq_a, sc_a, fp_a, pv_a), (seq_b, sc_b, fp_b, pv_b) = rows
            conn.execute(
                "UPDATE audit_anchors SET scope=?, fingerprint=?,"
                " prev_fingerprint=? WHERE seq=?", (sc_b, fp_b, pv_b,
                                                    seq_a))
            conn.execute(
                "UPDATE audit_anchors SET scope=?, fingerprint=?,"
                " prev_fingerprint=? WHERE seq=?", (sc_a, fp_a, pv_a,
                                                    seq_b))
            conn.commit()
        finally:
            conn.close()
        result = verify_chain(self.store)
        self.assertFalse(result.ok)
        self.assertEqual(result.divergence.kind, "chain_link_broken")

    def test_tampered_pack_envelope_is_detected(self):
        # The receipts themselves are anchored: rewriting a stored
        # pack envelope (e.g. to claim a different fingerprint
        # after the fact) diverges.
        conn = self.hostile()
        try:
            conn.execute(
                "UPDATE context_packs SET payload ="
                " replace(payload, ?, ?) WHERE id = ?",
                (self.pack.fingerprint, "f" * 64, self.pack.pack_id))
            conn.commit()
        finally:
            conn.close()
        result = verify_chain(self.store)
        self.assertFalse(result.ok)
        self.assertEqual(result.divergence.kind, "fingerprint_mismatch")


if __name__ == "__main__":
    unittest.main()
