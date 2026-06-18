# INVARIANT 3 — Reciprocal supersession.
#
# Product language: "When a fact is corrected, the old version and
# the new version point at each other, the temporal handoff is
# exact, and the correction is itself an authorized decision. Two
# reviewers correcting the same fact at the same moment cannot fork
# history: exactly one correction wins, the other is told — cleanly
# — who won."
#
# Executable form: after a gate supersession the materialized
# records satisfy the library's reciprocity validators; a
# two-thread race on the same target produces exactly one
# supersession edge, one clean ConflictError naming the winner, and
# a store that still validates end to end.

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from agent_memory_contracts import validate_ledger_bundle
from agent_memory_contracts.runtime.anchors import (
    verify_chain,
    verify_coverage,
)
from agent_memory_contracts.runtime.gate import MemoryGate
from agent_memory_contracts.runtime.store import ConflictError, MemoryStore

from ..runtime_seed import (
    T2,
    T_CREATED,
    _decision,
    _fact,
    build_universe,
)


def _seed_base(gate: MemoryGate, universe) -> None:
    gate.submit_source(universe.source, created_at=T_CREATED)
    for span in universe.spans:
        gate.submit_span(span, created_at=T_CREATED)
    for candidate in universe.candidates.values():
        gate.submit_candidate(candidate, created_at=T_CREATED)
    gate.promote(universe.decisions["promote_weekly"],
                 [universe.entries["weekly"]], created_at=T_CREATED)


def _rival_supersession(universe, *, valid_from: str):
    """A second, distinct successor for the weekly entry."""
    rival_entry = _fact(universe.candidates["daily"],
                        source_id=str(universe.source["id"]),
                        decision_id="redmem_x", valid_from=valid_from,
                        supersedes=[str(universe.entries["weekly"]["id"])])
    rival_decision = _decision(
        "supersede",
        sorted([str(universe.candidates["weekly"]["id"]),
                str(universe.candidates["daily"]["id"])]),
        sorted([str(universe.entries["weekly"]["id"]),
                str(rival_entry["id"])]),
        sorted([universe.span_ids[0], universe.span_ids[2]]),
        f"rival supersession effective {valid_from}")
    rival_entry["reducer_decision_id"] = str(rival_decision["id"])
    return rival_decision, rival_entry


class ReciprocityTests(unittest.TestCase):
    def test_materialized_reciprocity_always_validates(self):
        universe = build_universe()
        store = MemoryStore()
        gate = MemoryGate(store)
        _seed_base(gate, universe)
        gate.promote(universe.decisions["supersede"],
                     [universe.entries["daily"]],
                     supersessions=[(str(universe.entries["weekly"]["id"]),
                                     str(universe.entries["daily"]["id"]))],
                     created_at=T_CREATED)
        weekly = store.get_ledger_entry(
            str(universe.entries["weekly"]["id"]))
        daily = store.get_ledger_entry(str(universe.entries["daily"]["id"]))
        # Reciprocity, both directions.
        self.assertIn(daily["id"], weekly["superseded_by"])
        self.assertIn(weekly["id"], daily["supersedes"])
        # The temporal handoff is exact.
        self.assertEqual(weekly["valid_until"], daily["valid_from"])
        self.assertEqual(weekly["valid_until"], T2)
        # The correction is itself an authorized decision, and the
        # library's validators accept the materialized graph.
        self.assertEqual(weekly["reducer_decision_id"],
                         daily["reducer_decision_id"])
        records = store.all_records()
        validate_ledger_bundle(
            source_records=records["sources"],
            episode_records=records["episodes"],
            evidence_spans=records["spans"],
            candidate_records=records["candidates"],
            reducer_decisions=records["decisions"],
            ledger_entries=records["entries"],
        )
        store.close()


