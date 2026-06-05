"""Tests for the ``--json`` output mode of the CLI.

All tests invoke the CLI via ``subprocess.run`` with ``PYTHONPATH=src``.
The test environment is deliberately "no pip install" to match how a
developer runs the tool from a fresh clone.

When ``--json`` is passed, every subcommand emits a single JSON object
to stdout on success, and a separate JSON object to stderr on failure
(so exit-code 1 callers can still parse the error). Exit codes are
preserved.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


# Probe for the optional jsonschema dependency once at import time.
try:
    import jsonschema  # noqa: F401

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def _env() -> dict:
    """Return a clean env with PYTHONPATH=src, mirroring test_cli.py."""
    return {**os.environ, "PYTHONPATH": "src"}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    """Run the CLI with the given args, return the CompletedProcess."""
    return subprocess.run(
        ["python3", "-m", "agent_memory_contracts", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=Path(__file__).parent.parent,
    )


def _valid_source_dict() -> dict:
    """Return a dict that satisfies the ``source_record`` JSON Schema."""
    return {
        "id": "src_" + ("a" * 59),
        "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": "CLI JSON test source",
        "origin_uri": "https://example.com/x",
        "raw_ref": {"kind": "external_uri", "value": "https://example.com/x"},
        "content_hash_sha256": "a" * 64,
        "captured_at": "2026-05-30T12:00:00Z",
        "observed_at": "2026-05-30T12:00:00Z",
        "author_or_sender": "user",
        "participants": ["user"],
        "privacy_class": "internal",
        "custody_status": "external_pointer",
        "parser_version": "v1",
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Help / discovery
# ---------------------------------------------------------------------------


class CLINoArgsJSONTests(unittest.TestCase):
    """``--json`` is a parser-level flag and is accepted before any subcommand."""

    def test_json_flag_is_accepted_at_top_level(self):
        # ``--json`` alone (no subcommand) must not raise an argparse
        # error. It only becomes meaningful with a subcommand; this
        # test just confirms the parser-level placement.
        r = _run(["--json"])
        # No subcommand -> argparse's required subparser kicks in.
        # We just care that the parser saw the flag (no "unrecognized
        # arguments" error).
        self.assertIn("subcommand", r.stderr)


class CLIHelpJSONTests(unittest.TestCase):
    """``--help`` describes the ``--json`` flag."""

    def test_help_mentions_json_flag(self):
        r = _run(["--help"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--json", r.stdout)


# ---------------------------------------------------------------------------
# validate --json
# ---------------------------------------------------------------------------


class CLIValidateJSONModeTests(unittest.TestCase):
    """``validate --json <path> --schema <name>`` on a JSON file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_json(self, name: str, data) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_validate_json_success_emits_ok_true_with_empty_errors(self):
        """Case 1: valid record -> ok=true, errors=[]."""
        path = self._write_json("valid.json", _valid_source_dict())
        r = _run(
            ["--json", "validate", str(path), "--schema", "source_record"]
        )
        # Case 8: exit code preserved as 0.
        self.assertEqual(r.returncode, 0, r.stderr)
        # Case 9: stdout is parseable.
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["schema"], "source_record")
        self.assertEqual(payload["path"], str(path))
        self.assertEqual(payload["mode"], "json")
        self.assertEqual(payload["errors"], [])

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_validate_json_failure_emits_ok_false_with_errors(self):
        """Case 2: invalid record -> ok=false, errors non-empty, exit 1."""
        bad = dict(_valid_source_dict())
        del bad["title"]
        path = self._write_json("bad.json", bad)
        r = _run(
            ["--json", "validate", str(path), "--schema", "source_record"]
        )
        # Case 8: exit code preserved as 1.
        self.assertEqual(r.returncode, 1, r.stderr)
        # On failure the JSON goes to stderr so stdout is empty.
        self.assertEqual(r.stdout, "")
        # Case 9: stderr is parseable.
        payload = json.loads(r.stderr)
        self.assertEqual(payload["ok"], False)
        self.assertEqual(payload["schema"], "source_record")
        self.assertEqual(payload["path"], str(path))
        self.assertEqual(payload["mode"], "json")
        self.assertIsInstance(payload["errors"], list)
        self.assertGreater(len(payload["errors"]), 0)

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_validate_json_bundle_success_mode_is_bundle(self):
        """Case 3: --bundle --json -> mode='bundle'."""
        rec = _valid_source_dict()
        path = self._write_json("bundle.json", [rec, rec])
        r = _run(
            ["--json", "validate", str(path),
             "--schema", "source_record", "--bundle"]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["mode"], "bundle")
        self.assertEqual(payload["errors"], [])

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_validate_json_jsonl_success_mode_is_jsonl(self):
        """Case 4: --jsonl --json -> mode='jsonl'."""
        rec = _valid_source_dict()
        path = self.tmpdir / "valid.jsonl"
        path.write_text(
            json.dumps(rec) + "\n" + json.dumps(rec) + "\n",
            encoding="utf-8",
        )
        r = _run(
            ["--json", "validate", str(path),
             "--schema", "source_record", "--jsonl"]
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["mode"], "jsonl")
        self.assertEqual(payload["errors"], [])

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_validate_json_missing_file_emits_ok_false(self):
        """Case 11 (validate): missing file -> ok=false JSON, exit 1."""
        path = self.tmpdir / "does_not_exist.json"
        r = _run(
            ["--json", "validate", str(path), "--schema", "source_record"]
        )
        self.assertEqual(r.returncode, 1)
        # Failure path: JSON to stderr, stdout empty.
        self.assertEqual(r.stdout, "")
        payload = json.loads(r.stderr)
        self.assertEqual(payload["ok"], False)
        self.assertIn("not found", payload["errors"][0])


