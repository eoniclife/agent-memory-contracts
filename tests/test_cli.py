"""Tests for the ``python -m agent_memory_contracts`` CLI entry point.

All tests invoke the CLI via ``subprocess.run`` with ``PYTHONPATH=src``.
The test environment is deliberately "no pip install" to match how a
developer runs the tool from a fresh clone.
"""

from __future__ import annotations

import json
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


def _valid_source_dict() -> dict:
    """Return a dict that satisfies the ``source_record`` JSON Schema."""
    return {
        "id": "src_"
        + ("a" * 59),
        "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": "CLI test source",
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


class CLIHelpTests(unittest.TestCase):
    """``--help`` prints usage and exits 0."""

    def test_help_exits_zero_and_prints_usage(self):
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "--help"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage", result.stdout)


class CLIVersionTests(unittest.TestCase):
    """``--version`` prints name + version and exits 0."""

    def test_version_exits_zero_and_prints_package_name(self):
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "--version"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("agent-memory-contracts", result.stdout)


class CLIUnknownSubcommandTests(unittest.TestCase):
    """An unknown subcommand exits non-zero."""

    def test_unknown_subcommand_exits_nonzero(self):
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "frobnicate"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("frobnicate", result.stderr)
        self.assertIn("invalid choice", result.stderr)


class CLINoArgsTests(unittest.TestCase):
    """No arguments at all prints usage to stderr and exits 2."""

    def test_no_args_exits_2_and_prints_usage_to_stderr(self):
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("usage", result.stderr)


class CLIValidateJSONTests(unittest.TestCase):
    """``validate <path> --schema <name>`` on a JSON file."""

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
    def test_validate_valid_json_exits_zero(self):
        path = self._write_json("valid.json", _valid_source_dict())
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "validate",
             str(path), "--schema", "source_record"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_validate_invalid_json_exits_1_and_prints_error(self):
        # A JSON object that is missing required fields.
        bad = dict(_valid_source_dict())
        del bad["title"]
        path = self._write_json("bad.json", bad)
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "validate",
             str(path), "--schema", "source_record"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotEqual(result.stderr, "")

    def test_validate_nonexistent_file_exits_1(self):
        path = self.tmpdir / "does_not_exist.json"
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "validate",
             str(path), "--schema", "source_record"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)


class CLIValidateJSONLTests(unittest.TestCase):
    """``validate <path> --schema <name> --jsonl`` on a JSONL file."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    @unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
    def test_validate_jsonl_valid_exits_zero(self):
        rec = _valid_source_dict()
        path = self.tmpdir / "valid.jsonl"
        path.write_text(json.dumps(rec) + "\n" + json.dumps(rec) + "\n",
                        encoding="utf-8")
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "validate",
             str(path), "--schema", "source_record", "--jsonl"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_validate_jsonl_invalid_exits_1(self):
        bad = dict(_valid_source_dict())
        del bad["title"]
        path = self.tmpdir / "bad.jsonl"
        path.write_text(json.dumps(bad) + "\n", encoding="utf-8")
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "validate",
             str(path), "--schema", "source_record", "--jsonl"],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 1, result.stderr)


class CLIFingerprintTests(unittest.TestCase):
    """``fingerprint <path>`` prints a 64-char hex digest."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_json(self, name: str, data) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_fingerprint_json_file_exits_zero_and_prints_64_hex_chars(self):
        path = self._write_json("bundle.json", [
            {"id": "rec_00000000", "schema_version": "1.0.0", "v": 1},
            {"id": "rec_00000001", "schema_version": "1.0.0", "v": 2},
        ])
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "fingerprint",
             str(path)],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        digest = result.stdout.strip()
        self.assertEqual(len(digest), 64, f"expected 64-char digest, got: {digest!r}")
        self.assertTrue(
            all(c in "0123456789abcdef" for c in digest),
            f"digest is not lowercase hex: {digest!r}",
        )

    def test_fingerprint_jsonl_file_exits_zero(self):
        path = self.tmpdir / "bundle.jsonl"
        path.write_text(
            '{"id": "rec_00000000", "schema_version": "1.0.0"}\n'
            '{"id": "rec_00000001", "schema_version": "1.0.0"}\n',
            encoding="utf-8",
        )
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "fingerprint",
             str(path)],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(result.stdout.strip()), 64)

    def test_fingerprint_nonexistent_file_exits_1(self):
        path = self.tmpdir / "does_not_exist.json"
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "fingerprint",
             str(path)],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)


class CLIDiffTests(unittest.TestCase):
    """``diff <path-a> <path-b>`` prints a human-readable summary."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_json(self, name: str, data) -> Path:
        p = self.tmpdir / name
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    def test_diff_exits_zero_and_prints_summary_line(self):
        a = self._write_json("a.json", [{"id": "x", "v": 1}])
        b = self._write_json("b.json", [{"id": "x", "v": 2}])
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "diff",
             str(a), str(b)],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(" changed,", result.stdout)

    def test_diff_identical_files_exits_zero_and_prints_zero_change_counts(self):
        a = self._write_json("same.json", [{"id": "x", "v": 1}])
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "diff",
             str(a), str(a)],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0 added", result.stdout)
        self.assertIn("0 removed", result.stdout)
        self.assertIn("0 changed", result.stdout)

    def test_diff_added_removed_changed_prints_change_lines(self):
        a = self._write_json("a.json", [{"id": "x", "v": 1}])
        b = self._write_json("b.json", [
            {"id": "x", "v": 1},
            {"id": "y", "v": 2},   # added
        ])
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "diff",
             str(a), str(b)],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("+ y", result.stdout)

    def test_diff_nonexistent_file_exits_1(self):
        a = self.tmpdir / "nonexistent.json"
        b = self._write_json("b.json", [{"id": "x"}])
        result = subprocess.run(
            ["python3", "-m", "agent_memory_contracts", "diff",
             str(a), str(b)],
            capture_output=True,
            text=True,
            env={**subprocess.os.environ, "PYTHONPATH": "src"},
            cwd=Path(__file__).parent.parent,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("not found", result.stderr)


if __name__ == "__main__":
    unittest.main()