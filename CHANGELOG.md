# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