# ---------------------------------------------------------------------------
# fingerprint --json
# ---------------------------------------------------------------------------


class CLIFingerprintJSONModeTests(unittest.TestCase):
    """``fingerprint --json <path>`` emits ok, fingerprint, record_count."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_json(self, name: str, data) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_fingerprint_json_emits_64_char_lowercase_hex(self):
        """Case 5: ok=true, 64-char lowercase hex digest, record_count correct."""
        records = [
            {"id": "rec_00000000", "schema_version": "1.0.0", "v": 1},
            {"id": "rec_00000001", "schema_version": "1.0.0", "v": 2},
        ]
        path = self._write_json("bundle.json", records)
        r = _run(["--json", "fingerprint", str(path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["path"], str(path))
        self.assertEqual(payload["record_count"], 2)
        fp = payload["fingerprint"]
        self.assertIsInstance(fp, str)
        self.assertEqual(len(fp), 64, f"expected 64-char digest, got: {fp!r}")
        self.assertTrue(
            all(c in "0123456789abcdef" for c in fp),
            f"digest is not lowercase hex: {fp!r}",
        )

    def test_fingerprint_jsonl_emits_record_count(self):
        path = self.tmpdir / "bundle.jsonl"
        path.write_text(
            '{"id": "rec_00000000", "schema_version": "1.0.0"}\n'
            '{"id": "rec_00000001", "schema_version": "1.0.0"}\n'
            '{"id": "rec_00000002", "schema_version": "1.0.0"}\n',
            encoding="utf-8",
        )
        r = _run(["--json", "fingerprint", str(path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["record_count"], 3)
        self.assertEqual(len(payload["fingerprint"]), 64)

    def test_fingerprint_json_missing_file_emits_ok_false(self):
        """Case 11 (fingerprint): missing file -> ok=false JSON, exit 1."""
        path = self.tmpdir / "does_not_exist.json"
        r = _run(["--json", "fingerprint", str(path)])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        payload = json.loads(r.stderr)
        self.assertEqual(payload["ok"], False)
        self.assertIn("not found", payload["error"])


# ---------------------------------------------------------------------------
# diff --json
# ---------------------------------------------------------------------------


class CLIDiffJSONModeTests(unittest.TestCase):
    """``diff --json <a> <b>`` emits added, removed, changed, unchanged_count."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_json(self, name: str, data) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_diff_json_emits_full_record_dicts(self):
        """Case 6: added/removed/changed are full record dicts; unchanged_count correct."""
        a = self._write_json("a.json", [
            {"id": "x", "v": 1},  # unchanged (matched in b)
            {"id": "y", "v": 2},  # changed (b has v=3)
        ])
        b = self._write_json("b.json", [
            {"id": "x", "v": 1},  # unchanged
            {"id": "y", "v": 3},  # changed
            {"id": "z", "v": 9},  # added
        ])
        r = _run(["--json", "diff", str(a), str(b)])
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        # added/removed are full record dicts.
        self.assertEqual(len(payload["added"]), 1)
        self.assertEqual(payload["added"][0], {"id": "z", "v": 9})
        self.assertEqual(payload["removed"], [])
        # changed is a list of [old, new] pairs of full record dicts.
        self.assertEqual(len(payload["changed"]), 1)
        old, new = payload["changed"][0]
        self.assertEqual(old, {"id": "y", "v": 2})
        self.assertEqual(new, {"id": "y", "v": 3})
        # unchanged_count is 1 (the "x" record).
        self.assertEqual(payload["unchanged_count"], 1)

    def test_diff_json_identical_files_have_empty_changes(self):
        """Case 7: identical files -> added/removed/changed empty, unchanged=record count."""
        a = self._write_json("same.json", [
            {"id": "a", "v": 1},
            {"id": "b", "v": 2},
            {"id": "c", "v": 3},
        ])
        r = _run(["--json", "diff", str(a), str(a)])
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["added"], [])
        self.assertEqual(payload["removed"], [])
        self.assertEqual(payload["changed"], [])
        self.assertEqual(payload["unchanged_count"], 3)

    def test_diff_json_missing_file_a_emits_ok_false(self):
        """Case 11 (diff, a missing): ok=false JSON, exit 1."""
        a = self.tmpdir / "nonexistent.json"
        b = self._write_json("b.json", [{"id": "x"}])
        r = _run(["--json", "diff", str(a), str(b)])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        payload = json.loads(r.stderr)
        self.assertEqual(payload["ok"], False)
        self.assertIn("not found", payload["error"])
        self.assertEqual(payload["path_a"], str(a))
        self.assertEqual(payload["path_b"], str(b))

    def test_diff_json_missing_file_b_emits_ok_false(self):
        """Case 11 (diff, b missing): ok=false JSON, exit 1, path_b reported."""
        a = self._write_json("a.json", [{"id": "x"}])
        b = self.tmpdir / "nonexistent.json"
        r = _run(["--json", "diff", str(a), str(b)])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        payload = json.loads(r.stderr)
        self.assertEqual(payload["ok"], False)
        self.assertIn("not found", payload["error"])


