# INVARIANT 2 — Full provenance.
#
# Product language: "Pick any fact your AI relies on. We can walk
# you, in one query, from that fact to the decision that authorized
# it, to the extracted claim that proposed it, to the exact lines
# of the exact document it came from. Every fact. No exceptions —
# including the ones that have since been corrected."
#
# Executable form: a chain walk (entry -> decision -> candidate ->
# span -> source) over every ledger entry must resolve completely —
# on the synthetic universe AND on the full seeded Arthashila NBFC
# corpus through the runtime; the library's audit pack must agree
# (complete chains == ledger entries, incomplete == 0).

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_memory_contracts import compute_audit_pack
from agent_memory_contracts.runtime.gate import MemoryGate
from agent_memory_contracts.runtime.store import MemoryStore

from ..runtime_seed import build_universe, seed_via_gate

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
DATASET_DIR = Path(
    "/sessions/inspiring-magical-a8efd8/mnt/Personal-Offboarding/"
    "launch-week/05-demo-dataset")
_DATASET_AVAILABLE = (DATASET_DIR / "walkthrough.md").exists()


def walk_provenance(store: MemoryStore) -> list[str]:
    """Walk every ledger entry's full chain through store reads.
    Returns the breaks (empty = every chain complete)."""
    breaks: list[str] = []
    for entry in store.list_ledger_entries():
        entry_id = str(entry.get("id") or "")
        # entry -> decision (the governing decision after
        # materialization: supersede/override handoff included).
        decision_id = str(entry.get("reducer_decision_id") or "")
        decision = store.get_decision(decision_id)
        if decision is None:
            breaks.append(f"{entry_id}: decision {decision_id} missing")
            continue
        if entry_id not in (decision.get("target_ledger_entry_ids") or []):
            breaks.append(f"{entry_id}: not targeted by {decision_id}")
        # entry -> candidate(s).
        for candidate_id in entry.get("candidate_ids") or []:
            candidate = store.get_candidate(str(candidate_id))
            if candidate is None:
                breaks.append(f"{entry_id}: candidate {candidate_id} "
                              f"missing")
                continue
            if str(candidate_id) not in (
                    decision.get("target_candidate_ids") or []):
                breaks.append(f"{entry_id}: candidate {candidate_id} not "
                              f"authorized by {decision_id}")
        # entry -> span(s) -> source(s).
        span_ids = list(entry.get("evidence_span_ids") or [])
        if not span_ids:
            breaks.append(f"{entry_id}: no evidence spans")
        for span_id in span_ids:
            span = store.get_span(str(span_id))
            if span is None:
                breaks.append(f"{entry_id}: span {span_id} missing")
                continue
            source = store.get_source(str(span.get("source_id") or ""))
            if source is None:
                breaks.append(f"{entry_id}: source for span {span_id} "
                              f"missing")
        # decision evidence must cover the entry's evidence.
        decision_spans = set(decision.get("evidence_span_ids") or [])
        if not set(map(str, span_ids)).issubset(decision_spans):
            breaks.append(f"{entry_id}: evidence not covered by "
                          f"{decision_id}")
    return breaks


def assert_audit_pack_agrees(case: unittest.TestCase,
                             store: MemoryStore) -> None:
    records: list[dict] = []
    by_plane = store.all_records()
    for plane in ("sources", "spans", "candidates", "decisions",
                  "entries"):
        records.extend(by_plane[plane])
    pack = compute_audit_pack(records, as_of="2026-06-08T09:00:00Z")
    case.assertEqual(pack.incomplete_chain_count, 0)
    case.assertEqual(pack.complete_chain_count,
                     len(by_plane["entries"]))


class SyntheticProvenanceTests(unittest.TestCase):
    def test_every_chain_resolves_including_corrected_facts(self):
        store = MemoryStore()
        gate = MemoryGate(store)
        seed_via_gate(gate, build_universe(), include_retract=True)
        self.assertEqual(walk_provenance(store), [])
        assert_audit_pack_agrees(self, store)
        store.close()

    def test_the_walk_actually_detects_breaks(self):
        # Adversarial control: a hostile deletion breaks the chain
        # and the walk reports it (the walk is not a tautology).
        import sqlite3
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "store.sqlite3"
            store = MemoryStore(path)
            gate = MemoryGate(store)
            universe = build_universe()
            seed_via_gate(gate, universe)
            conn = sqlite3.connect(path)
            try:
                for name in [str(r[0]) for r in conn.execute(
                        "SELECT name FROM sqlite_master"
                        " WHERE type = 'trigger'")]:
                    conn.execute(f"DROP TRIGGER {name}")
                conn.execute("DELETE FROM spans WHERE id = ?",
                             (universe.span_ids[0],))
                conn.commit()
            finally:
                conn.close()
            breaks = walk_provenance(store)
            self.assertTrue(any("missing" in b for b in breaks))
            store.close()


@unittest.skipUnless(_DATASET_AVAILABLE,
                     f"Arthashila dataset not available at {DATASET_DIR}")
class ArthashilaProvenanceTests(unittest.TestCase):
    """The chain walk over the whole seeded demo dataset, through
    the runtime store the --runtime pipeline built."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp())
        proc = subprocess.run(
            ["python3", "examples/arthashila_demo/build.py", "--runtime",
             "--dataset", str(DATASET_DIR), "--out", str(cls.tmpdir)],
            capture_output=True, text=True,
            env={**os.environ, "PYTHONPATH": "src"}, cwd=REPO_ROOT)
        assert proc.returncode == 0, proc.stderr
        cls.summary = json.loads(
            (cls.tmpdir / "summary.json").read_text(encoding="utf-8"))
        cls.store = MemoryStore(cls.tmpdir / "runtime-store.sqlite3")

    @classmethod
    def tearDownClass(cls):
        cls.store.close()
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_every_arthashila_chain_resolves(self):
        self.assertEqual(walk_provenance(self.store), [])
        assert_audit_pack_agrees(self, self.store)

    def test_the_superseded_rate_cell_keeps_its_full_chain(self):
        # The corrected (v3) cell is still fully walkable — a
        # correction is a governed event, not an erasure.
        superseded = [e for e in self.store.list_ledger_entries()
                      if e["status"] == "superseded"]
        self.assertEqual(len(superseded), 1)
        entry = superseded[0]
        decision = self.store.get_decision(entry["reducer_decision_id"])
        self.assertEqual(decision["decision_type"], "supersede")
        self.assertTrue(entry["superseded_by"])

    def test_quarantined_poisons_have_decision_trails_not_entries(self):
        rejected = self.store.rejected_candidate_ids()
        self.assertEqual(len(rejected), 2)
        ledger_candidates = {
            str(c) for e in self.store.list_ledger_entries()
            for c in e.get("candidate_ids") or []}
        for candidate_id in rejected:
            self.assertNotIn(candidate_id, ledger_candidates)
            self.assertIsNotNone(self.store.get_candidate(candidate_id))


if __name__ == "__main__":
    unittest.main()
