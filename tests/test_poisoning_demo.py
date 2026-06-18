"""Smoke tests for examples/poisoning_demo/.

The demo's claims are its invariants, so the test asserts them:

- Store A (silent) is poisoned: after the attack it serves the
  forged payout rate as truth.
- Store B (governed) is clean: the forged candidates are rejected
  and quarantined in the candidate plane with rejection receipts,
  and the trusted ledger's ``bundle_fingerprint`` is unchanged by
  the attack.
- The demo generates ``report.md`` (and ``summary.json``) and runs
  cleanly as a script.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from examples.poisoning_demo.governed_store import (
    REJECT_NO_AUTHORIZING_CHAIN,
    REJECT_UNTRUSTED_SOURCE,
)
from examples.poisoning_demo.run import main as run_demo_main


REPO_ROOT = Path(__file__).resolve().parent.parent


class PoisoningDemoInvariantTests(unittest.TestCase):
    """Run the demo in-process once; assert the key invariants."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = Path(tempfile.mkdtemp())
        cls.result = run_demo_main(out_dir=cls.tmpdir)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmpdir, ignore_errors=True)

    def test_store_a_is_poisoned(self):
        a = self.result["store_a"]
        self.assertTrue(a["poisoned"])
        self.assertIn("1.00%", a["answer_before_attack"])
        self.assertIn("1.75%", a["answer_after_attack"])

    def test_store_a_memory_changed_by_attack(self):
        a = self.result["store_a"]
        self.assertNotEqual(a["fingerprint_before"], a["fingerprint_after"])

    def test_store_b_is_clean(self):
        b = self.result["store_b"]
        self.assertFalse(b["poisoned"])
        self.assertIn("1.00%", b["answer_before_attack"])
        self.assertIn("1.00%", b["answer_after_attack"])
        self.assertNotIn("1.75%", b["answer_after_attack"])

    def test_store_b_trusted_fingerprint_unchanged(self):
        b = self.result["store_b"]
        self.assertEqual(b["trusted_fingerprint_before"],
                         b["trusted_fingerprint_after"])
        # And it is a real fingerprint, not an empty placeholder.
        self.assertEqual(len(b["trusted_fingerprint_before"]), 64)

    def test_store_b_rejected_and_quarantined_the_forgery(self):
        b = self.result["store_b"]
        self.assertEqual(b["rejected_during_attack"], 2)
        self.assertEqual(b["promoted_during_attack"], 0)
        self.assertEqual(b["quarantined_candidates"], 2)
        receipts = b["rejection_receipts"]
        self.assertEqual(len(receipts), 2)
        for receipt in receipts:
            self.assertEqual(receipt["reason_code"],
                             REJECT_UNTRUSTED_SOURCE)
            self.assertTrue(receipt["decision_id"].startswith("redmem_"))
            self.assertTrue(receipt["candidate_id"].startswith("cand_"))
            # The kill chain covers both failure modes: spoofed
            # sender AND no authorizing chain.
            self.assertIn(REJECT_UNTRUSTED_SOURCE, receipt["rationale"])
            self.assertIn("no recognised authorization reference",
                          receipt["rationale"])
            self.assertIn("provenance", receipt["failed_checks"])

    def test_store_b_promoted_only_the_policy_facts(self):
        b = self.result["store_b"]
        self.assertEqual(b["trusted_entries"], 2)

    def test_report_md_generated_with_key_content(self):
        report_path = self.tmpdir / "report.md"
        self.assertTrue(report_path.exists())
        text = report_path.read_text(encoding="utf-8")
        self.assertIn("# Poisoning demo report", text)
        self.assertIn("rejection receipts", text.lower())
        self.assertIn(REJECT_UNTRUSTED_SOURCE, text)
        self.assertIn("Unchanged: **True**", text)
        b = self.result["store_b"]
        self.assertIn(b["trusted_fingerprint_before"], text)
        for receipt in b["rejection_receipts"]:
            self.assertIn(receipt["decision_id"], text)

    def test_summary_json_generated_and_matches_result(self):
        summary_path = self.tmpdir / "summary.json"
        self.assertTrue(summary_path.exists())
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
        self.assertEqual(payload, self.result)

    def test_reject_reason_codes_are_distinct_constants(self):
        self.assertNotEqual(REJECT_UNTRUSTED_SOURCE,
                            REJECT_NO_AUTHORIZING_CHAIN)


class PoisoningDemoDeterminismTests(unittest.TestCase):
    def test_two_runs_produce_identical_results(self):
        tmp_a = Path(tempfile.mkdtemp())
        tmp_b = Path(tempfile.mkdtemp())
        try:
            r1 = run_demo_main(out_dir=tmp_a)
            r2 = run_demo_main(out_dir=tmp_b)
            self.assertEqual(r1, r2)
            self.assertEqual(
                (tmp_a / "report.md").read_text(encoding="utf-8"),
                (tmp_b / "report.md").read_text(encoding="utf-8"))
        finally:
            shutil.rmtree(tmp_a, ignore_errors=True)
            shutil.rmtree(tmp_b, ignore_errors=True)


class PoisoningDemoScriptTests(unittest.TestCase):
    def test_runs_cleanly_as_a_script(self):
        tmpdir = Path(tempfile.mkdtemp())
        try:
            proc = subprocess.run(
                ["python3", "examples/poisoning_demo/run.py",
                 "--out", str(tmpdir)],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": "src"},
                cwd=REPO_ROOT,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Store B (governed)", proc.stdout)
            self.assertIn("untrusted_source", proc.stdout)
            self.assertTrue((tmpdir / "report.md").exists())
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