# ---------------------------------------------------------------------------
# Cross-cutting invariants
# ---------------------------------------------------------------------------


class CLIJSONOutputParseableTests(unittest.TestCase):
    """Case 9: every ``--json`` test stdout/stderr above was already
    ``json.loads()``'d. This test asserts the same invariant explicitly
    on a fresh end-to-end run of every subcommand."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, data) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_validate_json_stdout_is_json(self):
        path = self._write("v.json", _valid_source_dict())
        r = _run(["--json", "validate", str(path), "--schema", "source_record"])
        self.assertEqual(r.returncode, 0)
        json.loads(r.stdout)  # must not raise

    def test_fingerprint_json_stdout_is_json(self):
        path = self._write("b.json", [{"id": "a", "v": 1}])
        r = _run(["--json", "fingerprint", str(path)])
        self.assertEqual(r.returncode, 0)
        json.loads(r.stdout)

    def test_diff_json_stdout_is_json(self):
        a = self._write("a.json", [{"id": "x", "v": 1}])
        b = self._write("b.json", [{"id": "x", "v": 2}])
        r = _run(["--json", "diff", str(a), str(b)])
        self.assertEqual(r.returncode, 0)
        json.loads(r.stdout)

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_validate_json_stderr_on_failure_is_json(self):
        bad = dict(_valid_source_dict())
        del bad["title"]
        path = self._write("b.json", bad)
        r = _run(["--json", "validate", str(path), "--schema", "source_record"])
        self.assertEqual(r.returncode, 1)
        json.loads(r.stderr)


# ---------------------------------------------------------------------------
# Case 10: default (no --json) human-readable output is byte-identical
# to the pre-change baseline. The existing tests/test_cli.py tests
# already enforce this via the byte-level assertions in
# CLIValidateJSONTests, CLIValidateJSONLTests, CLIFingerprintTests,
# CLIDiffTests. Re-importing those test classes here and re-running
# them would only duplicate coverage; we instead assert the specific
# property with a focused check.
# ---------------------------------------------------------------------------


class CLIDefaultOutputPreservedTests(unittest.TestCase):
    """Case 10: when ``--json`` is omitted, the human-readable output
    is unchanged. The pre-existing test_cli.py suite covers the full
    output; this test pins one signature line per subcommand so a
    future refactor of the default output path gets caught here even
    if test_cli.py is restructured."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, name: str, data) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_validate_default_emits_no_stdout_on_success(self):
        path = self._write("v.json", _valid_source_dict())
        r = _run(["validate", str(path), "--schema", "source_record"])
        self.assertEqual(r.returncode, 0, r.stderr)
        # Default success path is silent on stdout (errors go to stderr).
        self.assertEqual(r.stdout, "")

    def test_fingerprint_default_emits_64_char_hex(self):
        path = self._write("b.json", [{"id": "a", "v": 1}])
        r = _run(["fingerprint", str(path)])
        self.assertEqual(r.returncode, 0, r.stderr)
        digest = r.stdout.strip()
        self.assertEqual(len(digest), 64)

    def test_diff_default_emits_summary_line(self):
        a = self._write("a.json", [{"id": "x", "v": 1}])
        b = self._write("b.json", [{"id": "x", "v": 2}])
        r = _run(["diff", str(a), str(b)])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(" changed,", r.stdout)


if __name__ == "__main__":
    unittest.main()
