# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - 2026-06-06

### Added

- New module `agent_memory_contracts.merge` with one public function
  `merge_bundles(*bundles, *, id_field="id", prefer="last")` and a
  frozen `BundleMerge` dataclass result. The merge is many-to-one
  and set-semantic:
  - Records are deduplicated by `id_field`; intra-bundle duplicates
    are resolved by last-write-wins and reported in
    `BundleMerge.duplicate_ids`.
  - Inter-bundle content conflicts (same id, different content)
    are reported in `BundleMerge.conflicts` regardless of
    `prefer` policy.
  - `prefer="last"` (default) and `prefer="first"` resolve the
    conflict silently; `prefer="raise"` raises `ValueError`.
  - Use cases: multi-source ingest (pulling records from N
    upstream systems into one bundle), bidirectional sync
    (combining local and remote views), and backfill (stitching
    historical re-extractions into an existing bundle).
  - Like the other bundle primitives: stdlib only, dicts /
    Mappings / dataclasses accepted, content-derived canonical
    JSON comparison, deterministic output.
- New subcommand `python -m agent_memory_contracts merge <paths...>`
  with `--prefer {last,first,raise}` and `--id-field` options.
  Supports the same `--json` envelope as the other subcommands.
- New `py.typed` marker at `src/agent_memory_contracts/py.typed`,
  enabling PEP 561 typed-package support. Downstream `mypy` /
  `pyright` will pick up the library's `from __future__ import
  annotations` and per-module type hints.
- `gitignore` updates: `.mavis/` (Mavis plan state), `uv.lock` /
  `poetry.lock` / `Pipfile.lock` (the project uses setuptools), and
  local analysis scratchpads (`COMPETITIVE_ANALYSIS.md`,
  `YC_APPLICATION_DRAFT.md`) are now ignored.

### Changed

- `python -m agent_memory_contracts` CLI now supports a global
  `--json` flag that flips every subcommand (`validate`,
  `fingerprint`, `diff`, `merge`) from human-readable text output
  to a single, machine-parseable JSON object. Exit codes are
  preserved (0 success, 1 failure, 2 usage error). The
  `validate --bundle` path also got a pre-existing `NameError` fix
  (the submodule was not bound locally; the import list now
  includes `validate_bundle`).
- `argparse` parser uses `parents=[json_parent]` so `--json` is
  visible in every subcommand's `--help` and works both before and
  after the subcommand name. `allow_abbrev=False` is set on the
  top-level parser to prevent `--json` from being silently
  reinterpreted as the existing `--jsonl` flag.
- 89 new tests across three new test files (`test_merge.py`,
  `test_cli_json.py`, `test_cli_merge.py`) plus the expanded
  `test_cli.py` baseline. The full suite is 228 passed + 1
  expected skip on Python 3.10/3.11/3.12.

## [0.4.0] - 2026-06-05

### Added

- New module `agent_memory_contracts.bundle_diff` with one public
  function `bundle_diff(a, b, *, id_field="id") -> BundleDiff`. The
  result is a frozen dataclass with `added`, `removed`, `changed`,
  and `unchanged_count` fields. Set-semantic (order-insensitive,
  duplicate-ids collapse last-write-wins, dict-dataclass equivalent).
  Short-circuits via `bundle_fingerprint` when both bundles hash
  equal, so the common case (no changes) is a single hash
  comparison without iterating records.
- New module `agent_memory_contracts.__main__` exposing
  `python -m agent_memory_contracts` with three subcommands:
  - `validate <path> --schema <name> [--jsonl]`
  - `fingerprint <path>`
  - `diff <path-a> <path-b>`
  - `--help`, `--version`, sensible exit codes (0 on success, 1 on
    validation error, 2 on usage error). Stdlib `argparse` only.
- New functions on `agent_memory_contracts.jsonschema_validator`:
  - `validate_jsonl(path, schema_name, *, raise_on_error=True)`
    validates a JSONL file and returns per-line error messages.
  - `iter_validated_jsonl(path, schema_name)` is a generator that
    yields `(line_number, instance_or_errors)` for streaming use.
  Both are gated behind the existing `[jsonschema]` extra.
- 53 new tests across three new test files (test_bundle_diff.py,
  test_cli.py) and the extended test_jsonschema_validator.py. The
  full suite is 139 passed + 1 expected skip on Python 3.10/3.11/3.12.

### Changed

- `bundle_diff.py` now imports `_canonical_record` from
  `agent_memory_contracts.bundles` instead of duplicating the
  helper locally. Single source of truth for the canonical-JSON
  record representation.

## [0.3.0] - 2026-06-03

### Added

- New module `agent_memory_contracts.bundles` with one public
  function `bundle_fingerprint(records, *, id_field="id")`. Returns
  a deterministic SHA-256 hex digest of a set of records,
  content-sensitive, order-insensitive, set-semantic.
  - Records can be dicts, Mappings, or dataclass instances.
  - Records with the same ``id_field`` value are deduplicated
    before hashing (last write wins).
  - Records are sorted by id before concatenation; the separator
    is a newline, which is safe because canonical JSON cannot
    contain an unescaped newline inside a string.
  - Stdlib only -- reuses the same `sha256_hex` primitive the
    id helpers already use.
  - Use cases: cache key, idempotency token, change-detection
    digest, audit-chain anchor.
- 14 new test methods in `tests/test_bundles.py` covering:
  - Determinism (same input -> same hash; empty bundle is
    deterministic; 64 lowercase hex format).
  - Order insensitivity (reversed, shuffled, single element).
  - Content sensitivity (value change, add, remove, rename id,
    key reorder still matches).
  - Dedup by id (duplicate same content collapses; duplicate
    different content -- last write wins, deterministic for
    input order).
  - Dict-dataclass equivalence (a bundle of dataclass instances
    and a bundle of equivalent dicts hash to the same value).
  - Idempotency (re-running the same pipeline produces the
    same hash).
  - Real-world integration (uses `SourceRecord.from_dict` and
    `PreferenceLedgerEntry.from_dict` end-to-end).
  - Custom `id_field` (works for records named with a slug
    instead of ``id``).
- README section "Bundle fingerprint" with a worked example.
- README "What's in the box" lists the new primitive and bumps
  the test count to 75.

## [0.2.0] - 2026-06-03

### Added

- New optional module `agent_memory_contracts.jsonschema_validator`
  for validating records against the published JSON Schemas. The
  module is gated behind a new `[jsonschema]` extra
  (`pip install agent-memory-contracts[jsonschema]`) so the
  library stays stdlib-only at runtime.
  - `is_available()` — runtime check for the optional dep.
  - `load_schema(name)` — load one of the bundled JSON Schemas by
    short name (e.g. `"taste_card"`). Raises `SchemaNotFoundError`
    on unknown names.
  - `validate_instance(instance, schema_name, *, raise_on_error=True)`
    — validate a single record dict. Returns an empty list on
    success, or a list of formatted error messages when
    `raise_on_error` is False.
  - `validate_bundle(records, *, raise_on_error=True)` — validate a
    collection of `(schema_name, instance)` pairs, reporting per-
    schema errors.
  - `VALID_SCHEMA_NAMES` — frozen set of every schema name the
    package ships, useful for tooling and tests.
- 12 new tests in `tests/test_jsonschema_validator.py` covering
  schema load, valid instance, missing required field, bad enum
  value, `additionalProperties: false` enforcement, raise vs.
  collect modes, Python-dataclass-to-JSON-Schema roundtrip, error
  path formatting, and graceful-degradation when `jsonschema` is
  not installed.
- README section "JSON Schemas" updated with a worked example
  using the new validator.

## [0.1.0] - 2026-06-01

### Added

- Initial public release.
- Six memory planes with JSON Schema (Draft 2020-12) and Python dataclass contracts:
  - **Evidence** plane: `SourceRecord`, `EpisodeRecord`, `EvidenceSpan`
  - **Candidate** plane: `CandidateClaim`, `CandidatePreference`, `CandidateDecision`, `CandidateTask`, `CandidateTasteSignal`
  - **Ledger** plane: `FactLedgerEntry`, `PreferenceLedgerEntry`, `DecisionLedgerEntry`, `MemoryReducerDecision`
  - **Taste** plane: `TasteCard`, `TasteReducerDecision`, `TasteDeltaProposal`
  - **State** plane: `ProjectStateSnapshot`, `CoreStateSnapshot`, `StateReducerDecision`, `ProjectStateDeltaProposal`, `CoreStateDeltaProposal`
  - **ContextPack** plane: `ContextPack`, `ContextPackBuildReceipt`, `ContextPackValidationReport`
- Stable, content-derived ID helpers for every record type (`make_*_id`).
- Per-plane bundle validators: `validate_candidate_bundle`, `validate_ledger_bundle`, `validate_taste_bundle`, `validate_state_bundle`, `validate_contextpack_bundle`.
- Temporal query helpers for state and taste planes (`current_*`, `*_as_of`, `*_supersession_chain`).
- Standard-library only (no runtime dependencies).
- Optional `jsonschema`-style external validation via the included JSON Schema files.
- Apache-2.0 licensed.
