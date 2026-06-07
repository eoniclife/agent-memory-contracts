# Stability Policy (v1.0.0)

This document codifies the **public API surface**, the
**SemVer policy**, the **CHANGELOG discipline**, the
**schema policy**, and the **deprecation policy** for
`agent-memory-contracts` v1.0.0 and beyond.

The v1.0.0 commit is the **public API freeze**. After
v1.0.0, every public name listed in this document is
locked. Additions go in minor releases (1.1.0);
breaking changes require a major release (2.0.0).

---

## The Public API Surface

The public API is the names exported from
`agent_memory_contracts.__init__` (the `__all__` list).
Every name in `__all__` is a stable, public name. The
audit script `scripts/audit_public_api.py` walks
`__all__` and verifies that this document matches.

### Evidence plane

| Name | Module | Description |
| --- | --- | --- |
| `SourceRecord` | `evidence_contracts` | A raw input record |
| `EpisodeRecord` | `evidence_contracts` | A time-bounded segment of a source |
| `EvidenceSpan` | `evidence_contracts` | A slice of a source, cited by claims |
| `source_record_from_dict` | `evidence_contracts` | Deserialize a `SourceRecord` |
| `episode_record_from_dict` | `evidence_contracts` | Deserialize an `EpisodeRecord` |
| `evidence_span_from_dict` | `evidence_contracts` | Deserialize an `EvidenceSpan` |
| `make_source_id` | `evidence_ids` | Derive a `src_*` id |
| `make_episode_id` | `evidence_ids` | Derive an `ep_*` id |
| `make_span_id` | `evidence_ids` | Derive a `span_*` id |
| `sha256_hex` | `evidence_ids` | SHA-256 hex digest |

### Candidate plane

| Name | Module | Description |
| --- | --- | --- |
| `CandidateClaim` | `candidate_contracts` | An extracted claim (untrusted) |
| `CandidateDecision` | `candidate_contracts` | An extracted decision (untrusted) |
| `CandidatePreference` | `candidate_contracts` | An extracted preference (untrusted) |
| `CandidateTask` | `candidate_contracts` | An extracted task (untrusted) |
| `CandidateTasteSignal` | `candidate_contracts` | An extracted taste signal (untrusted) |
| `candidate_claim_from_dict` | `candidate_contracts` | Deserialize a `CandidateClaim` |
| `candidate_decision_from_dict` | `candidate_contracts` | Deserialize a `CandidateDecision` |
| `candidate_preference_from_dict` | `candidate_contracts` | Deserialize a `CandidatePreference` |
| `candidate_task_from_dict` | `candidate_contracts` | Deserialize a `CandidateTask` |
| `candidate_taste_signal_from_dict` | `candidate_contracts` | Deserialize a `CandidateTasteSignal` |
| `make_candidate_id` | `candidate_ids` | Derive a `cand_*` id |

### Ledger plane

| Name | Module | Description |
| --- | --- | --- |
| `FactLedgerEntry` | `ledger_contracts` | A trusted fact |
| `PreferenceLedgerEntry` | `ledger_contracts` | A trusted preference |
| `DecisionLedgerEntry` | `ledger_contracts` | A trusted decision |
| `MemoryReducerDecision` | `ledger_contracts` | A reducer audit record |
| `fact_ledger_entry_from_dict` | `ledger_contracts` | Deserialize a `FactLedgerEntry` |
| `preference_ledger_entry_from_dict` | `ledger_contracts` | Deserialize a `PreferenceLedgerEntry` |
| `decision_ledger_entry_from_dict` | `ledger_contracts` | Deserialize a `DecisionLedgerEntry` |
| `reducer_decision_from_dict` | `ledger_contracts` | Deserialize a `MemoryReducerDecision` |
| `ledger_entry_from_dict` | `ledger_contracts` | Generic ledger deserializer |
| `validate_candidate_bundle` | `candidate_contracts` | Cross-plane reference validator for candidates |
| `make_ledger_entry_id` | `ledger_ids` | Derive a `fact_*` / `pref_*` / `dec_*` id |
| `make_reducer_decision_id` | `ledger_ids` | Derive a `redmem_*` id |
| `validate_ledger_bundle` | `ledger_contracts` | Cross-plane reference validator |

### Taste plane

