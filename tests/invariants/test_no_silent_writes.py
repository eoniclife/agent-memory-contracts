# INVARIANT 1 — No silent writes.
#
# Product language: "Nothing your AI treats as a trusted fact got
# there without an authorizing decision. There is no API, no code
# path, and no raw-SQL shortcut that writes trusted memory behind
# the reviewer's back — and if someone rips the locks off the
# database itself, the audit layer still flags every row no
# decision accounts for."
#
# Executable form: every public method of the store and the gate is
# fuzzed with trusted-plane payloads (ledger entries, reducer
# decisions, edge tuples, trusted ids). None of them may grow the
# trusted tables — except a fully valid promote(), the single
# authorized door, which is the positive control.

from __future__ import annotations

import inspect
import sqlite3
import unittest
from typing import Any

from agent_memory_contracts.runtime.anchors import (
    verify_chain,
    verify_coverage,
)
from agent_memory_contracts.runtime.gate import MemoryGate
from agent_memory_contracts.runtime.store import MemoryStore

from ..runtime_seed import T_CREATED, build_universe

#: The tables whose growth means "trusted memory changed".
TRUSTED_TABLES = ("ledger_entries", "reducer_decisions", "supersessions",
                  "status_overrides")

#: Methods that are lifecycle plumbing, not write vectors.
_EXCLUDED = {"close"}


def _trusted_counts(store: MemoryStore) -> dict[str, int]:
    counts = store.counts()
    return {table: counts[table] for table in TRUSTED_TABLES}


def _ledger_write_vectors(universe: Any) -> list[Any]:
    """Payloads an attacker would push at every surface: a valid
    ledger entry, a valid reducer decision, supersession tuples,
    trusted-plane ids, and junk."""
    entry = dict(universe.entries["weekly"])
    decision = dict(universe.decisions["promote_weekly"])
    return [
        entry,
        decision,
        [entry],
        [(str(entry["id"]), str(universe.entries["daily"]["id"]))],
        str(entry["id"]),
        str(decision["id"]),
        "fact_" + "0" * 24,
        {"id": "fact_" + "1" * 24, "ledger_type": "fact",
         "status": "active"},
    ]


def _fuzz_object(target: Any, vectors: list[Any]) -> list[str]:
    """Call every public method with every vector arrangement that
    fits its arity. Exceptions are expected (and swallowed); what
    matters is the table counts afterwards. Returns the list of
    attempted call signatures, for the record."""
    attempted: list[str] = []
    for name, method in inspect.getmembers(target):
        if name.startswith("_") or name in _EXCLUDED:
            continue
        if not callable(method) or inspect.isclass(method):
            continue
        argument_sets: list[tuple[Any, ...]] = [()]
        for vector in vectors:
            argument_sets.append((vector,))
            for second in vectors:
                argument_sets.append((vector, second))
        for args in argument_sets:
            attempted.append(f"{name}/{len(args)}")
            try:
                method(*args)
            except Exception:
                pass
    return attempted


class NoSilentWritesFuzzTests(unittest.TestCase):
    def test_no_public_method_writes_trusted_memory_except_promote(self):
        universe = build_universe()
        store = MemoryStore()
        gate = MemoryGate(store)
        vectors = _ledger_write_vectors(universe)
        baseline = _trusted_counts(store)

        attempted_store = _fuzz_object(store, vectors)
        attempted_gate = _fuzz_object(gate, vectors)

        self.assertGreater(len(attempted_store) + len(attempted_gate), 100)
        self.assertEqual(_trusted_counts(store), baseline,
                         "a fuzzed call grew a trusted table")
        # The fuzz also failed to sneak anything into the
        # untrusted planes with trusted-plane payloads.
        self.assertEqual(store.counts()["candidates"], 0)

        # Positive control: the single authorized door works, and
        # is the ONLY thing that moves the trusted counts.
        gate.submit_source(universe.source, created_at=T_CREATED)
        for span in universe.spans:
            gate.submit_span(span, created_at=T_CREATED)
        gate.submit_candidate(universe.candidates["weekly"],
                              created_at=T_CREATED)
        before = _trusted_counts(store)
        gate.promote(universe.decisions["promote_weekly"],
                     [universe.entries["weekly"]], created_at=T_CREATED)
        after = _trusted_counts(store)
        self.assertEqual(after["ledger_entries"],
                         before["ledger_entries"] + 1)
        self.assertEqual(after["reducer_decisions"],
                         before["reducer_decisions"] + 1)
        self.assertTrue(verify_chain(store).ok)
        self.assertTrue(verify_coverage(store).ok)
        store.close()

    def test_raw_sql_writes_are_blocked_by_the_schema_itself(self):
        store = MemoryStore()
        for sql in [
            "INSERT INTO ledger_entries (id, tenant_id, record_type,"
            " status, decision_id, payload, created_at) VALUES ('fact_x',"
            " 'default', 'fact', 'active', 'redmem_x', '{}', 'now')",
            "INSERT INTO reducer_decisions (id, tenant_id, decision_type,"
            " payload, created_at) VALUES ('redmem_x', 'default',"
            " 'promote', '{}', 'now')",
            "INSERT INTO supersessions (tenant_id, superseded_id,"
            " superseding_id, decision_id, created_at) VALUES ('default',"
            " 'a', 'b', 'c', 'now')",
        ]:
            with self.assertRaises(sqlite3.IntegrityError):
                store._conn.execute(sql)
        store.close()

    def test_even_a_guard_bypass_cannot_write_silently(self):
        # The triggers can be bypassed by hand-arming the guard —
        # raw DB access is the hostile-DBA threat model. The write
        # lands, but it is NOT silent: coverage flags the row no
        # anchor accounts for. The invariant holds at the audit
        # layer when the SQL layer is subverted.
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.sqlite3"
            store = MemoryStore(path)
            raw = sqlite3.connect(path)
            try:
                raw.execute("BEGIN IMMEDIATE")
                raw.execute("UPDATE _write_guard SET armed = 1")
                raw.execute(
                    "INSERT INTO ledger_entries (id, tenant_id,"
                    " record_type, status, decision_id, payload,"
                    " created_at) VALUES ('fact_bypass00000000000000',"
                    " 'default', 'fact', 'active', 'redmem_forged',"
                    " '{\"id\": \"fact_bypass00000000000000\"}', 'now')")
                raw.execute("UPDATE _write_guard SET armed = 0")
                raw.execute("COMMIT")
            finally:
                raw.close()
            report = verify_coverage(store)
            self.assertFalse(report.ok)
            self.assertIn("ledger_entries:fact_bypass00000000000000",
                          report.unanchored)
            store.close()


if __name__ == "__main__":
    unittest.main()
