"""CLI entry point for ``python -m agent_memory_contracts``.

A small, stdlib-only command-line interface that exposes the four
operational primitives of the library:

- ``validate``   Validate a JSON or JSONL file against a bundled schema
                 (uses the optional ``jsonschema`` validator).
- ``fingerprint`` Print the deterministic SHA-256 fingerprint of a bundle.
- ``diff``       Compare two bundles and print a set-semantic diff.
- ``merge``      Merge two or more bundles into one (set-semantic,
                 last-write-wins on duplicate ids; ``--prefer`` selects
                 the conflict-resolution policy).

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
    python -m agent_memory_contracts merge a.json b.json c.json --prefer last
    python -m agent_memory_contracts --json validate path/to/record.json --schema source_record
    python -m agent_memory_contracts --json fingerprint path/to/bundle.json
    python -m agent_memory_contracts --json diff before.json after.json
    python -m agent_memory_contracts --json merge a.json b.json --prefer first
    python -m agent_memory_contracts validate --json path/to/record.json --schema source_record
    python -m agent_memory_contracts fingerprint --json path/to/bundle.json
    python -m agent_memory_contracts diff --json before.json after.json

``--json`` is a parser-level flag that can appear before or after the
subcommand. When set, every subcommand emits a single JSON object to
stdout (and a separate JSON object to stderr on failure), so callers
can ``json.loads()`` the result regardless of exit code. The
human-readable text output is unchanged when ``--json`` is omitted.

.. versionadded:: 0.4.0
.. versionchanged:: 0.5.0
   Added the ``merge`` subcommand for many-to-one bundle merging.
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
    validate_bundle,
    validate_instance,
    validate_jsonl,
)
from agent_memory_contracts.merge import BundleMerge, merge_bundles


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


def _emit_json(payload: Any, *, to_stderr: bool = False) -> None:
    """Write ``payload`` as a single JSON object to stdout or stderr.

    Used by the ``--json`` mode of each subcommand. On success the
    payload goes to stdout so callers can pipe it into ``json.loads``
    regardless of whether the human-readable text is silenced. On
    failure the payload is emitted to stderr so exit-code 1 callers
    can still parse the error.

    Keys are emitted in insertion order (not alphabetically sorted) so
    the on-the-wire shape matches the spec, which writes ``ok`` first
    and ``errors`` last for the validate envelope. The output is
    terminated with a single newline to match the convention used by
    the human-readable path.
    """
    stream = sys.stderr if to_stderr else sys.stdout
    json.dump(payload, stream, ensure_ascii=False)
    stream.write("\n")


def cmd_validate(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        if args.json:
            _emit_json(
                {"ok": False, "schema": args.schema, "path": str(path),
                 "mode": "json", "errors": [f"file not found: {path}"]},
                to_stderr=True,
            )
        else:
            print(f"validate: file not found: {path}", file=sys.stderr)
        return 1

    if args.jsonl:
        # JSONL mode: stream through validate_jsonl.
        try:
            errors = validate_jsonl(path, args.schema, raise_on_error=False)
        except SchemaNotFoundError as exc:
            if args.json:
                _emit_json(
                    {"ok": False, "schema": args.schema, "path": str(path),
                     "mode": "jsonl", "errors": [str(exc)]},
                    to_stderr=True,
                )
            else:
                print(f"validate: {exc}", file=sys.stderr)
            return 1
        except ImportError as exc:
            if args.json:
                _emit_json(
                    {"ok": False, "schema": args.schema, "path": str(path),
                     "mode": "jsonl", "errors": [str(exc)]},
                    to_stderr=True,
                )
            else:
                print(f"validate: {exc}", file=sys.stderr)
            return 1
        if errors:
            # Flatten per-line errors into a single list of strings.
            flat: list[str] = []
            for _ln, msgs in errors:
                flat.extend(msgs)
            if args.json:
                _emit_json(
                    {"ok": False, "schema": args.schema, "path": str(path),
                     "mode": "jsonl", "errors": flat},
                    to_stderr=True,
                )
            else:
                for _ln, msgs in errors:
                    _print_errors_to_stderr(msgs)
            return 1
        if args.json:
            _emit_json(
                {"ok": True, "schema": args.schema, "path": str(path),
                 "mode": "jsonl", "errors": []}
            )
        return 0

    # Single JSON object or array of records.
    try:
        payload = load_single_json(path)
    except FileNotFoundError:
        if args.json:
            _emit_json(
                {"ok": False, "schema": args.schema, "path": str(path),
                 "mode": "json", "errors": [f"file not found: {path}"]},
                to_stderr=True,
            )
        else:
            print(f"validate: file not found: {path}", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        msg = (
            f"invalid JSON: {exc.msg} (line {exc.lineno}, col {exc.colno})"
        )
        if args.json:
            _emit_json(
                {"ok": False, "schema": args.schema, "path": str(path),
                 "mode": "json", "errors": [msg]},
                to_stderr=True,
            )
        else:
            print(f"validate: {msg} in {path}", file=sys.stderr)
        return 1

    if args.bundle:
        if not isinstance(payload, list):
            msg = (
                f"--bundle requires a JSON array at the top level, "
                f"got {type(payload).__name__}"
            )
            if args.json:
                _emit_json(
                    {"ok": False, "schema": args.schema, "path": str(path),
                     "mode": "bundle", "errors": [msg]},
                    to_stderr=True,
                )
            else:
                print(f"validate: {msg}", file=sys.stderr)
            return 1
        try:
            all_errors = validate_bundle(
                ((args.schema, r) for r in payload),
                raise_on_error=False,
            )
        except SchemaNotFoundError as exc:
            if args.json:
                _emit_json(
                    {"ok": False, "schema": args.schema, "path": str(path),
                     "mode": "bundle", "errors": [str(exc)]},
                    to_stderr=True,
                )
            else:
                print(f"validate: {exc}", file=sys.stderr)
            return 1
        except ImportError as exc:
            if args.json:
                _emit_json(
                    {"ok": False, "schema": args.schema, "path": str(path),
                     "mode": "bundle", "errors": [str(exc)]},
                    to_stderr=True,
                )
            else:
                print(f"validate: {exc}", file=sys.stderr)
            return 1
        if all_errors:
            flat_bundle: list[str] = []
            for schema_name, msgs in all_errors.items():
                for m in msgs:
                    flat_bundle.append(f"{schema_name}: {m}")
            if args.json:
                _emit_json(
                    {"ok": False, "schema": args.schema, "path": str(path),
                     "mode": "bundle", "errors": flat_bundle},
                    to_stderr=True,
                )
            else:
                for schema_name, msgs in all_errors.items():
                    _print_errors_to_stderr(msgs, prefix=f"{schema_name}: ")
            return 1
        if args.json:
            _emit_json(
                {"ok": True, "schema": args.schema, "path": str(path),
                 "mode": "bundle", "errors": []}
            )
        return 0

    # Default: single JSON object, validate against args.schema.
    if not isinstance(payload, dict):
        msg = (
            f"expected a JSON object at the top level "
            f"(use --bundle for arrays or --jsonl for JSONL files), "
            f"got {type(payload).__name__}"
        )
        if args.json:
            _emit_json(
                {"ok": False, "schema": args.schema, "path": str(path),
                 "mode": "json", "errors": [msg]},
                to_stderr=True,
            )
        else:
            print(f"validate: {msg}", file=sys.stderr)
        return 1

    try:
        errors = validate_instance(
            payload, args.schema, raise_on_error=False
        )
    except SchemaNotFoundError as exc:
        if args.json:
            _emit_json(
                {"ok": False, "schema": args.schema, "path": str(path),
                 "mode": "json", "errors": [str(exc)]},
                to_stderr=True,
            )
        else:
            print(f"validate: {exc}", file=sys.stderr)
        return 1
    except ImportError as exc:
        if args.json:
            _emit_json(
                {"ok": False, "schema": args.schema, "path": str(path),
                 "mode": "json", "errors": [str(exc)]},
                to_stderr=True,
            )
        else:
            print(f"validate: {exc}", file=sys.stderr)
        return 1
    if errors:
        if args.json:
            _emit_json(
                {"ok": False, "schema": args.schema, "path": str(path),
                 "mode": "json", "errors": errors},
                to_stderr=True,
            )
        else:
            _print_errors_to_stderr(errors)
        return 1
    if args.json:
        _emit_json(
            {"ok": True, "schema": args.schema, "path": str(path),
             "mode": "json", "errors": []}
        )
    return 0


def cmd_fingerprint(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        if args.json:
            _emit_json(
                {"ok": False, "path": str(path),
                 "error": f"file not found: {path}"},
                to_stderr=True,
            )
        else:
            print(f"fingerprint: file not found: {path}", file=sys.stderr)
        return 1
    try:
        records = read_bundle(path)
    except FileNotFoundError:
        if args.json:
            _emit_json(
                {"ok": False, "path": str(path),
                 "error": f"file not found: {path}"},
                to_stderr=True,
            )
        else:
            print(f"fingerprint: file not found: {path}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, ValueError) as exc:
        if args.json:
            _emit_json(
                {"ok": False, "path": str(path),
                 "error": f"failed to parse {path}: {exc}"},
                to_stderr=True,
            )
        else:
            print(f"fingerprint: failed to parse {path}: {exc}",
                  file=sys.stderr)
        return 1
    digest = bundle_fingerprint(records)
    if args.json:
        _emit_json(
            {"ok": True, "path": str(path), "fingerprint": digest,
             "record_count": len(records)}
        )
    else:
        print(digest)
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    a_path = Path(args.path_a)
    b_path = Path(args.path_b)
    if not a_path.exists():
        if args.json:
            _emit_json(
                {"ok": False, "path_a": str(a_path), "path_b": str(b_path),
                 "error": f"file not found: {a_path}"},
                to_stderr=True,
            )
        else:
            print(f"diff: file not found: {a_path}", file=sys.stderr)
        return 1
    if not b_path.exists():
        if args.json:
            _emit_json(
                {"ok": False, "path_a": str(a_path), "path_b": str(b_path),
                 "error": f"file not found: {b_path}"},
                to_stderr=True,
            )
        else:
            print(f"diff: file not found: {b_path}", file=sys.stderr)
        return 1
    try:
        a = read_bundle(a_path)
        b = read_bundle(b_path)
    except FileNotFoundError as exc:
        if args.json:
            _emit_json(
                {"ok": False, "path_a": str(a_path), "path_b": str(b_path),
                 "error": f"file not found: {exc.filename or exc}"},
                to_stderr=True,
            )
        else:
            print(f"diff: file not found: {exc.filename or exc}",
                  file=sys.stderr)
        return 1
    except (json.JSONDecodeError, ValueError) as exc:
        if args.json:
            _emit_json(
                {"ok": False, "path_a": str(a_path), "path_b": str(b_path),
                 "error": f"failed to parse input: {exc}"},
                to_stderr=True,
            )
        else:
            print(f"diff: failed to parse input: {exc}", file=sys.stderr)
        return 1
    result = bundle_diff(a, b)
    if args.json:
        _emit_json(
            {"ok": True,
             "added": result.added,
             "removed": result.removed,
             "changed": [list(pair) for pair in result.changed],
             "unchanged_count": result.unchanged_count}
        )
    else:
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


def cmd_merge(args: argparse.Namespace) -> int:
    """Merge N bundles into one. See :func:`merge_bundles` for the
    full conflict-resolution semantics.
    """
    paths = [Path(p) for p in args.paths]
    if not paths:
        if args.json:
            _emit_json(
                {"ok": False, "error": "at least one input file is required"},
                to_stderr=True,
            )
        else:
            print(
                "merge: at least one input file is required",
                file=sys.stderr,
            )
        return 1

    # Check all paths exist up front so we fail fast on a bad arg
    # rather than partially loading the others.
    for p in paths:
        if not p.exists():
            if args.json:
                _emit_json(
                    {"ok": False, "error": f"file not found: {p}"},
                    to_stderr=True,
                )
            else:
                print(f"merge: file not found: {p}", file=sys.stderr)
            return 1

    bundles: list[list[dict]] = []
    for p in paths:
        try:
            bundles.append(read_bundle(p))
        except FileNotFoundError as exc:
            if args.json:
                _emit_json(
                    {"ok": False, "error": f"file not found: {exc.filename or p}"},
                    to_stderr=True,
                )
            else:
                print(
                    f"merge: file not found: {exc.filename or p}",
                    file=sys.stderr,
                )
            return 1
        except (json.JSONDecodeError, ValueError) as exc:
            if args.json:
                _emit_json(
                    {"ok": False, "error": f"failed to parse {p}: {exc}"},
                    to_stderr=True,
                )
            else:
                print(
                    f"merge: failed to parse {p}: {exc}", file=sys.stderr,
                )
            return 1

    try:
        result = merge_bundles(
            *bundles, id_field=args.id_field, prefer=args.prefer,
        )
    except ValueError as exc:
        # Raised by prefer='raise' on a content conflict.
        if args.json:
            _emit_json({"ok": False, "error": str(exc)}, to_stderr=True)
        else:
            print(f"merge: {exc}", file=sys.stderr)
        return 1

    if args.json:
        _emit_json({
            "ok": True,
            "prefer": args.prefer,
            "id_field": args.id_field,
            "input_count": len(bundles),
            "record_count": len(result.records),
            "conflict_count": len(result.conflicts),
            "duplicate_id_count": len(result.duplicate_ids),
            "conflicts": [
                [id_val, [[idx, rec] for idx, rec in variants]]
                for id_val, variants in result.conflicts
            ],
            "duplicate_ids": result.duplicate_ids,
            "records": result.records,
        })
    else:
        print(
            f"merged: {len(result.records)} records, "
            f"{len(result.conflicts)} conflict(s), "
            f"{len(result.duplicate_ids)} duplicate id(s) "
            f"(prefer={args.prefer}, id_field={args.id_field}, "
            f"inputs={len(bundles)})"
        )
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
    # ``--json`` is shared across every subcommand via this parent
    # parser. This gives ``--json`` a single source of truth (defined
    # once, not duplicated per subparser) while still surfacing it in
    # every subcommand's ``--help`` and making it work both before
    # (``--json validate ...``) and after (``validate --json ...``)
    # the subcommand name.
    #
    # ``default=argparse.SUPPRESS`` is critical: without it, a
    # subparser dispatch that does NOT see ``--json`` on its slice of
    # the command line would write ``args.json=False`` back to the
    # shared namespace, silently clobbering the top-level parser's
    # value when the user wrote ``--json <subcmd> ...``. SUPPRESS
    # tells the subparser to leave the attribute alone if it wasn't
    # given, so the top-level value (or another subparser's value)
    # is preserved.
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help=(
            "Emit a single JSON object to stdout (and a separate JSON "
            "object to stderr on failure) instead of the human-readable "
            "text output."
        ),
    )

    # ``allow_abbrev=False`` disables argparse's long-option prefix
    # matching. Without it, ``--json`` would be silently reinterpreted
    # as the existing ``--jsonl`` flag whenever it appears adjacent to
    # a subparser that defines ``--jsonl`` (e.g. ``validate``). That
    # was a real silent data-handling bug: the user typed ``--json``
    # expecting JSON output, and the validator silently entered JSONL
    # mode, accepted the file (or silently mis-validated it) without
    # any warning. Disabling abbreviation forces the user to type
    # ``--jsonl`` in full.
    parser = argparse.ArgumentParser(
        prog="python -m agent_memory_contracts",
        description=(
            "agent-memory-contracts CLI: validate, fingerprint, diff, "
            "and merge agent memory bundles."
        ),
        allow_abbrev=False,
        parents=[json_parent],
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"{PACKAGE_NAME} {_get_version()}",
    )
    sub = parser.add_subparsers(dest="subcommand", metavar="<subcommand>")

    p_validate = sub.add_parser(
        "validate",
        parents=[json_parent],
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
        parents=[json_parent],
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
        parents=[json_parent],
        help="Compare two bundles and show the set-semantic diff.",
        description=(
            "Diff two bundles and print a human-readable summary. "
            "Both inputs are treated as sets of records keyed by 'id'."
        ),
    )
    p_diff.add_argument("path_a", help="Path to the 'before' bundle.")
    p_diff.add_argument("path_b", help="Path to the 'after' bundle.")
    p_diff.set_defaults(_func=cmd_diff)

    p_merge = sub.add_parser(
        "merge",
        parents=[json_parent],
        help="Merge two or more bundles into a single bundle.",
        description=(
            "Merge two or more bundles into a single bundle. "
            "Records are deduplicated by --id-field; on duplicate ids "
            "the --prefer policy determines which version wins."
        ),
    )
    p_merge.add_argument(
        "paths",
        nargs="+",
        help=(
            "Paths to the input bundles (JSON or JSONL). At least one "
            "path is required."
        ),
    )
    p_merge.add_argument(
        "--prefer",
        choices=["last", "first", "raise"],
        default="last",
        help=(
            "Conflict resolution policy. 'last' (default) keeps the "
            "version from the last input; 'first' keeps the version "
            "from the first; 'raise' fails with a non-zero exit on any "
            "id conflict across inputs."
        ),
    )
    p_merge.add_argument(
        "--id-field",
        default="id",
        help=(
            "The field that uniquely identifies each record. "
            "Defaults to 'id'."
        ),
    )
    p_merge.set_defaults(_func=cmd_merge)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """Run the CLI. Returns a Unix-style exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    # ``--json`` uses ``default=argparse.SUPPRESS`` so that the
    # subparser's absence of the flag doesn't clobber a value set at
    # the top level. Normalize the suppressed case to ``False`` here
    # so the subcommand handlers can use a single ``if args.json:``
    # check without ``getattr`` everywhere.
    if not hasattr(args, "json"):
        args.json = False
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
