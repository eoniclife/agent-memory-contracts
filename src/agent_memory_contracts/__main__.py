"""CLI entry point for ``python -m agent_memory_contracts``.

A small, stdlib-only command-line interface that exposes the three
operational primitives of the library:

- ``validate``   Validate a JSON or JSONL file against a bundled schema
                 (uses the optional ``jsonschema`` validator).
- ``fingerprint`` Print the deterministic SHA-256 fingerprint of a bundle.
- ``diff``       Compare two bundles and print a set-semantic diff.

The CLI is intentionally thin: it parses a file, calls into the
public Python API, and shapes the output. All real work stays in the
library modules so that the same code paths are exercised by
production callers and CLI callers.

Usage::

    python -m agent_memory_contracts --help
    python -m agent_memory_contracts --version
    python -m agent_memory_contracts validate path/to/record.json --schema source_record
    python -m agent_memory_contracts validate path/to/records.jsonl --jsonl --schema source_record
    python -m agent_memory_contracts fingerprint path/to/bundle.json
    python -m agent_memory_contracts diff before.json after.json

.. versionadded:: 0.4.0
"""

from __future__ import annotations

import argparse
import json
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, Callable

# `bundle_fingerprint` is the only top-level re-export; the rest of
# the surface we use lives in dedicated submodules. Importing from
# the submodules keeps this CLI working without requiring a change
# to the package's __init__.
from agent_memory_contracts import bundle_fingerprint
from agent_memory_contracts.bundle_diff import BundleDiff, bundle_diff
from agent_memory_contracts.jsonschema_validator import (
    SchemaNotFoundError,
    validate_instance,
    validate_jsonl,
)


PACKAGE_NAME = "agent-memory-contracts"


# ---------------------------------------------------------------------------
# File loading helpers
# ---------------------------------------------------------------------------


def _is_jsonl_path(path: Path, text: str) -> bool:
    """Return True if ``path`` should be parsed as JSONL.

    Detection rules, in order:

    1. A ``.jsonl`` extension is always JSONL.
    2. A ``.json`` extension is always JSON.
    3. Other / no extensions: sniff the first non-whitespace character.
       ``{`` or ``[`` -> JSON; anything else -> JSONL.
    """
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        return True
    if suffix == ".json":
        return False
    for ch in text:
        if ch.isspace():
            continue
        return ch not in ("{", "[")
    # Empty / whitespace-only file: treat as JSONL (no records).
    return True


def read_bundle(path: Path) -> list[dict]:
    """Load a bundle of record dicts from a JSON or JSONL file.

    - ``.jsonl`` (or JSONL by sniff): one record per line, blank lines
      skipped, each non-blank line must parse as a JSON object.
    - ``.json`` (or JSON by sniff): the file is parsed as a single
      JSON value. A JSON object is a one-record bundle; a JSON array
      is a multi-record bundle (non-dict entries are skipped with no
      error -- they cannot meaningfully be fingerprinted or diffed).

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        ValueError: if the file cannot be parsed or the content
            shape is not a bundle of dicts.
    """
    text = path.read_text(encoding="utf-8")
    if _is_jsonl_path(path, text):
        records: list[dict] = []
        for ln, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"line {ln}: invalid JSON: {exc.msg} "
                    f"(col {exc.colno})"
                ) from exc
            if not isinstance(obj, dict):
                raise ValueError(
                    f"line {ln}: JSONL record must be a JSON object, "
                    f"got {type(obj).__name__}"
                )
            records.append(obj)
        return records

    obj = json.loads(text)
    if isinstance(obj, dict):
        return [obj]
    if isinstance(obj, list):
        return [r for r in obj if isinstance(r, dict)]
    raise ValueError(
        f"expected a JSON object or array of records, "
        f"got {type(obj).__name__}"
    )


