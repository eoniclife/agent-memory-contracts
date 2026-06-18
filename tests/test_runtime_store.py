"""Tests for agent_memory_contracts.runtime.store.

Covers the ADR-2/3/13 storage semantics in isolation: append-only
triggers, the gate-only write guard, supersession + status-override
materialization, the active ledger (structural and time-travel),
deterministic search, drift checks, and tenancy scoping. The
public write path is tested in test_runtime_gate.py.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_memory_contracts import validate_ledger_bundle
from agent_memory_contracts.runtime.store import (
    MemoryStore,
    NotFoundError,
    _epoch_or_none,
    canonical_json,
)

from .runtime_seed import T1, T2, build_universe, seed_raw


class TriggerEnforcementTests(unittest.TestCase):
    """The SQL layer itself refuses silent writes and any mutation."""

    def setUp(self):
        self.store = MemoryStore()

    def tearDown(self):
        self.store.close()

    def test_unarmed_insert_blocked_on_every_governed_table(self):
        statements = {
            "sources": ("INSERT INTO sources (id, tenant_id, record_type,"
                        " payload, created_at) VALUES ('src_x', 'default',"
                        " 'manual_note', '{}', 'now')"),
            "ledger_entries": (
                "INSERT INTO ledger_entries (id, tenant_id, record_type,"
                " status, decision_id, payload, created_at) VALUES"
                " ('fact_x', 'default', 'fact', 'active', 'redmem_x',"
                " '{}', 'now')"),
            "reducer_decisions": (
                "INSERT INTO reducer_decisions (id, tenant_id,"
                " decision_type, payload, created_at) VALUES ('redmem_x',"
                " 'default', 'promote', '{}', 'now')"),
            "supersessions": (
                "INSERT INTO supersessions (tenant_id, superseded_id,"
                " superseding_id, decision_id, created_at) VALUES"
                " ('default', 'fact_a', 'fact_b', 'redmem_x', 'now')"),
            "status_overrides": (
                "INSERT INTO status_overrides (tenant_id, entry_id,"
                " status, decision_id, created_at) VALUES ('default',"
                " 'fact_a', 'retracted', 'redmem_x', 'now')"),
            "audit_anchors": (
                "INSERT INTO audit_anchors (tenant_id, scope, fingerprint,"
                " prev_fingerprint, actor, kind, created_at) VALUES"
                " ('default', '{}', 'f', 'p', 'me', 'write', 'now')"),
            "answers": (
                "INSERT INTO answers (id, tenant_id, status, question,"
                " payload, created_at) VALUES ('ans_x', 'default',"
                " 'answered', 'q', '{}', 'now')"),
        }
        for table, sql in statements.items():
            with self.subTest(table=table):
                with self.assertRaises(sqlite3.IntegrityError) as ctx:
                    self.store._conn.execute(sql)
                self.assertIn("no silent writes", str(ctx.exception))

    def test_update_and_delete_blocked_even_when_armed(self):
        universe = build_universe()
        seed_raw(self.store, universe)
        entry_id = str(universe.entries["weekly"]["id"])
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            with self.store._txn() as conn:
                self.store._arm_guard(conn)
                conn.execute(
                    "UPDATE ledger_entries SET payload = '{}' WHERE id = ?",
                    (entry_id,))
        self.assertIn("append-only", str(ctx.exception))
        with self.assertRaises(sqlite3.IntegrityError) as ctx:
            with self.store._txn() as conn:
                self.store._arm_guard(conn)
                conn.execute("DELETE FROM ledger_entries WHERE id = ?",
                             (entry_id,))
        self.assertIn("append-only", str(ctx.exception))
        # The rolled-back transactions left the row intact.
        self.assertIsNotNone(self.store.get_ledger_entry(entry_id))

    def test_guard_value_never_committed(self):
        universe = build_universe()
        seed_raw(self.store, universe)
        self.assertEqual(self.store.guard_committed_value(), 0)

    def test_raw_connection_on_same_file_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.sqlite3"
            store = MemoryStore(path)
            try:
                raw = sqlite3.connect(path)
                try:
                    with self.assertRaises(sqlite3.IntegrityError):
                        raw.execute(
                            "INSERT INTO ledger_entries (id, tenant_id,"
                            " record_type, status, decision_id, payload,"
                            " created_at) VALUES ('fact_raw', 'default',"
                            " 'fact', 'active', 'redmem_x', '{}', 'now')")
                finally:
                    raw.close()
            finally:
                store.close()


class MaterializationTests(unittest.TestCase):
    """ADR-3: edges are merged into returned records so the library
    validators pass exactly as written."""

    def setUp(self):
        self.universe = build_universe()
        self.store = MemoryStore()
        seed_raw(self.store, self.universe, include_retract=True)

    def tearDown(self):
        self.store.close()

    def test_superseded_entry_materializes_reciprocity_and_handoff(self):
        weekly_id = str(self.universe.entries["weekly"]["id"])
        daily_id = str(self.universe.entries["daily"]["id"])
        weekly = self.store.get_ledger_entry(weekly_id)
        self.assertEqual(weekly["status"], "superseded")
        self.assertEqual(weekly["superseded_by"], [daily_id])
        self.assertEqual(weekly["valid_until"], T2)
        self.assertEqual(weekly["reducer_decision_id"],
                         str(self.universe.decisions["supersede"]["id"]))
        daily = self.store.get_ledger_entry(daily_id)
        self.assertEqual(daily["status"], "active")
        self.assertEqual(daily["supersedes"], [weekly_id])
        self.assertEqual(daily["superseded_by"], [])

    def test_stored_payload_is_never_rewritten(self):
        weekly_id = str(self.universe.entries["weekly"]["id"])
        stored = self.store.stored_payload(weekly_id)
        self.assertEqual(stored["status"], "active")
        self.assertIsNone(stored["valid_until"])
        self.assertEqual(stored["superseded_by"], [])

    def test_status_override_materializes_retraction(self):
        budget_id = str(self.universe.entries["budget"]["id"])
        budget = self.store.get_ledger_entry(budget_id)
        self.assertEqual(budget["status"], "retracted")
        self.assertEqual(budget["reducer_decision_id"],
                         str(self.universe.decisions["retract"]["id"]))
        stored = self.store.stored_payload(budget_id)
        self.assertEqual(stored["status"], "active")

    def test_materialized_export_passes_the_library_validator(self):
        records = self.store.all_records()
        validate_ledger_bundle(
            source_records=records["sources"],
            episode_records=records["episodes"],
            evidence_spans=records["spans"],
            candidate_records=records["candidates"],
            reducer_decisions=records["decisions"],
            ledger_entries=records["entries"],
        )

    def test_get_record_dispatches_by_prefix(self):
        for record_id in [str(self.universe.source["id"]),
                          self.universe.span_ids[0],
                          str(self.universe.candidates["weekly"]["id"]),
                          str(self.universe.decisions["promote_weekly"]["id"]),
                          str(self.universe.entries["weekly"]["id"])]:
            self.assertIsNotNone(self.store.get_record(record_id))
        self.assertIsNone(self.store.get_record("fact_" + "0" * 24))
        with self.assertRaises(NotFoundError):
            self.store.get_record("unknown_prefix_123")


class ActiveLedgerTests(unittest.TestCase):
    """The active_ledger view and the time-travel read."""

    def setUp(self):
        self.universe = build_universe()
        self.store = MemoryStore()
        seed_raw(self.store, self.universe, include_retract=True)

    def tearDown(self):
        self.store.close()

    def test_structural_active_excludes_superseded_and_overridden(self):
        active_ids = {e["id"] for e in self.store.active_entries()}
        self.assertEqual(active_ids,
                         {str(self.universe.entries["daily"]["id"])})

    def test_time_travel_returns_the_truth_of_that_moment(self):
        weekly_id = str(self.universe.entries["weekly"]["id"])
        daily_id = str(self.universe.entries["daily"]["id"])
        # Before T2 the (since-superseded) weekly cadence was the truth.
        before = {e["id"] for e in
                  self.store.active_entries(as_of="2026-06-01T12:00:00Z")}
        self.assertIn(weekly_id, before)
        self.assertNotIn(daily_id, before)
        # From T2 the successor is the truth.
        after = {e["id"] for e in self.store.active_entries(as_of=T2)}
        self.assertIn(daily_id, after)
        self.assertNotIn(weekly_id, after)

    def test_retracted_entries_are_withdrawn_at_every_point_in_time(self):
        budget_id = str(self.universe.entries["budget"]["id"])
        for as_of in (T1, T2, "2027-01-01T00:00:00Z"):
            ids = {e["id"] for e in self.store.active_entries(as_of=as_of)}
            self.assertNotIn(budget_id, ids)

    def test_as_of_must_be_iso8601(self):
        with self.assertRaises(ValueError):
            self.store.active_entries(as_of="not-a-date")

    def test_max_privacy_filters_above_clearance(self):
        # All seeded entries default to internal; a public ceiling
        # hides them, an internal ceiling shows them.
        self.assertEqual(self.store.active_entries(max_privacy="public"), [])
        self.assertEqual(len(self.store.active_entries(
            max_privacy="internal")), 1)

    def test_epoch_parsing_is_timezone_aware(self):
        utc = _epoch_or_none("2026-02-12T09:38:00Z")
        ist = _epoch_or_none("2026-02-12T15:08:00+05:30")
        self.assertEqual(utc, ist)
        self.assertIsNone(_epoch_or_none(None))
        self.assertIsNone(_epoch_or_none("garbage"))


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.universe = build_universe()
        self.store = MemoryStore()
        seed_raw(self.store, self.universe)

    def tearDown(self):
        self.store.close()

    def test_search_is_deterministic_and_ranked(self):
        first = self.store.search_entries("deploy cadence daily")
        second = self.store.search_entries("deploy cadence daily")
        self.assertEqual([h.to_dict() for h in first],
                         [h.to_dict() for h in second])
        self.assertEqual(first[0].entry_id,
                         str(self.universe.entries["daily"]["id"]))
        self.assertTrue(all(0.0 < h.score <= 1.0 for h in first))

    def test_search_marks_superseded_hits(self):
        hits = {h.entry_id: h for h in self.store.search_entries("deploys")}
        weekly_id = str(self.universe.entries["weekly"]["id"])
        self.assertTrue(hits[weekly_id].superseded)
        self.assertEqual(
            hits[weekly_id].decision_id,
            str(self.universe.decisions["supersede"]["id"]))

    def test_search_active_only_excludes_superseded(self):
        hits = self.store.search_entries("deploys",
                                         include_superseded=False)
        ids = {h.entry_id for h in hits}
        self.assertNotIn(str(self.universe.entries["weekly"]["id"]), ids)

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.store.search_entries("  --  "), [])

    def test_limit_is_respected(self):
        hits = self.store.search_entries("deploys", limit=1)
        self.assertEqual(len(hits), 1)


class HousekeepingTests(unittest.TestCase):
    def test_extracted_columns_clean_after_seed(self):
        store = MemoryStore()
        seed_raw(store, build_universe(), include_retract=True)
        self.assertEqual(store.check_extracted_columns(), [])
        store.close()

    def test_extracted_column_drift_is_reported(self):
        store = MemoryStore()
        universe = build_universe()
        # Seed one source with a deliberately wrong extracted column.
        with store._txn() as conn:
            store._arm_guard(conn)
            payload = dict(universe.source)
            conn.execute(
                "INSERT INTO sources (id, tenant_id, record_type, title,"
                " captured_at, privacy_tier, payload, created_at)"
                " VALUES (?, ?, 'WRONG_TYPE', ?, ?, ?, ?, 'now')",
                (str(payload["id"]), store.tenant_id,
                 str(payload["title"]), payload["captured_at"],
                 payload["privacy_class"], canonical_json(payload)))
            store._disarm_guard(conn)
        findings = store.check_extracted_columns()
        self.assertEqual(len(findings), 1)
        self.assertIn("record_type drift", findings[0])
        store.close()

    def test_counts(self):
        store = MemoryStore()
        seed_raw(store, build_universe(), include_retract=True)
        counts = store.counts()
        self.assertEqual(counts["sources"], 1)
        self.assertEqual(counts["spans"], 3)
        self.assertEqual(counts["candidates"], 4)
        self.assertEqual(counts["reducer_decisions"], 5)
        self.assertEqual(counts["ledger_entries"], 3)
        self.assertEqual(counts["supersessions"], 1)
        self.assertEqual(counts["status_overrides"], 1)
        store.close()

    def test_tenants_are_isolated_on_the_same_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.sqlite3"
            store_a = MemoryStore(path, tenant_id="tenant-a")
            seed_raw(store_a, build_universe())
            store_b = MemoryStore(path, tenant_id="tenant-b")
            try:
                self.assertEqual(store_b.list_ledger_entries(), [])
                self.assertEqual(store_b.counts()["sources"], 0)
                self.assertEqual(len(store_a.list_ledger_entries()), 3)
            finally:
                store_a.close()
                store_b.close()

    def test_empty_tenant_id_rejected(self):
        with self.assertRaises(ValueError):
            MemoryStore(tenant_id="")

    def test_context_manager_closes(self):
        with MemoryStore() as store:
            self.assertEqual(store.counts()["sources"], 0)


if __name__ == "__main__":
    unittest.main()
