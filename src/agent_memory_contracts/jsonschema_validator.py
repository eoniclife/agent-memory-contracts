"""Optional jsonschema-backed validation for the bundled JSON Schemas.

The agent-memory-contracts package ships JSON Schemas (Draft 2020-12)
in :mod:`agent_memory_contracts.schemas`. The Python dataclass
contracts in this package mirror those schemas and use stdlib-only
validation. This module adds an **opt-in** jsonschema-backed validator
for users who want:

- Validation against the published JSON Schema (the source of truth)
  in addition to the Python mirror checks. The two are kept in
  lockstep at the contract version, but validating against the
  schema directly catches drift between the JSON Schema and the
  Python dataclass if a maintainer ever changes one without the
  other.
- Polyglot interop. A Python service that produces records can
  validate them with the same schema a TypeScript / Rust / Go
  consumer will use, so the round trip is verified before the
  record leaves the producing system.
- Field-level error paths with the exact JSON Pointer to the
  field that failed, which the stdlib validators don't surface.

Install the optional extra to use this module:

.. code-block:: bash

    pip install agent-memory-contracts[jsonschema]

The module degrades gracefully: if ``jsonschema`` is not installed,
:func:`is_available` returns ``False`` and the validation functions
raise :class:`ImportError` with an actionable message rather than a
bare ``ModuleNotFoundError``.

.. versionadded:: 0.2.0
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping

try:
    import jsonschema  # type: ignore[import-not-found]
except ImportError as _exc:  # pragma: no cover
    _MISSING_IMPORT_ERROR: Exception | None = _exc
    jsonschema = None  # type: ignore[assignment]
else:
    _MISSING_IMPORT_ERROR = None


#: The dotted package path where the JSON Schema files live.
_SCHEMA_PACKAGE = "agent_memory_contracts.schemas"

#: Set of every schema name published by this package, sans the
#: ``.schema.json`` suffix. Used to validate the requested schema
#: name and to enumerate the full schema set for tests and tooling.
VALID_SCHEMA_NAMES: frozenset[str] = frozenset({
    "source_record",
    "episode_record",
    "evidence_span",
    "candidate_claim",
    "candidate_preference",
    "candidate_decision",
    "candidate_task",
    "candidate_taste_signal",
    "fact_ledger_entry",
    "preference_ledger_entry",
    "decision_ledger_entry",
    "memory_reducer_decision",
    "taste_card",
    "taste_reducer_decision",
    "taste_delta_proposal",
    "project_state_snapshot",
    "core_state_snapshot",
    "state_reducer_decision",
    "project_state_delta_proposal",
    "core_state_delta_proposal",
    "context_pack",
    "context_pack_build_receipt",
    "context_pack_validation_report",
})


class SchemaNotFoundError(ValueError):
    """Raised when a schema name is not one of the bundled schemas."""


def is_available() -> bool:
    """Return True if the ``jsonschema`` package is importable.

    Use this to gate the optional validator at runtime without
    triggering an :class:`ImportError`::

        from agent_memory_contracts import jsonschema_validator as jv
        if jv.is_available():
            jv.validate_instance(record, "taste_card")
        else:
            # fall back to the stdlib Python contract validator
            TasteCard.from_dict(record)
    """
    return jsonschema is not None


def _require_jsonschema() -> None:
    """Raise ImportError with an actionable message if jsonschema is missing."""
    if jsonschema is None:
        raise ImportError(
            "jsonschema is not installed. Install the optional extra with: "
            "pip install 'agent-memory-contracts[jsonschema]'. "
            f"(original error: {_MISSING_IMPORT_ERROR})"
        )


def load_schema(name: str) -> dict[str, Any]:
    """Load one of the bundled JSON Schemas by short name.

    Args:
        name: The schema's short name, e.g. ``"taste_card"``. Must be
            one of :data:`VALID_SCHEMA_NAMES`.

    Returns:
        The parsed JSON Schema as a dict.

    Raises:
        SchemaNotFoundError: if ``name`` is not a known schema.
        FileNotFoundError: if the schema file is missing from the
            installed package (a packaging bug, not a user error).
    """
    if name not in VALID_SCHEMA_NAMES:
        raise SchemaNotFoundError(
            f"unknown schema name: {name!r}. "
            f"Valid names: {sorted(VALID_SCHEMA_NAMES)}"
        )
    files = resources.files(_SCHEMA_PACKAGE)
    resource = files.joinpath(f"{name}.schema.json")
    if not resource.is_file():
        # This is a packaging bug: the schema name is in the
        # registry but the file isn't shipped.
        raise FileNotFoundError(
            f"schema resource not found in package: "
            f"{_SCHEMA_PACKAGE}/{name}.schema.json"
        )
    return json.loads(resource.read_text(encoding="utf-8"))


def _format_error(error: Any) -> str:
    """Format a single jsonschema error into a one-line message.

    The path is rendered as a JSON Pointer-ish string. Empty paths
    are rendered as ``<root>``.
    """
    path = "/".join(str(p) for p in error.absolute_path) or "<root>"
    return f"{path}: {error.message}"


def validate_instance(
    instance: Mapping[str, Any],
    schema_name: str,
    *,
    raise_on_error: bool = True,
) -> list[str]:
    """Validate a record dict against one of the bundled JSON Schemas.

    Args:
        instance: The record to validate, as a dict (typically the
            output of :func:`dataclasses.asdict` on one of the
            Python contract classes).
        schema_name: The schema to validate against.
        raise_on_error: If True (the default), raise
            :class:`jsonschema.ValidationError` on the first error.
            If False, collect all errors and return them as a list
            of formatted strings.

    Returns:
        An empty list if the instance is valid, otherwise a list
        of formatted error messages (only when ``raise_on_error``
        is False).

    Raises:
        SchemaNotFoundError: if ``schema_name`` is not known.
        ImportError: if ``jsonschema`` is not installed.
        jsonschema.ValidationError: if validation fails and
            ``raise_on_error`` is True.
    """
    _require_jsonschema()
    schema = load_schema(schema_name)
    if raise_on_error:
        jsonschema.validate(instance=dict(instance), schema=schema)  # type: ignore[union-attr]
        return []
    validator = jsonschema.Draft202012Validator(schema)  # type: ignore[union-attr]
    return [_format_error(e) for e in validator.iter_errors(instance)]


def validate_bundle(
    records: Iterable[tuple[str, Mapping[str, Any]]],
    *,
    raise_on_error: bool = True,
) -> dict[str, list[str]]:
    """Validate a bundle of (schema_name, instance) pairs.

    Args:
        records: An iterable of ``(schema_name, instance)`` pairs.
        raise_on_error: If True (the default), raise on the first
            schema that has any error. If False, validate every
            record and return a dict mapping schema_name to its
            list of error messages.

    Returns:
        A dict mapping each schema_name that had at least one
        error to its list of formatted error messages. Empty if
        the bundle is fully valid (and ``raise_on_error`` is False).

    Raises:
        SchemaNotFoundError: if any ``schema_name`` is not known.
        ImportError: if ``jsonschema`` is not installed.
        jsonschema.ValidationError: if validation fails for at
            least one record and ``raise_on_error`` is True.
    """
    _require_jsonschema()
    errors: dict[str, list[str]] = {}
    for schema_name, instance in records:
        record_errors = validate_instance(
            instance, schema_name, raise_on_error=False
        )
        if record_errors:
            errors.setdefault(schema_name, []).extend(record_errors)
    if raise_on_error and errors:
        first_schema, first_errors = next(iter(errors.items()))
        # Raise with the first error from the first failing schema.
        raise jsonschema.ValidationError(  # type: ignore[union-attr]
            f"{first_schema}: {first_errors[0]}"
        )
    return errors


def validate_jsonl(
    path: str | Path,
    schema_name: str,
    *,
    raise_on_error: bool = True,
) -> list[tuple[int, list[str]]]:
    """Validate a JSONL file line-by-line against one of the bundled JSON Schemas.

    Each line of the file is treated as a single JSON value and validated
    against ``schema_name``. Lines that fail to parse or fail validation
    are collected (with their 1-indexed line number) and returned.

    Args:
        path: Path to the JSONL file. Each non-empty line should be a
            complete JSON value.
        schema_name: The schema to validate every parsed record against.
        raise_on_error: If True (the default), raise
            :class:`jsonschema.ValidationError` on the first error,
            with the offending line number in the message
            (``"line N: <reason>"``). If False, validate every line
            and return a list of ``(line_number, error_messages)``
            tuples for every line that produced an error (parse or
            validation).

    Returns:
        A list of ``(line_number, error_messages)`` tuples, one per
        line that produced at least one error. Empty when the file
        is fully valid (and ``raise_on_error`` is False).

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        SchemaNotFoundError: if ``schema_name`` is not known.
        ImportError: if ``jsonschema`` is not installed.
        jsonschema.ValidationError: on the first error if
            ``raise_on_error`` is True. The message is prefixed with
            ``"line N: "``.
    """
    _require_jsonschema()
    errors: list[tuple[int, list[str]]] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line_number, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n").rstrip("\r")
            try:
                instance = json.loads(line)
            except json.JSONDecodeError as exc:
                msg = f"line {line_number}: invalid JSON: {exc}"
                errors.append((line_number, [msg]))
                if raise_on_error:
                    raise jsonschema.ValidationError(  # type: ignore[union-attr]
                        msg
                    )
                continue
            line_errors = validate_instance(
                instance, schema_name, raise_on_error=False
            )
            if line_errors:
                errors.append((line_number, line_errors))
                if raise_on_error:
                    raise jsonschema.ValidationError(  # type: ignore[union-attr]
                        f"line {line_number}: {line_errors[0]}"
                    )
    return errors


def iter_validated_jsonl(
    path: str | Path,
    schema_name: str,
) -> Iterator[tuple[int, dict | list[str]]]:
    """Yield one tuple per line of a JSONL file, validated against a bundled schema.

    The second element of each yielded tuple is either the parsed
    record (when the line is valid JSON AND validates against
    ``schema_name``) or a list of error message strings (one entry
    for a parse error, multiple for validation errors).

    This function never raises on JSON or validation errors; the
    caller decides what to do with the yielded error lists. It will
    raise on missing files, unknown schema names, and the missing
    ``jsonschema`` dependency.

    Args:
        path: Path to the JSONL file.
        schema_name: The schema to validate every parsed record against.

    Yields:
        ``(line_number, payload)`` tuples, 1-indexed. ``payload`` is
        a dict on success, or a list of error message strings on
        failure (parse or validation).

    Raises:
        FileNotFoundError: if ``path`` does not exist.
        SchemaNotFoundError: if ``schema_name`` is not known.
        ImportError: if ``jsonschema`` is not installed.
    """
    _require_jsonschema()
    with open(path, "r", encoding="utf-8") as fh:
        for line_number, raw in enumerate(fh, start=1):
            line = raw.rstrip("\n").rstrip("\r")
            try:
                instance = json.loads(line)
            except json.JSONDecodeError as exc:
                yield (
                    line_number,
                    [f"line {line_number}: invalid JSON: {exc}"],
                )
                continue
            line_errors = validate_instance(
                instance, schema_name, raise_on_error=False
            )
            if line_errors:
                yield (line_number, line_errors)
            else:
                yield (line_number, instance)


__all__ = [
    "VALID_SCHEMA_NAMES",
    "SchemaNotFoundError",
    "is_available",
    "iter_validated_jsonl",
    "load_schema",
    "validate_bundle",
    "validate_instance",
    "validate_jsonl",
]
