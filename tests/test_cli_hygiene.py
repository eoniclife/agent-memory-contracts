"""Tests for the ``hygiene`` CLI subcommand."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).parent.parent


def _env() -> dict:
    return {**os.environ, "PYTHONPATH": "src"}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", "-m", "agent_memory_contracts", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=REPO_ROOT,
    )


def _build_bundle(records: list[dict]) -> str:
    """Write a JSONL bundle to a temp file and return its path."""
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False)
    for r in records:
        tmp.write(json.dumps(r) + "\n")
    tmp.close()
    return tmp.name


def _valid_pref(id_str: str, **overrides) -> dict:
    rec = {
        "id": id_str,
        "schema_version": "1.0.0",
        "ledger_type": "preference",
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "subject": "memory architecture",
        "preference_text": f"test {id_str}",
        "domain": "architecture",
        "strength": "hard_constraint",
        "valid_from": "2026-06-01T00:00:00Z",
        "valid_until": None,
        "stale_after": None,
        "superseded_by": [],
        "evidence_span_ids": ["span_aaaa"],
        "reducer_decision_id": "redmem_aaaa",
        "privacy_class": "internal",
        "metadata": {},
    }
    rec.update(overrides)
    return rec


class HygieneCLIBasicTests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.bundle_path = self.tmpdir / "bundle.jsonl"
        with self.bundle_path.open("w") as fh:
            for r in [_valid_pref("pref_aaaa"),
                      _valid_pref("pref_bbbb")]:
                fh.write(json.dumps(r) + "\n")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_hygiene_basic_markdown(self):
        r = _run(["hygiene", str(self.bundle_path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        # The Markdown report is on stdout.
        self.assertIn("# Memory hygiene report", r.stdout)
        self.assertIn("2 total records", r.stdout)
        self.assertIn("Window:", r.stdout)
        self.assertIn("Bundle fingerprint:", r.stdout)

    def test_hygiene_json_mode(self):
        r = _run(["--json", "hygiene", str(self.bundle_path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        # The JSON envelope parses.
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["total_records"], 2)
        self.assertEqual(payload["schema_version"], "1.0.0")
        self.assertTrue(payload["id"].startswith("hygiene_"))
        self.assertIn("records_by_plane", payload)
        self.assertIn("records_by_type", payload)
        self.assertIn("records_by_privacy", payload)
        self.assertIn("active_count", payload)
        self.assertIn("stale_count", payload)

    def test_hygiene_missing_file_exits_1(self):
        r = _run(["hygiene", str(self.tmpdir / "does_not_exist.jsonl")])
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)

    def test_hygiene_missing_file_json_mode(self):
        r = _run(["--json", "hygiene",
                  str(self.tmpdir / "does_not_exist.jsonl")])
        self.assertEqual(r.returncode, 1)
        # On failure the JSON goes to stderr; stdout is empty.
        self.assertEqual(r.stdout, "")
        payload = json.loads(r.stderr)
        self.assertEqual(payload["ok"], False)
        self.assertIn("not found", payload["error"])

    def test_hygiene_with_window_flags(self):
        r = _run(["hygiene", str(self.bundle_path),
                  "--from", "2026-05-01T00:00:00Z",
                  "--to", "2026-07-01T00:00:00Z"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("# Memory hygiene report", r.stdout)
        # The window is reflected in the report.
        self.assertIn("2026-05-01T00:00:00Z", r.stdout)
        self.assertIn("2026-07-01T00:00:00Z", r.stdout)

    def test_hygiene_window_start_after_window_end_exits_1(self):
        r = _run(["hygiene", str(self.bundle_path),
                  "--from", "2026-07-01T00:00:00Z",
                  "--to", "2026-06-01T00:00:00Z"])
        self.assertEqual(r.returncode, 1)
        # The error message is on stderr.
        self.assertIn("after", r.stderr.lower())
        self.assertIn("window_start", r.stderr.lower())
        self.assertIn("window_end", r.stderr.lower())

    def test_hygiene_malformed_window_exits_1(self):
        r = _run(["hygiene", str(self.bundle_path),
                  "--from", "not-iso"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("ISO 8601", r.stderr)

    def test_hygiene_malformed_bundle_exits_1(self):
        # Write a malformed JSONL file.
        bad_path = self.tmpdir / "bad.jsonl"
        bad_path.write_text("not json at all\n")
        r = _run(["hygiene", str(bad_path)])
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed to parse", r.stderr)

    def test_hygiene_help_exits_0(self):
        r = _run(["hygiene", "--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("Compute a MemoryHygieneReport", r.stdout)
        self.assertIn("--from", r.stdout)
        self.assertIn("--to", r.stdout)
        self.assertIn("--json", r.stdout)

    def test_hygiene_subcommand_appears_in_top_level_help(self):
        r = _run(["--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("hygiene", r.stdout)


class HygieneCLIIntegrationTests(unittest.TestCase):
    """End-to-end: a bundle with mixed states produces the
    expected Markdown report."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_mixed_state_bundle_produces_expected_counts(self):
        bundle_path = self.tmpdir / "bundle.jsonl"
        with bundle_path.open("w") as fh:
            for r in [
                # 3 active
                _valid_pref("pref_active_1"),
                _valid_pref("pref_active_2"),
                _valid_pref("pref_active_3"),
                # 2 superseded
                _valid_pref("pref_super_1",
                            superseded_by=["pref_newer_1"]),
                _valid_pref("pref_super_2",
                            superseded_by=["pref_newer_2"]),
                # 1 expired
                _valid_pref("pref_expired",
                            valid_from="2026-01-01T00:00:00Z",
                            valid_until="2026-05-01T00:00:00Z"),
            ]:
                fh.write(json.dumps(r) + "\n")
        r = _run(["hygiene", str(bundle_path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        # The headline: 6 total records, 5 active, 0 stale,
        # 1 expired, 2 superseded.
        self.assertIn("6 total records", r.stdout)
        self.assertIn("5 active", r.stdout)
        self.assertIn("1 expired", r.stdout)
        self.assertIn("2 superseded", r.stdout)


if __name__ == "__main__":
    unittest.main()
