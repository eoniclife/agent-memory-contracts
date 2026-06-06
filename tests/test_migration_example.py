"""Smoke test for the SQLite-to-contracts migration example.

Runs ``docs/migration_example.py`` as a subprocess and confirms:
- exit 0
- the output mentions the expected section headings
- the deduplication report is present and shows the right
  3 rows -> 2 distinct ledger ids
- the JSONL fileset is actually written to disk
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent
MIGRATION_EXAMPLE = REPO_ROOT / "docs" / "migration_example.py"


class MigrationExampleSmokeTests(unittest.TestCase):
    """The migration example script runs end-to-end and produces
    a valid contracts fileset."""

    def setUp(self):
        # Sandbox the output to a tempdir so we don't pollute
        # /tmp across test runs and we can inspect the files.
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.env = {**os.environ, "PYTHONPATH": "src",
                    "MIGRATION_OUT_DIR": str(self.tmpdir)}

    def tearDown(self):
        self._tmp.cleanup()

    def test_migration_example_runs_to_completion(self):
        r = subprocess.run(
            ["python3", str(MIGRATION_EXAMPLE)],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=REPO_ROOT,
        )
        self.assertEqual(r.returncode, 0,
                         f"stderr:\n{r.stderr}\nstdout:\n{r.stdout}")
        for heading in ("Migration: synthetic SQLite memory",
                        "Deduplication report",
                        "CLI sanity check",
                        "Migration complete"):
            self.assertIn(heading, r.stdout)

    def test_migration_example_collapses_duplicate_preferences(self):
        r = subprocess.run(
            ["python3", str(MIGRATION_EXAMPLE)],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=REPO_ROOT,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # The seed data has 2 identical preference rows + 1 decision
        # row, so the report should say "3 SQLite row(s) -> 2
        # distinct ledger id(s)".
        self.assertIn("3 SQLite row(s) -> 2 distinct ledger id(s)",
                      r.stdout)

    def test_migration_example_writes_expected_jsonl_fileset(self):
        r = subprocess.run(
            ["python3", str(MIGRATION_EXAMPLE)],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=REPO_ROOT,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        for filename in ("sources.jsonl", "spans.jsonl",
                         "candidates.jsonl", "reducer_decisions.jsonl",
                         "ledger.jsonl"):
            path = self.tmpdir / filename
            self.assertTrue(path.exists(),
                            f"expected output file missing: {path}")
            # Every JSONL file must have at least one valid JSON
            # record per line.
            with path.open() as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            self.assertGreater(len(lines), 0,
                               f"{filename} is empty")
            for ln in lines:
                obj = json.loads(ln)
                self.assertIsInstance(obj, dict)
                self.assertIn("id", obj)
                self.assertIn("schema_version", obj)

    def test_migration_example_ledger_jsonl_validates(self):
        """The JSONL ledger the example writes is loadable as a
        JSONL bundle and passes a basic schema sanity check."""
        r = subprocess.run(
            ["python3", str(MIGRATION_EXAMPLE)],
            capture_output=True,
            text=True,
            env=self.env,
            cwd=REPO_ROOT,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        ledger_path = self.tmpdir / "ledger.jsonl"
        with ledger_path.open() as fh:
            ledgers = [json.loads(ln) for ln in fh
                       if ln.strip()]
        # Each ledger entry must have the shape the library expects.
        for entry in ledgers:
            self.assertEqual(entry["schema_version"], "1.0.0")
            self.assertIn(entry["ledger_type"],
                          ("preference", "decision", "fact"))
            self.assertEqual(entry["status"], "active")
            self.assertTrue(entry["reducer_decision_id"],
                            "ledger entry missing reducer_decision_id")
            self.assertTrue(entry["evidence_span_ids"],
                            "ledger entry missing evidence_span_ids")
            # The id is content-derived, so it must look like a
            # hex string with a ledger-type prefix.
            self.assertRegex(
                entry["id"],
                r"^(pref|fact|dec)_[0-9a-f]+$",
            )

    def test_migration_example_fingerprint_is_stable(self):
        """Re-running the example produces the same bundle
        fingerprint. The content-derived id story means the
        fingerprint is deterministic for a given input."""
        # First run
        subprocess.run(
            ["python3", str(MIGRATION_EXAMPLE)],
            capture_output=True, text=True,
            env=self.env, cwd=REPO_ROOT,
            check=True,
        )
        first_ledger = (self.tmpdir / "ledger.jsonl").read_text()

        # Second run
        subprocess.run(
            ["python3", str(MIGRATION_EXAMPLE)],
            capture_output=True, text=True,
            env=self.env, cwd=REPO_ROOT,
            check=True,
        )
        second_ledger = (self.tmpdir / "ledger.jsonl").read_text()

        self.assertEqual(first_ledger, second_ledger,
                         "ledger.jsonl differs across runs; "
                         "id derivation should be deterministic")


if __name__ == "__main__":
    unittest.main()