| Name | Module | Description |
| --- | --- | --- |
| `TasteCard` | `taste_contracts` | A trusted taste card |
| `TasteReducerDecision` | `taste_contracts` | A taste-reducer audit record |
| `taste_card_from_dict` | `taste_contracts` | Deserialize a `TasteCard` |
| `current_taste_cards` | `taste_contracts` | The current taste cards (active status) |
| `is_taste_card_active_at` | `taste_contracts` | Temporal query for taste cards |
| `taste_cards_as_of` | `taste_contracts` | The taste cards as of a timestamp |
| `taste_supersession_chain` | `taste_contracts` | The supersession chain of a taste card |
| `taste_reducer_decision_from_dict` | `taste_contracts` | Deserialize a `TasteReducerDecision` |
| `validate_taste_bundle` | `taste_contracts` | Cross-plane reference validator for taste |
| `make_taste_card_id` | `taste_ids` | Derive a `taste_*` id |
| `make_taste_reducer_decision_id` | `taste_ids` | Derive a `redtaste_*` id |

### State plane

| Name | Module | Description |
| --- | --- | --- |
| `ProjectStateSnapshot` | `state_contracts` | A snapshot of project state |
| `CoreStateSnapshot` | `state_contracts` | A snapshot of core state |
| `StateReducerDecision` | `state_contracts` | A state-reducer audit record |
| `project_state_from_dict` | `state_contracts` | Deserialize a `ProjectStateSnapshot` |
| `core_state_from_dict` | `state_contracts` | Deserialize a `CoreStateSnapshot` |
| `current_project_states` | `state_contracts` | The current project states |
| `current_core_states` | `state_contracts` | The current core states |
| `current_core_state` | `state_contracts` | The current single core state |
| `is_project_state_active_at` | `state_contracts` | Temporal query for project state |
| `is_core_state_active_at` | `state_contracts` | Temporal query for core state |
| `project_states_as_of` | `state_contracts` | The project states as of a timestamp |
| `core_states_as_of` | `state_contracts` | The core states as of a timestamp |
| `project_state_supersession_chain` | `state_queries` | The supersession chain of a project state |
| `core_state_supersession_chain` | `state_queries` | The supersession chain of a core state |
| `project_state_for_project` | `state_queries` | The current state of a single project |
| `state_reducer_decision_from_dict` | `state_contracts` | Deserialize a `StateReducerDecision` |
| `validate_state_bundle` | `state_contracts` | Cross-plane reference validator for state |
| `make_project_state_id` | `state_ids` | Derive a `projstate_*` id |
| `make_core_state_id` | `state_ids` | Derive a `corestate_*` id |
| `make_state_reducer_decision_id` | `state_ids` | Derive a `redstate_*` id |

### ContextPack plane

| Name | Module | Description |
| --- | --- | --- |
| `ContextPack` | `contextpack_contracts` | A task-ready context bundle |
| `ContextPackBuildReceipt` | `contextpack_contracts` | A `BuildReceipt` (audit) |
| `ContextPackValidationReport` | `contextpack_contracts` | A `ValidationReport` (audit) |
| `context_pack_from_dict` | `contextpack_contracts` | Deserialize a `ContextPack` |
| `context_pack_build_receipt_from_dict` | `contextpack_contracts` | Deserialize a `BuildReceipt` |
| `context_pack_validation_report_from_dict` | `contextpack_contracts` | Deserialize a `ValidationReport` |
| `validate_contextpack_bundle` | `contextpack_validation` | Cross-plane reference validator |
| `make_context_pack_id` | `contextpack_ids` | Derive a `ctx_*` id |
| `make_context_pack_build_receipt_id` | `contextpack_ids` | Derive a `rcpt_*` id |
| `make_context_pack_validation_report_id` | `contextpack_ids` | Derive a `vrpt_*` id |

### Bundle primitives

| Name | Module | Description |
| --- | --- | --- |
| `bundle_fingerprint` | `bundles` | SHA-256 fingerprint of a bundle |
| `bundle_diff` | `bundle_diff` | Set-semantic diff of two bundles |
| `merge_bundles` | `merge` | Set-semantic merge of N bundles |
| `BundleMerge` | `merge` | The result of a merge (with conflict list) |
| `ConflictResolution` | `conflict` | An audit record for a resolved conflict |
| `resolve_conflict` | `conflict` | Build a `ConflictResolution` |
| `apply_resolutions` | `conflict` | Apply resolutions to a bundle |
| `validate_resolutions` | `conflict` | Validate a list of resolutions |
| `MemoryHygieneReport` | `hygiene` | Hygiene report (counts, by plane) |
| `compute_hygiene_report` | `hygiene` | Compute a report for a bundle |
| `hygiene_report_to_markdown` | `hygiene` | Format a report as markdown |

### Provenance (v0.8.0)

| Name | Module | Description |
| --- | --- | --- |
| `CitationGraph` | `citations` | A derived, frozen citation DAG |
| `CitationNode` | `citations` | A node in the graph |
| `CitationEdge` | `citations` | A directed edge in the graph |
| `CitationPath` | `citations` | A walk through the graph |
| `DanglingRef` | `citations` | A reference to a missing record |
| `find_unsupported_claims` | `citations` | Find claims with no source chain |
| `find_unused_sources` | `citations` | Find sources not cited by any claim |
| `find_dangling_refs` | `citations` | Find missing references |
| `default_claim_predicate` | `citations` | Default "is this a claim?" predicate |
| `default_source_predicate` | `citations` | Default "is this a source?" predicate |

