"""Tests for agent_memory_contracts.runtime.anchors.

ADR-6 treated as security-grade code: the happy chain, then every
tamper scenario the ADR names -- edited payload, deleted row,
reordered/rewritten chain -- plus the two runtime-specific
bypasses: a hand-armed write guard and a silent (unanchored) row,
which the coverage check catches.

Tampering requires hostile-DBA powers (dropping the append-only
triggers); the tests do exactly that, because that is the threat
model the anchors exist for.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_memory_contracts.runtime.anchors import (
    GENESIS_PREV,
    append_anchor,
    compute_scope_fingerprint,
    full_scope,
    make_scope,
    verify_chain,
    verify_coverage,
)
from agent_memory_contracts.runtime.store import MemoryStore, StoreError

from .runtime_seed import T_CREATED, build_universe, seed_anchored


class _AnchoredStoreCase(unittest.TestCase):
    """A file-backed, fully anchored store plus a hostile raw
    connection that has dropped the protective triggers."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "store.sqlite3"
        self.universe = build_universe()
        self.store = MemoryStore(self.db_path)
        self.seqs = seed_anchored(self.store, self.universe,
                                  include_retract=True)

    def tearDown(self):
        self.store.close()
        self._tmp.cleanup()

    def hostile_conn(self) -> sqlite3.Connection:
        """A raw connection with every trigger dropped (the DBA
        threat model)."""
        conn = sqlite3.connect(self.db_path)
        triggers = [str(r[0]) for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'")]
        for name in triggers:
            conn.execute(f"DROP TRIGGER {name}")
        conn.commit()
        return conn


class ChainHappyPathTests(_AnchoredStoreCase):
    def test_untampered_chain_verifies_end_to_end(self):
        result = verify_chain(self.store)
        self.assertTrue(result.ok, result.divergence)
        self.assertEqual(result.anchors_checked, len(self.seqs))
        self.assertIsNone(result.divergence)

    def test_chain_links_to_genesis(self):
        anchors = self.store.list_anchors()
        self.assertEqual(anchors[0]["prev_fingerprint"], GENESIS_PREV)
        for prev, anchor in zip(anchors, anchors[1:]):
            self.assertEqual(anchor["prev_fingerprint"],
                             prev["fingerprint"])

    def test_coverage_is_complete_after_gateful_writes(self):
        report = verify_coverage(self.store)
        self.assertTrue(report.ok, (report.unanchored,
                                    report.derived_index_drift))

    def test_fingerprints_are_replayable_later(self):
        # Recompute every anchor from a *fresh* store handle: the
        # stored payloads are immutable, so fingerprints replay.
        reopened = MemoryStore(self.db_path)
        try:
            import json
            for anchor in reopened.list_anchors():
                fingerprint, missing = compute_scope_fingerprint(
                    reopened, json.loads(anchor["scope"]))
                self.assertIsNone(missing)
                self.assertEqual(fingerprint, anchor["fingerprint"])
        finally:
            reopened.close()

    def test_verified_anchor_over_full_scope(self):
        scope = full_scope(self.store)
        with self.store._txn() as conn:
            self.store._arm_guard(conn)
            receipt = append_anchor(self.store, conn, scope=scope,
                                    kind="verified", actor="test-nightly",
                                    created_at=T_CREATED)
            self.store._disarm_guard(conn)
        self.assertEqual(receipt.kind, "verified")
        self.assertEqual(self.store.list_anchors()[-1]["scope"],
                         json.dumps(scope, sort_keys=True,
                                    separators=(",", ":")))
        result = verify_chain(self.store)
        self.assertTrue(result.ok, result.divergence)

    def test_append_anchor_preserves_legacy_non_ascii_scope_bytes(self):
        scope = {"record_ids": [], "note": "é"}
        with self.store._txn() as conn:
            self.store._arm_guard(conn)
            append_anchor(self.store, conn, scope=scope, kind="verified",
                          actor="test-nightly", created_at=T_CREATED)
            self.store._disarm_guard(conn)
        self.assertEqual(
            self.store.list_anchors()[-1]["scope"],
            json.dumps(scope, sort_keys=True, separators=(",", ":")),
        )
        result = verify_chain(self.store)
        self.assertTrue(result.ok, result.divergence)

    def test_append_anchor_rejects_unknown_kind_and_missing_scope(self):
        with self.store._txn() as conn:
            self.store._arm_guard(conn)
            with self.assertRaises(ValueError):
                append_anchor(self.store, conn, scope=make_scope([]),
                              kind="nightly", actor="x",
                              created_at=T_CREATED)
            with self.assertRaises(StoreError):
                append_anchor(self.store, conn,
                              scope=make_scope(["fact_" + "0" * 24]),
                              kind="write", actor="x",
                              created_at=T_CREATED)
            self.store._disarm_guard(conn)


class TamperDetectionTests(_AnchoredStoreCase):
    def test_edited_payload_is_detected(self):
        entry_id = str(self.universe.entries["weekly"]["id"])
        conn = self.hostile_conn()
        try:
            conn.execute(
                "UPDATE ledger_entries SET payload ="
                " replace(payload, 'weekly', 'monthly') WHERE id = ?",
                (entry_id,))
            conn.commit()
        finally:
            conn.close()
        result = verify_chain(self.store)
        self.assertFalse(result.ok)
        self.assertEqual(result.divergence.kind, "fingerprint_mismatch")
        # The divergence is at the promote batch (anchor 3).
        self.assertEqual(result.divergence.seq, self.seqs[2])

    def test_deleted_row_is_detected(self):
        span_id = self.universe.span_ids[0]
        conn = self.hostile_conn()
        try:
            conn.execute("DELETE FROM spans WHERE id = ?", (span_id,))
            conn.commit()
        finally:
            conn.close()
        result = verify_chain(self.store)
        self.assertFalse(result.ok)
        self.assertEqual(result.divergence.kind, "missing_record")
        self.assertIn(span_id, result.divergence.detail)

    def test_reordered_chain_is_detected(self):
        # Swap the contents of anchors 1 and 2 (the rewrite a
        # DBA would attempt to reorder history).
        conn = self.hostile_conn()
        try:
            rows = conn.execute(
                "SELECT seq, scope, fingerprint, prev_fingerprint"
                " FROM audit_anchors ORDER BY seq LIMIT 2").fetchall()
            (seq_a, scope_a, fp_a, prev_a), (seq_b, scope_b, fp_b,
                                             prev_b) = rows
            conn.execute(
                "UPDATE audit_anchors SET scope = ?, fingerprint = ?,"
                " prev_fingerprint = ? WHERE seq = ?",
                (scope_b, fp_b, prev_b, seq_a))
            conn.execute(
                "UPDATE audit_anchors SET scope = ?, fingerprint = ?,"
                " prev_fingerprint = ? WHERE seq = ?",
                (scope_a, fp_a, prev_a, seq_b))
            conn.commit()
        finally:
            conn.close()
        result = verify_chain(self.store)
        self.assertFalse(result.ok)
        self.assertEqual(result.divergence.kind, "chain_link_broken")
        self.assertEqual(result.divergence.seq, self.seqs[0])

    def test_rewritten_anchor_fingerprint_breaks_the_link(self):
        conn = self.hostile_conn()
        try:
            conn.execute(
                "UPDATE audit_anchors SET fingerprint = ? WHERE seq = ?",
                ("f" * 64, self.seqs[1]))
            conn.commit()
        finally:
            conn.close()
        result = verify_chain(self.store)
        self.assertFalse(result.ok)
        # Either the rewritten anchor no longer matches its own
        # recomputation, or the next link breaks -- first
        # divergence wins.
        self.assertEqual(result.divergence.kind, "fingerprint_mismatch")
        self.assertEqual(result.divergence.seq, self.seqs[1])

    def test_unparseable_scope_is_detected(self):
        conn = self.hostile_conn()
        try:
            conn.execute(
                "UPDATE audit_anchors SET scope = 'not json' WHERE seq = ?",
                (self.seqs[0],))
            conn.commit()
        finally:
            conn.close()
        result = verify_chain(self.store)
        self.assertFalse(result.ok)
        self.assertEqual(result.divergence.kind, "scope_invalid")

    def test_hand_armed_guard_trips_the_wire(self):
        conn = sqlite3.connect(self.db_path)  # no trigger drop needed
        try:
            conn.execute("UPDATE _write_guard SET armed = 1 WHERE id = 1")
            conn.commit()
        finally:
            conn.close()
        result = verify_chain(self.store)
        self.assertFalse(result.ok)
        self.assertEqual(result.divergence.kind, "guard_armed")

    def test_silent_row_is_flagged_by_coverage(self):
        # An attacker arms the guard and inserts a well-formed row
        # without an anchor: the triggers cannot stop it, the
        # chain still verifies (old anchors are untouched), but
        # coverage flags the orphan.
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("UPDATE _write_guard SET armed = 1 WHERE id = 1")
            conn.execute(
                "INSERT INTO ledger_entries (id, tenant_id, record_type,"
                " status, decision_id, payload, created_at) VALUES"
                " ('fact_silent000000000000000', 'default', 'fact',"
                " 'active', 'redmem_forged', '{\"id\":"
                " \"fact_silent000000000000000\"}', 'now')")
            conn.execute("UPDATE _write_guard SET armed = 0 WHERE id = 1")
            conn.execute("COMMIT")
        finally:
            conn.close()
        self.assertTrue(verify_chain(self.store).ok)
        report = verify_coverage(self.store)
        self.assertFalse(report.ok)
        self.assertIn("ledger_entries:fact_silent000000000000000",
                      report.unanchored)

    def test_forged_authorization_edge_is_flagged_as_index_drift(self):
        # A forged decision_authorizations row (making a rejected
        # candidate look promoted to index readers) disagrees with
        # the anchored decision payloads.
        conn = self.hostile_conn()
        try:
            conn.execute(
                "INSERT INTO decision_authorizations (tenant_id,"
                " decision_id, authorized_kind, authorized_id,"
                " decision_type, created_at) VALUES ('default',"
                " 'redmem_forged', 'candidate', ?, 'promote', 'now')",
                (str(self.universe.candidates["poison"]["id"]),))
            conn.commit()
        finally:
            conn.close()
        report = verify_coverage(self.store)
        self.assertFalse(report.ok)
        self.assertTrue(any("unexpected row" in d
                            for d in report.derived_index_drift))


class ScopeFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_deterministic_and_order_insensitive(self):
        universe = build_universe()
        store = MemoryStore()
        seed_anchored(store, universe)
        ids = [str(universe.source["id"])] + universe.span_ids
        a, _ = compute_scope_fingerprint(store, make_scope(ids))
        b, _ = compute_scope_fingerprint(store, make_scope(reversed(ids)))
        self.assertEqual(a, b)
        store.close()

    def test_missing_record_short_circuits(self):
        store = MemoryStore()
        fingerprint, missing = compute_scope_fingerprint(
            store, make_scope(["fact_" + "0" * 24]))
        self.assertIsNone(fingerprint)
        self.assertEqual(missing, "fact_" + "0" * 24)
        # Unknown prefixes are reported as missing, not crashes.
        fingerprint, missing = compute_scope_fingerprint(
            store, make_scope(["weird_id"]))
        self.assertIsNone(fingerprint)
        self.assertEqual(missing, "weird_id")
        store.close()

    def test_edges_change_the_fingerprint(self):
        universe = build_universe()
        store = MemoryStore()
        seed_anchored(store, universe)
        ids = [str(universe.entries["weekly"]["id"])]
        bare, _ = compute_scope_fingerprint(store, make_scope(ids))
        with_edge, _ = compute_scope_fingerprint(store, make_scope(
            ids, supersession_edges=[("a", "b", "c")]))
        self.assertNotEqual(bare, with_edge)
        store.close()


if __name__ == "__main__":
    unittest.main()
