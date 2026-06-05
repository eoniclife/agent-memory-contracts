"""Tests for the ``merge`` subcommand.

Mirrors the structure of ``tests/test_cli.py`` and ``test_cli_json.py``:
subprocess-based, no pip install, ``PYTHONPATH=src``.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


def _env() -> dict:
    return {**os.environ, "PYTHONPATH": "src"}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", "-m", "agent_memory_contracts", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=Path(__file__).parent.parent,
    )


def _rec(i: int) -> dict:
    return {
        "id": f"rec_{i:08x}",
        "schema_version": "1.0.0",
        "value": i,
        "name": f"record {i}",
    }


class CLIMergeHelpTests(unittest.TestCase):
    """``merge --help`` documents the subcommand and its options."""

    def test_merge_help_exits_zero_and_lists_options(self):
        r = _run(["merge", "--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        for token in ("--prefer", "--id-field", "paths"):
            self.assertIn(token, r.stdout)


class CLIMergeSuccessTests(unittest.TestCase):
    """``merge <a.json> <b.json> ...`` succeeds on disjoint / overlapping bundles."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_json(self, name: str, data) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_merge_disjoint_bundles_exits_zero_and_summarizes(self):
        a = self._write_json("a.json", [_rec(0), _rec(1), _rec(2)])
        b = self._write_json("b.json", [_rec(3), _rec(4), _rec(5)])
        r = _run(["merge", str(a), str(b)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("6 records", r.stdout)
        self.assertIn("0 conflict", r.stdout)

    def test_merge_overlapping_bundles_prefer_last_wins(self):
        a = self._write_json("a.json", [_rec(0), _rec(1)])
        # Same id as a's rec_0 but with a tampered value.
        b = self._write_json("b.json", [{"id": "rec_00000000", "value": 999}])
        r = _run(["merge", str(a), str(b)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("2 records", r.stdout)
        self.assertIn("1 conflict", r.stdout)

    def test_merge_prefer_first_resolves_conflict_silently(self):
        a = self._write_json("a.json", [{"id": "x", "value": 1}])
        b = self._write_json("b.json", [{"id": "x", "value": 2}])
        r = _run(["merge", str(a), str(b), "--prefer", "first"])
        self.assertEqual(r.returncode, 0, r.stderr)
        # prefer='first' still surfaces the conflict for audit; the
        # resolved record is from the first bundle, but the
        # ``conflicts`` list is non-empty so the caller can see
        # something interesting happened.
        self.assertIn("1 conflict", r.stdout)

    def test_merge_prefer_raise_exits_1_on_conflict(self):
        a = self._write_json("a.json", [{"id": "x", "value": 1}])
        b = self._write_json("b.json", [{"id": "x", "value": 2}])
        r = _run(["merge", str(a), str(b), "--prefer", "raise"])
        self.assertEqual(r.returncode, 1, r.stdout)
        # The merge primitive's ValueError is propagated to the CLI's
        # stderr. The wording comes from merge_bundles itself.
        self.assertIn("different content", r.stderr.lower())

    def test_merge_three_bundles_counted_correctly(self):
        a = self._write_json("a.json", [_rec(0)])
        b = self._write_json("b.json", [_rec(1)])
        c = self._write_json("c.json", [_rec(2)])
        r = _run(["merge", str(a), str(b), str(c)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("3 records", r.stdout)
        self.assertIn("inputs=3", r.stdout)

    def test_merge_jsonl_input_is_supported(self):
        a = self._write_json("a.json", [_rec(0), _rec(1)])
        b_path = self.tmpdir / "b.jsonl"
        b_path.write_text(
            json.dumps(_rec(2)) + "\n" + json.dumps(_rec(3)) + "\n",
            encoding="utf-8",
        )
        r = _run(["merge", str(a), str(b_path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("4 records", r.stdout)

    def test_merge_missing_file_exits_1(self):
        a = self.tmpdir / "nonexistent.json"
        b = self._write_json("b.json", [_rec(0)])
        r = _run(["merge", str(a), str(b)])
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)


class CLIMergeJSONModeTests(unittest.TestCase):
    """``--json merge ...`` emits a structured envelope."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_json(self, name: str, data) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_merge_json_success_emits_ok_true_with_records(self):
        a = self._write_json("a.json", [_rec(0), _rec(1)])
        b = self._write_json("b.json", [_rec(2), _rec(3)])
        r = _run(["--json", "merge", str(a), str(b)])
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["record_count"], 4)
        self.assertEqual(payload["conflict_count"], 0)
        self.assertEqual(payload["prefer"], "last")
        self.assertEqual(payload["input_count"], 2)
        self.assertEqual(len(payload["records"]), 4)

    def test_merge_json_overlap_surfaces_conflict(self):
        a = self._write_json("a.json", [{"id": "x", "v": 1}])
        b = self._write_json("b.json", [{"id": "x", "v": 2}])
        r = _run(["--json", "merge", str(a), str(b)])
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["conflict_count"], 1)
        # The conflict envelope is [id, [[idx, rec], ...]].
        conflict_id, variants = payload["conflicts"][0]
        self.assertEqual(conflict_id, "x")
        self.assertEqual(len(variants), 2)
        self.assertEqual(variants[0][0], 0)  # first bundle index
        self.assertEqual(variants[1][0], 1)  # second bundle index

    def test_merge_json_prefer_raise_failure_is_json(self):
        a = self._write_json("a.json", [{"id": "x", "v": 1}])
        b = self._write_json("b.json", [{"id": "x", "v": 2}])
        r = _run(["--json", "merge", str(a), str(b), "--prefer", "raise"])
        self.assertEqual(r.returncode, 1, r.stdout)
        # On failure the JSON goes to stderr; stdout is empty.
        self.assertEqual(r.stdout, "")
        payload = json.loads(r.stderr)
        self.assertEqual(payload["ok"], False)
        self.assertIn("different content", payload["error"].lower())

    def test_merge_json_missing_file_is_json(self):
        a = self.tmpdir / "nonexistent.json"
        b = self._write_json("b.json", [_rec(0)])
        r = _run(["--json", "merge", str(a), str(b)])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        payload = json.loads(r.stderr)
        self.assertEqual(payload["ok"], False)
        self.assertIn("not found", payload["error"])


if __name__ == "__main__":
    unittest.main()