class SupersessionRaceTests(unittest.TestCase):
    def test_concurrent_conflicting_supersessions_one_winner(self):
        universe = build_universe()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.sqlite3"
            seed_store = MemoryStore(path)
            _seed_base(MemoryGate(seed_store), universe)
            seed_store.close()

            batch_a = (universe.decisions["supersede"],
                       universe.entries["daily"],
                       (str(universe.entries["weekly"]["id"]),
                        str(universe.entries["daily"]["id"])))
            rival_decision, rival_entry = _rival_supersession(
                universe, valid_from="2026-06-03T10:00:00Z")
            batch_b = (rival_decision, rival_entry,
                       (str(universe.entries["weekly"]["id"]),
                        str(rival_entry["id"])))

            barrier = threading.Barrier(2)
            results: dict[str, object] = {}

            def contender(name: str, batch) -> None:
                decision, entry, edge = batch
                store = MemoryStore(path)  # own connection, same file
                gate = MemoryGate(store, actor=f"racer-{name}")
                barrier.wait()
                try:
                    receipt = gate.promote(decision, [entry],
                                           supersessions=[edge],
                                           created_at=T_CREATED)
                    results[name] = receipt
                except Exception as exc:  # noqa: BLE001 - recorded
                    results[name] = exc
                finally:
                    store.close()

            threads = [threading.Thread(target=contender,
                                        args=("a", batch_a)),
                       threading.Thread(target=contender,
                                        args=("b", batch_b))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=30)

            outcomes = [results.get("a"), results.get("b")]
            winners = [r for r in outcomes
                       if not isinstance(r, Exception)]
            losers = [r for r in outcomes if isinstance(r, Exception)]
            self.assertEqual(len(winners), 1,
                             f"exactly one promotion must win: {outcomes}")
            self.assertEqual(len(losers), 1)
            # The loser got a clean, structured conflict naming
            # the winning decision — not a deadlock, not a
            # corrupted store, not a generic 500.
            loser = losers[0]
            self.assertIsInstance(loser, ConflictError)
            self.assertEqual(loser.conflict_kind,
                             "entry_already_superseded")
            self.assertEqual(loser.winning_decision_id,
                             winners[0].decision_id)

            # Exactly one edge; the store still validates,
            # anchors verify, coverage is complete.
            store = MemoryStore(path)
            edges = store.supersession_edges()
            self.assertEqual(len(edges), 1)
            self.assertEqual(edges[0][2], winners[0].decision_id)
            records = store.all_records()
            validate_ledger_bundle(
                source_records=records["sources"],
                episode_records=records["episodes"],
                evidence_spans=records["spans"],
                candidate_records=records["candidates"],
                reducer_decisions=records["decisions"],
                ledger_entries=records["entries"],
            )
            self.assertTrue(verify_chain(store).ok)
            self.assertTrue(verify_coverage(store).ok)
            store.close()

    def test_sequential_double_supersession_same_clean_conflict(self):
        # The race's deterministic twin: the second supersession of
        # an already-superseded entry gets the identical structured
        # error (the API behaves the same under race and replay).
        universe = build_universe()
        store = MemoryStore()
        gate = MemoryGate(store)
        _seed_base(gate, universe)
        first = gate.promote(
            universe.decisions["supersede"], [universe.entries["daily"]],
            supersessions=[(str(universe.entries["weekly"]["id"]),
                            str(universe.entries["daily"]["id"]))],
            created_at=T_CREATED)
        rival_decision, rival_entry = _rival_supersession(
            universe, valid_from="2026-06-04T10:00:00Z")
        with self.assertRaises(ConflictError) as ctx:
            gate.promote(rival_decision, [rival_entry],
                         supersessions=[(
                             str(universe.entries["weekly"]["id"]),
                             str(rival_entry["id"]))])
        self.assertEqual(ctx.exception.conflict_kind,
                         "entry_already_superseded")
        self.assertEqual(ctx.exception.winning_decision_id,
                         first.decision_id)
        store.close()


if __name__ == "__main__":
    unittest.main()
