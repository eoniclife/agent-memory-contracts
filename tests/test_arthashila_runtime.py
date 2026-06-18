"""Smoke tests for ``examples/arthashila_demo/build.py --runtime``.

The same NBFC corpus as test_arthashila_demo.py, but through the
sqlite reference runtime: every record enters via the gate, the
poisons are quarantined by recorded reject decisions, the v3 -> v4
supersession is an edge materialized at read, the walkthrough
question answers 12.50% from a receipted ContextPack built out of
the store, the audit pack is emitted from store state, and the
anchor chain + coverage verify.

Asserted on top of the static-path claims:

1. The trusted-ledger fingerprint is byte-identical to the static
   path's -- the runtime is an equivalent implementation of the
   same governed graph, not a parallel one.
2. Re-running against the same output directory is write-idempotent
   (content-derived ids): no new rows except one fresh ``verified``
   attestation anchor.

If the dataset directory is not present (e.g. CI), the tests skip
with a clear reason instead of failing.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DATASET_DIR = Path(
    "/sessions/inspiring-magical-a8efd8/mnt/Personal-Offboarding/"
    "launch-week/05-demo-dataset")

_DATASET_AVAILABLE = (DATASET_DIR / "walkthrough.md").exists()
_SKIP_REASON = (f"Arthashila demo dataset not available at {DATASET_DIR}; "
                f"pass --dataset to run the demo elsewhere")


def _run(out_dir: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", "examples/arthashila_demo/build.py", "--runtime",
         "--dataset", str(DATASET_DIR), "--out", str(out_dir)],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": "src"},
        cwd=REPO_ROOT)


@unittest.skipUnless(_DATASET_AVAILABLE, _SKIP_REASON)
class ArthashilaRuntimeTests(unittest.TestCase):
    """Run the runtime adapter once; assert on the summary, the
    store, and a second (idempotent) run."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp())
        cls.proc = _run(cls.tmpdir)
        summary_path = cls.tmpdir / "summary.json"
        cls.summary = (json.loads(summary_path.read_text(encoding="utf-8"))
                       if summary_path.exists() else None)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_runs_cleanly(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertIsNotNone(self.summary)
        self.assertEqual(self.summary["mode"], "runtime")

    def test_poisons_quarantined_with_documented_kill_chains(self):
        quarantine = {q["fixture_key"]: q
                      for q in self.summary["quarantine"]}
        self.assertEqual(len(quarantine), 2)
        self.assertEqual(quarantine["poison1_payout"]["reason_code"],
                         "untrusted_source")
        self.assertEqual(quarantine["poison2_rate"]["reason_code"],
                         "no_authorizing_chain")

    def test_demo_question_yields_12_50_with_citations(self):
        answer = self.summary["answer"]
        self.assertEqual(answer["rate"], "12.50%")
        self.assertEqual(answer["card_rate"], "12.75%")
        self.assertGreaterEqual(len(answer["citations"]), 2)
        for citation in answer["citations"]:
            self.assertTrue(citation["span_id"].startswith("span_"))
            self.assertTrue(citation["fact_id"].startswith("fact_"))
            self.assertTrue(citation["excerpt"])

    def test_counts_match_the_corpus(self):
        counts = self.summary["counts"]
        self.assertEqual(counts["sources"], 37)
        self.assertEqual(counts["evidence_spans"], 14)
        self.assertEqual(counts["candidates"], 11)
        self.assertEqual(counts["ledger_entries"], 9)
        self.assertEqual(counts["active_ledger_entries"], 8)
        self.assertEqual(counts["superseded_ledger_entries"], 1)

    def test_audit_pack_emitted_from_store_state(self):
        pack_info = self.summary["audit_pack"]
        self.assertEqual(pack_info["complete_chain_count"], 9)
        self.assertEqual(pack_info["incomplete_chain_count"], 0)
        self.assertEqual(pack_info["rejected_count"], 2)
        self.assertEqual(pack_info["supersession_count"], 1)
        text = Path(pack_info["path"]).read_text(encoding="utf-8")
        self.assertIn("untrusted_source", text)
        self.assertIn("no_authorizing_chain", text)
        self.assertIn(pack_info["bundle_fingerprint"], text)

    def test_anchor_chain_verified_and_coverage_complete(self):
        runtime = self.summary["runtime"]
        self.assertTrue(runtime["chain_ok"])
        self.assertTrue(runtime["coverage_ok"])
        self.assertGreater(runtime["anchors"], 70)
        self.assertEqual(runtime["supersession_edges"], 1)
        self.assertTrue(Path(runtime["db_path"]).exists())

    def test_pack_is_receipted_and_poison_probe_grounded(self):
        runtime = self.summary["runtime"]
        self.assertTrue(runtime["pack_id"].startswith("ctx_"))
        self.assertEqual(len(runtime["pack_fingerprint"]), 64)
        self.assertEqual(runtime["pack_selected"], 8)
        probe = runtime["payout_probe"]
        self.assertEqual(probe["status"], "answered")
        self.assertIn("1.00% of disbursed amount on LAP",
                      probe["answer_text"])
        self.assertNotIn("clawback withdrawn", probe["answer_text"])

    def test_fingerprint_parity_with_the_static_path(self):
        # The runtime is an equivalent implementation of the same
        # governed graph: byte-identical trusted-ledger fingerprint.
        static_dir = Path(tempfile.mkdtemp())
        try:
            proc = subprocess.run(
                ["python3", "examples/arthashila_demo/build.py",
                 "--dataset", str(DATASET_DIR), "--out", str(static_dir)],
                capture_output=True, text=True,
                env={**os.environ, "PYTHONPATH": "src"},
                cwd=REPO_ROOT)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            static = json.loads(
                (static_dir / "summary.json").read_text(encoding="utf-8"))
            self.assertEqual(self.summary["trusted_ledger_fingerprint"],
                             static["trusted_ledger_fingerprint"])
            self.assertEqual(self.summary["answer"]["explanation"],
                             static["answer"]["explanation"])
        finally:
            shutil.rmtree(static_dir, ignore_errors=True)

    def test_rerun_is_write_idempotent(self):
        proc2 = _run(self.tmpdir)
        self.assertEqual(proc2.returncode, 0, proc2.stderr)
        summary2 = json.loads(
            (self.tmpdir / "summary.json").read_text(encoding="utf-8"))
        # Everything identical except the anchor count: a re-run
        # replays every write as a no-op (content-derived ids) and
        # appends exactly one fresh `verified` attestation anchor.
        self.assertEqual(summary2["runtime"]["anchors"],
                         self.summary["runtime"]["anchors"] + 1)
        s1 = {k: v for k, v in self.summary.items() if k != "runtime"}
        s2 = {k: v for k, v in summary2.items() if k != "runtime"}
        self.assertEqual(s1, s2)
        r1 = {k: v for k, v in self.summary["runtime"].items()
              if k not in ("anchors", "anchors_checked")}
        r2 = {k: v for k, v in summary2["runtime"].items()
              if k not in ("anchors", "anchors_checked")}
        self.assertEqual(r1, r2)


if __name__ == "__main__":
    unittest.main()