### Access control (v0.9.0)

| Name | Module | Description |
| --- | --- | --- |
| `PRIVACY_CLASS_ORDER` | `access` | Linear order of privacy classes |
| `BundleScope` | `access` | A scope (max privacy, record-type filter, name) |
| `AccessDecision` | `access` | Per-record allow/drop/redact decision |
| `AccessSummary` | `access` | Aggregate counts from decisions |
| `check_access` | `access` | Per-record scope check |
| `scope_bundle` | `access` | Whole-bundle filter |
| `summarize_access` | `access` | Aggregate decisions into a summary |
| `public_scope` | `access` | Scope factory: only public records |
| `team_scope` | `access` | Scope factory: up to internal |
| `customer_scope` | `access` | Scope factory: up to private |
| `private_scope` | `access` | Scope factory: all records |

### Embedding input (v1.0.0-alpha.1)

| Name | Module | Description |
| --- | --- | --- |
| `DEFAULT_MAX_CHARS` | `embedding` | Default `max_chars` for rendering |
| `EmbeddingInput` | `embedding` | Canonical input to an embedding model |
| `record_to_embedding_input` | `embedding` | Render a record as an `EmbeddingInput` |
| `text_for_record_type` | `embedding` | Per-type text renderer (public) |
| `embedding_input_to_dict` | `embedding` | Serialize an `EmbeddingInput` |
| `embedding_input_from_dict` | `embedding` | Reconstruct an `EmbeddingInput` |

### Schema migration (v1.0.0-alpha.2)

| Name | Module | Description |
| --- | --- | --- |
| `CURRENT_SCHEMA_VERSION` | `migrations` | The library's current schema version ("1.0.0") |
| `MigrationStep` | `migrations` | A single migration step |
| `MigrationResult` | `migrations` | The result of a migration |
| `SchemaMigrator` | `migrations` | A mutable registry of steps |
| `apply_migrations` | `migrations` | Lower-level apply function |
| `default_migrator` | `migrations` | Empty-registry factory |
| `migrate_bundle` | `migrations` | Top-level convenience |

### ContextPack compiler (v1.0.0-alpha.3)

| Name | Module | Description |
| --- | --- | --- |
| `ContextPackTask` | `compilation` | A task description for the compiler |
| `CompilationPolicy` | `compilation` | The compiler's configuration |
| `CompilationResult` | `compilation` | The compiler's output |
| `compile_context_pack` | `compilation` | The headline compile function |

### LangChain integration (v1.0.1)

The integration lives in the optional
`agent_memory_contracts.integrations.langchain` module. It
requires the `langchain-classic` package (installed via
`pip install agent-memory-contracts[langchain]`).

| Name | Module | Description |
| --- | --- | --- |
| `ContractsMemory` | `integrations.langchain` | A `BaseMemory` subclass |
| `ContractsMemoryConfig` | `integrations.langchain` | Configuration dataclass |
| `MemoryStore` | `integrations.langchain` | In-memory bundle store |

### MCP server (v1.0.2)

The MCP server lives in the optional
`agent_memory_contracts.integrations.mcp` module. It
requires the `fastmcp` package (installed via
`pip install agent-memory-contracts[mcp]`).

| Name | Module | Description |
| --- | --- | --- |
| `ContractsMCPServer` | `integrations.mcp` | The MCP server class |
| `MCPConfig` | `integrations.mcp` | Configuration dataclass |
| `run_server` | `integrations.mcp` | Entry-point function |

### CLI

The library ships a CLI via `python -m agent_memory_contracts`:

| Subcommand | Module | Description |
| --- | --- | --- |
| `validate` | `__main__` | Validate a record against a schema |
| `fingerprint` | `__main__` | Compute a bundle's fingerprint |
| `diff` | `__main__` | Diff two bundles |
| `merge` | `__main__` | Merge N bundles |
| `hygiene` | `__main__` | Compute a hygiene report |

A console script `agent-memory-contracts` is installed
alongside the module form.

---

## SemVer Policy

