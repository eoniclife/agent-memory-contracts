# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