def load_single_json(path: Path) -> Any:
    """Load a file as a single JSON value (object or array).

    Used by ``validate`` in the non-JSONL path.
    """
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def _print_errors_to_stderr(errors: list[str], prefix: str = "") -> None:
    for msg in errors:
        if prefix:
            print(f"{prefix}{msg}", file=sys.stderr)
        else:
            print(msg, file=sys.stderr)


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"validate: file not found: {path}", file=sys.stderr)
        return 1

    if args.jsonl:
        # JSONL mode: stream through validate_jsonl.
        try:
            errors = validate_jsonl(path, args.schema, raise_on_error=False)
        except jsonschema_validator.SchemaNotFoundError as exc:
            print(f"validate: {exc}", file=sys.stderr)
            return 1
        except ImportError as exc:
            print(f"validate: {exc}", file=sys.stderr)
            return 1
        if errors:
            for _ln, msgs in errors:
                _print_errors_to_stderr(msgs)
            return 1
        return 0

    # Single JSON object or array of records.
    try:
        payload = load_single_json(path)
    except FileNotFoundError:
        print(f"validate: file not found: {path}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(
            f"validate: invalid JSON in {path}: {exc.msg} "
            f"(line {exc.lineno}, col {exc.colno})",
            file=sys.stderr,
        )
        return 1

    if args.bundle:
        if not isinstance(payload, list):
            print(
                f"validate: --bundle requires a JSON array at the top "
                f"level, got {type(payload).__name__}",
                file=sys.stderr,
            )
            return 1
        try:
            all_errors = jsonschema_validator.validate_bundle(
                ((args.schema, r) for r in payload),
                raise_on_error=False,
            )
        except jsonschema_validator.SchemaNotFoundError as exc:
            print(f"validate: {exc}", file=sys.stderr)
            return 1
        except ImportError as exc:
            print(f"validate: {exc}", file=sys.stderr)
            return 1
        if all_errors:
            for schema_name, msgs in all_errors.items():
                _print_errors_to_stderr(msgs, prefix=f"{schema_name}: ")
            return 1
        return 0

    # Default: single JSON object, validate against args.schema.
    if not isinstance(payload, dict):
        print(
            f"validate: expected a JSON object at the top level "
            f"(use --bundle for arrays or --jsonl for JSONL files), "
            f"got {type(payload).__name__}",
            file=sys.stderr,
        )
        return 1

    try:
        errors = validate_instance(
            payload, args.schema, raise_on_error=False
        )
    except jsonschema_validator.SchemaNotFoundError as exc:
        print(f"validate: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        print(f"validate: {exc}", file=sys.stderr)
        return 1
    if errors:
        _print_errors_to_stderr(errors)
        return 1
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"fingerprint: file not found: {path}", file=sys.stderr)
        return 1
    try:
        records = read_bundle(path)
    except FileNotFoundError:
        print(f"fingerprint: file not found: {path}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"fingerprint: failed to parse {path}: {exc}", file=sys.stderr)
        return 1
    digest = bundle_fingerprint(records)
    print(digest)
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    a_path = Path(args.path_a)
    b_path = Path(args.path_b)
    if not a_path.exists():
        print(f"diff: file not found: {a_path}", file=sys.stderr)
        return 1
    if not b_path.exists():
        print(f"diff: file not found: {b_path}", file=sys.stderr)
        return 1
    try:
        a = read_bundle(a_path)
        b = read_bundle(b_path)
    except FileNotFoundError as exc:
        print(f"diff: file not found: {exc.filename or exc}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, ValueError) as exc:
        print(f"diff: failed to parse input: {exc}", file=sys.stderr)
        return 1
    result = bundle_diff(a, b)
    print(
        f"{len(result.added)} added, {len(result.removed)} removed, "
        f"{len(result.changed)} changed, {result.unchanged_count} unchanged"
    )
    for r in result.added:
        print(f"+ {r.get('id', '<no-id>')}")
    for r in result.removed:
        print(f"- {r.get('id', '<no-id>')}")
    for old, new in result.changed:
        rid = new.get("id") or old.get("id", "<no-id>")
        print(f"~ {rid}")
    return 0


# ---------------------------------------------------------------------------
# Version resolution
# ---------------------------------------------------------------------------


def _get_version() -> str:
    """Return the installed distribution version of this package.

    Uses :func:`importlib.metadata.version` per the project standard.
    Falls back to the package's own ``__version__`` attribute when
    the package is importable from a source tree (e.g. via
    ``PYTHONPATH=src``) but not pip-installed -- which is the
    common local-dev setup. The fallback keeps ``--version`` working
    without requiring a developer to ``pip install -e .`` first.
    """
    try:
        return metadata.version(PACKAGE_NAME)
    except metadata.PackageNotFoundError:
        try:
            from agent_memory_contracts import __version__ as v
            return v
        except (ImportError, AttributeError):
            return "unknown"


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agent_memory_contracts",
        description=(
            "agent-memory-contracts CLI: validate, fingerprint, and "
            "diff agent memory bundles."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PACKAGE_NAME} {_get_version()}",
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="<subcommand>")

    p_validate = sub.add_parser(
        "validate",
        help="Validate a JSON or JSONL file against a bundled schema.",
        description=(
            "Validate a single JSON object (default), a JSON array of "
            "records (--bundle), or a JSONL stream (--jsonl) against "
            "one of the bundled JSON Schemas."
        ),
    )
    p_validate.add_argument("path", help="Path to the file to validate.")
    p_validate.add_argument(
        "--schema",
        required=True,
        help=(
            "Schema short name, e.g. 'source_record', 'taste_card'. "
            "See jsonschema_validator.VALID_SCHEMA_NAMES for the full list."
        ),
    )
    p_validate.add_argument(
        "--jsonl",
        action="store_true",
        help="Treat the input as JSONL (one record per line).",
    )
    p_validate.add_argument(
        "--bundle",
        action="store_true",
        help=(
            "Treat the input as a JSON array of records and validate "
            "each one against --schema. Mutually exclusive with --jsonl."
        ),
    )
    p_validate.set_defaults(_func=cmd_validate)

    p_fingerprint = sub.add_parser(
        "fingerprint",
        help="Print the SHA-256 fingerprint of a bundle.",
        description=(
            "Compute and print the deterministic SHA-256 fingerprint "
            "of a bundle. Accepts both .json (object or array) and "
            ".jsonl (one record per line)."
        ),
    )
    p_fingerprint.add_argument(
        "path", help="Path to the JSON or JSONL bundle.",
    )
    p_fingerprint.set_defaults(_func=cmd_fingerprint)

    p_diff = sub.add_parser(
        "diff",
        help="Compare two bundles and show the set-semantic diff.",
        description=(
            "Diff two bundles and print a human-readable summary. "
            "Both inputs are treated as sets of records keyed by 'id'."
        ),
    )
    p_diff.add_argument("path_a", help="Path to the 'before' bundle.")
    p_diff.add_argument("path_b", help="Path to the 'after' bundle.")
    p_diff.set_defaults(_func=cmd_diff)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a Unix-style exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    func: Callable[[argparse.Namespace], int] | None = getattr(
        args, "_func", None,
    )
    if func is None:
        # No subcommand given. argparse with a required subparser
        # already prints the usage and exits 2 before we get here
        # on Python 3.7+, so this is a defensive fallback.
        parser.print_help(sys.stderr)
        return 2
    return int(func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