After v1.0.0, the library follows [Semantic Versioning](https://semver.org/):

- **Patch (`1.0.x`):** bug fixes only. No new features, no
  signature changes, no removals.
- **Minor (`1.x.0`):** new features, backwards compatible.
  New public names are added; existing public names
  do not change. A deprecation marker may be added
  (see Deprecation Policy below).
- **Major (`x.0.0`):** breaking changes allowed. Public
  names may be removed or renamed; signatures may
  change. A deprecation cycle is required for any
  public name that is removed (see Deprecation Policy
  below).

### Pre-release tags

- `1.0.0aN` — alpha: APIs may change. Used during the
  v1.0.0 development cycle (24a, 24b, 24c, 24d).
- `1.0.0bN` — beta: APIs are locked but bugs may
  exist. Used for early customer testing.
- `1.0.0rcN` — release candidate: production-quality
  code; release-blocker bug fixes only.

The v1.0.0 final release removes the pre-release
suffix. Subsequent releases follow the standard
`MAJOR.MINOR.PATCH` format.

---

## CHANGELOG Discipline

Every release has a section in `CHANGELOG.md` with the
following structure (the existing [Keep a Changelog](https://keepachangelog.com/) format):

```markdown
## [MAJOR.MINOR.PATCH] - YYYY-MM-DD

### Added
- New feature 1
- New feature 2

### Changed
- Behavior change 1

### Removed
- Deprecated feature removed (with migration note)

### Fixed
- Bug fix 1
```

PRs that change the public API must include a
CHANGELOG entry in the same PR. The PR template
includes a CHANGELOG checkbox.

Breaking changes (anything that would require a major
version bump) go in their own `### BREAKING` section
with a clear migration note.

---

## Schema Policy

The 24 JSON Schemas in
`src/agent_memory_contracts/schemas/` are at
`"1.0.0"` (the `SCHEMA_VERSION` constant in each
contract module). The schemas are content-stamped: the
hash of a record's canonical JSON is the record's id.

A schema change requires four steps:

1. **A migration step** registered in
   `default_migrator()` (or in a per-product
   migrator). The step's `migrate_record` callable
   takes a record at the old version and returns a
   record at the new version.
2. **A CHANGELOG entry** under `### Changed` (or
   `### BREAKING` if the change is breaking).
3. **A bump in the schema's `SCHEMA_VERSION`
   constant** in the relevant `*_contracts.py` module.
4. **A schema-migration test** in
   `tests/test_migrations.py` that exercises the
   migration step end-to-end.

The first schema migration is expected in v1.1.0 (the
"decay primitives" sprint). The v1.0.0 commit is the
freeze; the v1.1.0 commit is the first post-freeze
schema bump.

### JSON Schema files

The `schemas/*.json` files in
`src/agent_memory_contracts/schemas/` are the JSON
Schema representations of each contract type. They
are versioned with the same `SCHEMA_VERSION` constant
as the Python dataclass. Schema validation (when
`jsonschema` is installed) checks the record's
structure against these JSON Schemas.

---

## Deprecation Policy

A public name can be deprecated but not removed
within the same minor release. The deprecation cycle:

1. **Mark as deprecated** in v1.x.0:
   - Add a `DeprecationWarning` at import time.
   - Add a CHANGELOG entry under `### Deprecated`.
   - Add a `@deprecated` decorator (or a `# DEPRECATED:`
     comment) at the name's definition site.
2. **Remove in v1.(x+1).0 or later** (at least one
   minor release must pass between deprecation and
   removal).

Example deprecation timeline:
- v1.2.0: `old_name` is marked deprecated.
- v1.3.0: `old_name` emits a `DeprecationWarning`.
- v1.4.0: `old_name` is removed (with a `Removed`
  CHANGELOG entry pointing to the migration path).

---

## Audit Procedure

The audit script `scripts/audit_public_api.py` walks
`agent_memory_contracts.__all__` and verifies that this
document matches. The script exits 0 if they match,
1 if they don't. The CI workflow runs the script on
every push to `main`.

To run the audit locally:

```bash
python scripts/audit_public_api.py
```

A new public name added to `__all__` without a
corresponding entry in this document will fail the
audit. The fix is to add the name to this document in
the same PR that adds it to `__all__`.

---

## What This Document Does Not Cover

- **Backwards-incompatible renames** in the codebase
  that don't change a public name. These are internal
  refactors; the SemVer policy does not require a
  major version bump for internal changes. The
  internal refactor must not change the public name's
  semantics.
- **Performance changes.** Performance improvements
  are bug fixes (patch-level). Performance regressions
  are bug fixes (also patch-level, but the fix is
  likely a minor version's "regression fix").
- **Documentation-only changes.** No version bump.
  The CHANGELOG does not list documentation changes.
- **Test-only changes.** No version bump. Tests are
  not part of the public API; they are a
  contract-on-themselves.

---

## Bottom Line

v1.0.0 is the **commitment**: the public API is
frozen; the schema is at "1.0.0"; the migration
framework is the safety net; the CHANGELOG is the
audit trail. v1.1.0 is the first sprint that may add a
new public name, add a field to a schema, or remove a
deprecated name. v2.0.0 is the first sprint that may
break compatibility.

The library is production-ready. Use it.
