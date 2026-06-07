# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2026-06-07

The first stable release of `agent-memory-contracts`. The public
API is now frozen; everything in `__all__` is contract. Pre-1.0
releases (`0.1.0` through `0.9.0` + `1.0.0-alpha.1` through
`1.0.0-alpha.4`) are kept for traceability but are superseded by
this release.

### Added

- **Stability commitment.** New document
  `docs/STABILITY.md` describes the v1.0.0 stability contract:
  - The complete public API surface, listed by plane, with
    one row per name in `__all__`.
  - SemVer policy: bug-fix releases in `1.0.x`, new features
    in `1.x.0`, breaking changes in `x.0.0`. Pre-release tags
    (`a` / `b` / `rc`) reserved for the next major or minor.
  - CHANGELOG discipline: every release has a
    "Keep a Changelog" entry with `### Added`, `### Changed`,
    `### Removed`, `### Fixed` sections. Empty sections
    omitted.
  - Schema policy: `SCHEMA_VERSION = "1.0.0"` is the current
    schema string. Bumping it requires a registered
    `MigrationStep` in `default_migrator()`, a CHANGELOG
    entry, and a test in `tests/test_migrations.py`.
  - Deprecation policy: 3-minor-version sunset. v1.2.0 marks
    deprecated, v1.3.0 emits `DeprecationWarning`, v1.4.0
    removes (with `### Removed` section in CHANGELOG).
  - Audit procedure: `scripts/audit_public_api.py` walks
    `__all__` and verifies every name is documented in
    `STABILITY.md`. The audit is wired into CI and is a
    required check for any release.
- **Public API audit script** (`scripts/audit_public_api.py`).
  Reads `src/agent_memory_contracts/__init__.py` `__all__`,
  parses the `## The Public API Surface` table in
  `STABILITY.md`, and exits non-zero with the diff if the two
  diverge. Designed for `pytest`-style CI integration.
- **PyPI publish pipeline.** Library is now ready to publish to
  PyPI via GitHub Actions using [trusted publishing (OIDC)](https://docs.pypi.org/trusted-publishers/).
  New workflow: `.github/workflows/publish.yml`. Triggers on GitHub
  Release (`types: [published]`) and on manual `workflow_dispatch`
  (with a TestPyPI checkbox for dry-runs). Builds sdist + wheel,
  verifies `py.typed` marker and 24 schemas are present, and
  publishes via the OIDC token from the `pypi` environment — no
  long-lived PyPI token to leak.
- **Console script entry point.** `pip install agent-memory-contracts`
  now installs an `agent-memory-contracts` console script in
  addition to the existing `python -m agent_memory_contracts`
  module form. Both work; the console script is the recommended
  form in the README.
- **Install instructions + PyPI badge** in `README.md`. The library
  has zero runtime dependencies (stdlib only); the `[jsonschema]`
  extra is documented as optional for `validate` and the
  JSON-Schema validator.

### Changed

- `pyproject.toml`:
  - Version: `1.0.0a4` → `1.0.0`.
  - Added `[project.scripts]` entry: `agent-memory-contracts`
    → `agent_memory_contracts.__main__:main`.
  - Added `Changelog` URL to `[project.urls]`.
  - Added explicit `py.typed` to `[tool.setuptools.package-data]`
    (PEP 561; some setuptools versions do not auto-include it).
- `src/agent_memory_contracts/__init__.py`:
  - `__version__ = "1.0.0"`.
- No library-code or schema changes from the alpha-4 line. All
  501 existing tests still pass; `mypy --strict` is clean across
  all 28 source modules.

### Removed

- None. The 7-day deprecation window has not been used in the
  v1.0.0 line. `DeprecationWarning` is reserved for the
  deprecation policy in `STABILITY.md`; no names are currently
  deprecated.

### Bottom line

The library is **production-ready. Use it.** The public API
is frozen; subsequent `1.x.0` releases will add new names but
will not break existing names. Schema migrations are the only
backwards-incompatible event, and the `default_migrator()`
registry is the safety net.

## [1.0.1] - 2026-06-07

A backwards-compatible minor release. New optional integration
with LangChain's `BaseMemory`; no library-code or schema changes
in the core.

### Added

- **LangChain memory backend integration.**
  New optional module
  `agent_memory_contracts.integrations.langchain` (3 public
  names: `ContractsMemory`, `ContractsMemoryConfig`,
  `MemoryStore`). It is a real `BaseMemory` subclass —
  `ContractsMemory` extends `langchain_classic.base_memory.BaseMemory`
  and implements the four abstract methods
  (`memory_variables`, `load_memory_variables`,
  `save_context`, `clear`).

  Each `save_context` call records the conversation turn as
  an `EpisodeRecord` with two `EvidenceSpan` records (one for
  the input, one for the output). Each `load_memory_variables`
  call returns a session-shaped context_pack (a subset of the
  full `ContextPack` shape) containing the most-recent N
  episodes, their evidence, and the conversation source.

  The integration is the proof that the library is composable
  with the most popular LLM framework. The chain gets
  memory without writing a custom memory class:

  ```python
  from agent_memory_contracts.integrations.langchain import ContractsMemory
  from langchain_classic.chains import ConversationChain

  memory = ContractsMemory(session_id="user-42")
  chain = ConversationChain(llm=my_llm, memory=memory)
  chain.run("Hello, what's the capital of France?")  # records the turn
  chain.run("And Spain?")                            # reads prior turns
  ```

- **`[langchain]` optional extra.** `pyproject.toml` now
  declares `langchain = ["langchain-classic>=1.0"]`. The
  integration is not imported by the core library; users
  install the extra explicitly. A new `[all]` extra bundles
  `[jsonschema,langchain]`.

- **Example script** `examples/langchain_memory.py` — a
  runnable demonstration of the integration with a
  `FakeListChatModel` (no API key required).

- **Test file** `tests/test_integrations_langchain.py` —
  19 tests gated on `pytest.importorskip("langchain_classic.base_memory")`.
  Tests cover: `BaseMemory` subclass shape, save/load
  round-trip, `max_records_per_load` cap, `clear` semantics,
  `MemoryStore` (put/get/dedup/eviction/session isolation),
  config defaults, and a minimal in-memory chain smoke test.

- **Spec doc** `docs/specs/sprint_25_langchain_memory.md` —
  the durable rationale for the integration shape.

### Changed

- `docs/STABILITY.md` lists the 3 new public names in a new
  "LangChain integration (v1.0.1)" section.
- `pyproject.toml` adds the `[langchain]` and `[all]` extras.

### Removed

- None. The 7-day deprecation window has not been used in the
  v1.0.x line.

### Notes

- The integration is gated on `langchain-classic>=1.0`. The
  `BaseMemory` API is in `langchain_classic.base_memory` in
  modern LangChain (0.3+); we do not depend on the legacy
  `langchain<0.1` package.
- The integration does **not** use the library's
  `compile_context_pack`. The compiler requires a state
  record (a long-term ledger shape), which is the wrong fit
  for a session memory. The integration shapes the bundle
  directly: episodes in chronological order, evidence spans
  grouped by episode, sources listed once. This is honest
  about what the integration is (a session memory), not
  what it isn't (a long-term fact ledger).
- `BaseMemory` is deprecated in modern LangChain (since
  0.3.3, removal in 2.0.0; the recommended replacement is
  `langchain.agents.create_agent` with checkpointing or the
  `Store` API). The integration is still useful today and
  will continue to work for users on the classic memory
  pattern. A v1.x+ consideration is a `Store`-based
  adapter for the modern LangGraph path.

## [1.1.0] - 2026-06-07

The first release in the v1.1.x line. The first concrete
schema migration; the first new feature since v1.0.0 final.
This is the proof that the v1.0.0 stability commitment is
honest, not theoretical: a real schema bump, with a real
migration step, that doesn't break v1.0.0 records.

### Added

- **Decay module.** New `agent_memory_contracts.decay`
  module (5 public names: `DecayPolicy`, `DecayScore`,
  `apply_decay`, `default_decay_policy`,
  `v1_0_0_to_v1_1_0_step`). The module computes a
  freshness score (0.0–1.0) for each record at read
  time, used by the context_pack compiler to weight
  records in the selection. Exponential or linear
  decay curves; configurable half-life; event weights
  for supersession and new-evidence.

- **First concrete schema migration step.** The v1.0.0
  → v1.1.0 migration is registered in
  `default_migrator()`. The step adds an optional
  `freshness_score` field to ledger entries, taste
  cards, and state snapshots; bumps `schema_version`
  to "1.1.0". The step is a no-op for the field
  itself (existing records get `freshness_score: null`;
  the compiler handles the None case gracefully).

- **Auto-migration in dataclass `from_dict` and
  high-level validators.** Calling `from_dict` on a
  v1.0.0 record transparently migrates it to v1.1.0
  before building the dataclass. The high-level
  validators (`validate_ledger_bundle`,
  `validate_taste_bundle`, `validate_state_bundle`)
  also auto-migrate records before validating. This
  makes the migration transparent to products that
  don't need to deal with versions.

- **6 JSON Schemas updated to v1.1.0**:
  `fact_ledger_entry`, `preference_ledger_entry`,
  `decision_ledger_entry`, `taste_card`,
  `project_state_snapshot`, `core_state_snapshot`.
  Each adds the optional `freshness_score` field
  (type: `number | null`, default: `null`).

- **`freshness_score` field added to 6 dataclasses**
  (3 ledger entries, taste card, 2 state snapshots).
  Default value: `None`. Field is positioned at the
  end of each class to avoid breaking the dataclass
  field-order contract (no required fields after
  defaulted fields).

- **Example script** `examples/decay.py` —
  demonstrates decay scoring on a small bundle of
  facts at various ages, plus the first concrete
  migration step.

- **Test file** `tests/test_decay.py` — 15 tests
  covering `DecayPolicy` construction, `apply_decay`
  for exponential and linear curves, supersession
  event weighting, malformed-timestamp fallback,
  and the migration step.

- **Spec doc** `docs/specs/sprint_27_decay.md` —
  the durable rationale for the decay primitive
  and the v1.1.0 schema bump.

### Changed

- `docs/STABILITY.md` lists the 5 new public names
  in a new "Decay (v1.1.0)" section.

### Removed

- None.

### Notes

- **The `schema_version` constant in the 3 affected
  Python source files (ledger, taste, state) is now
  "1.0.0" with a lenient check** that accepts
  "1.0.0" or "1.1.0". This means v1.0.0 records
  continue to pass the dataclass `validate()` check.
  The 6 JSON Schemas are stricter: they require
  "1.1.0" (so a raw v1.0.0 record fails JSON schema
  validation; it must be auto-migrated first).
- **Migration framework's first real use.**
  `default_migrator()` is no longer empty; it
  registers the v1.0.0 → v1.1.0 step. The framework
  has been waiting for this since v1.0.0a2.
- **Decay is opt-in.** The compiler's
  `compile_context_pack` continues to default to no
  decay (preserving the v1.0.0 behavior). A future
  release will wire decay into the compiler's
  selection score; for v1.1.0, `apply_decay` is
  available as a standalone function for products
  that want to compute scores.

## [0.8.0] - 2026-06-06

### Added

- New module `agent_memory_contracts.citations` with the
  citation graph + provenance traversal primitives:
  - `CitationNode` (frozen dataclass) — a typed wrapper
    around a record in the graph. Three node kinds:
    `source` (SourceRecord, EpisodeRecord), `evidence`
    (EvidenceSpanRecord), `claim` (candidates, ledger
    entries, taste cards, context packs).
  - `CitationEdge` (frozen dataclass) — a directed edge
    with one of two relations: `cites` (claim -> evidence)
    or `derives_from` (evidence -> source).
  - `CitationPath` (frozen dataclass) — a single walk
    through the graph, with `length` and `is_supported()`
    helpers.
  - `DanglingRef` (frozen dataclass) — a citation from
    one record to another that is not present in the
    bundle. Transient analysis artifact, no id.
  - `CitationGraph` (frozen dataclass) — the graph itself,
    built from a bundle via `CitationGraph.from_bundle(bundle)`.
    Exposes `traverse` (BFS, forward/backward/both,
    bounded by `max_depth`), `descendants`, `predecessors`,
    `shortest_path`, `size`, `node_count_by_kind`,
    `has_node`, `get_node`, `dangling_refs`.
  - `find_unsupported_claims(bundle, *, claim_predicate=...)`
    — returns every claim in the bundle with no path to a
    source. Sorted by id.
  - `find_unused_sources(bundle, *, source_predicate=...)`
    — returns every source in the bundle with no path
    from a claim. Sorted by id.
  - `find_dangling_refs(bundle)` — returns every dangling
    reference. Sorted by (from_id, missing_id, relation).
  - `default_claim_predicate(record)` — any record with
    `evidence_span_ids` or `evidence_id` is a claim.
  - `default_source_predicate(record)` — SourceRecord or
    EpisodeRecord is a source.
- New `examples/citations.py` — worked example covering
  linear chain, diamond, disconnected (with dangling ref),
  and dict-form records.

### Behavioral notes

- The graph is **derived**, not stored. Build it on demand
  from a bundle; do not persist it. Frozen dataclass
  convention applies: no `add_node` / `remove_edge`.
- The graph is structurally a **DAG** (sources point to
  nothing, evidence points only to sources, claims point
  only to evidence). Cycles are not structurally possible.
  A cycle in the input raises `ValueError` from
  `from_bundle` (defensive check).
- `MemoryReducerDecision` is an audit record, not a claim.
  It carries `evidence_span_ids` for traceability but is
  excluded from the citation graph (the decision itself
  is not a claim).
- Both dataclass records and dict/Mapping records are
  accepted by `from_bundle`; classification is
  shape-based (`source_type`, `episode_type`,
  `span_hash_sha256`, `evidence_span_ids` / `evidence_id`).

### Test discipline

- 43 new tests in `tests/test_citations.py` covering:
  frozen-dataclass surface, build from empty/linear/
  diamond/disconnected/mixed-plane fixtures, dict-form
  classification, BFS traversal in all directions,
  `max_depth` semantics, `shortest_path`, the two audit
  queries, the dangling-refs function, default
  predicates, and public API exports.
- Total: 368 tests passing (was 325 in v0.7.0), 1
  expected skip. `mypy --strict` clean on all 23
  source files. The build artifact (when v0.8.0 is
  published) will be a ~110 KB wheel + ~130 KB sdist.

### Out of scope for v0.8.0

- No `citations` CLI subcommand. Primitives are library-
  only; one line of glue if a UX needs it.
- No graph serialization format. Persist a bundle; build
  the graph on demand.
- No trust scoring or confidence propagation. The graph
  is structural.
- No graph analytics (PageRank, centrality, clustering).
- No cycle handling. DAG is structural; cycles raise.

## [1.0.0-alpha.4] - 2026-06-07

### Added

- **End-to-end company brain demo**
  (`examples/company_brain_demo.py`). A single Python
  script that runs the full pipeline from raw
  sources to a task-ready `ContextPack`. The demo is
  the marketing artifact for the library's v1.0.0
  story — what an accelerator partner sees in the
  5-minute demo.

  The 7 stages:
  1. **Ingest** — 3 raw sources → 3 `SourceRecord` + 6
     `EvidenceSpan`.
  2. **Extract** — Simulated LLM output → 8
     `CandidateClaim`.
  3. **Reduce** — Promote 5 to trusted
     `FactLedgerEntry`; reject 3 (low confidence /
     stale).
  4. **Cite** — `CitationGraph.from_bundle`: all 6
     promoted facts have source chains; 0 dangling
     refs; 0 unsupported claims.
  5. **Access** — `team_scope`: 5 of 6 records
     allowed; 1 dropped (highly_sensitive).
  6. **Embed** — 5 `EmbeddingInput`s (the input
     boundary).
  7. **Compile** — `ContextPack` for the task, with
     `BuildReceipt` and `ValidationReport` attached.

  The demo is deterministic, ~200 LOC, runs in
  <5 seconds, uses only the public API, and runs as
  part of the "all examples" smoke test in CI.

### Out of scope for v1.0.0-alpha.4

- No new library code. The demo uses the existing
  public API only.
- No LLM calls. The demo simulates LLM output with
  hard-coded candidates.
- No embedding model calls. The demo renders
  `EmbeddingInput` but doesn't call an embedding
  model.
- No vector storage.

## [1.0.0-alpha.3] - 2026-06-07

### Added

- New module `agent_memory_contracts.compilation` with
  the ContextPack compiler:
  - `ContextPackTask` (frozen dataclass) — the task
    description the compiler is producing a pack for.
    Mirrors the seven required keys of the
    `ContextPack.task` field.
  - `CompilationPolicy` (frozen dataclass) — the
    compiler's configuration. `max_records=50`,
    `require_source_coverage=True`,
    `selection_strategy="recent"`, `exclude_stale=True`,
    `exclude_retracted=True`, `exclude_contested=False`
    by default. Builder/validator agent strings.
  - `CompilationResult` (frozen dataclass) — the
    output. `context_pack`, `build_receipt`,
    `validation_report`, `selected_record_ids`,
    `excluded_record_ids`, `selection_score_by_id`.
  - `compile_context_pack(bundle, *, task, scope=None,
    policy=None)` — the headline function. Filters
    the bundle by scope, schema-validates each
    record, applies the status filter, enforces
    source coverage (when `require_source_coverage=True`),
    scores and selects the top N records, and
    produces a `ContextPack` with a `BuildReceipt`
    and `ValidationReport`.

### Behavioral notes

- **Source coverage is enforced by default.** Every
  claim in the pack is required to have a path to a
  `SourceRecord` (or `EpisodeRecord`) in the
  citation graph. Claims without a path are
  excluded with reason `"no_source_backing"`. The
  product can disable with
  `require_source_coverage=False` (the compiler
  synthesizes a placeholder primary evidence span
  in that case).
- **State is required.** The `ContextPack` schema
  requires at least one `core_state_id` or
  `project_state_ids` reference. The compiler
  raises `ValueError` if the bundle has no state
  record. The product is responsible for ensuring
  the bundle has at least one `CoreStateSnapshot` or
  `ProjectStateSnapshot`.
- **Three selection strategies.** `"recent"`
  (default): sort by timestamp descending.
  `"diverse"`: pick one record per `record_type`,
  rotating. `"frequent"`: sort by citation graph
  in-degree (most-cited first).
- **Status filter.** `stale` and `retracted` are
  excluded by default. `contested` is kept by
  default (useful for "what's the disagreement?"
  queries). All three flags are configurable.
- **Deterministic.** Two calls with the same
  `(bundle, task, scope, policy)` produce the same
  `CompilationResult`. The `pack_hash_sha256` is
  the same. The `BuildReceipt.excluded` list is
  sorted by `(reason, id)`.
- **Idempotent.** Calling `compile_context_pack`
  twice on the same inputs produces the same
  result.
- **A pure function.** No I/O, no randomness, no
  global state. Side effects are limited to reading
  the wall clock for `created_at` / `validated_at`
  timestamps (which the product can override via
  a frozen-dataclass policy if determinism is
  required at the second level).

### Test discipline

- 24 new tests in `tests/test_compilation.py`
  covering: `ContextPackTask` invariants,
  `CompilationPolicy` invariants,
  `CompilationResult` frozen-ness, basic
  compilation, source-coverage enforcement, status
  filter, `max_records` cap, scope filtering,
  empty-bundle raising, no-state-record raising,
  selection strategies (recent, diverse,
  frequent), `require_source_coverage=False`
  path, dataclass input, public API exports.
- Total: 501 tests passing (was 477 in
  v1.0.0-alpha.2), 1 expected skip. `mypy --strict`
  clean on all 27 source files.

### Out of scope for v1.0.0-alpha.3

- No embedding model integration. The compiler
  produces a `ContextPack`; the product embeds it.
- No vector storage or similarity search.
- No retrieval pre-filtering. The product can
  pre-filter by semantic similarity before calling
  the compiler; the compiler selects from the
  filtered set.
- No CLI subcommand.
- No schema changes. The 23 JSON Schemas (now 24,
  including the new `compilation` module's types)
  stay unchanged.

## [1.0.0-alpha.2] - 2026-06-07

### Added

- New module `agent_memory_contracts.migrations` with the
  schema migration framework:
  - `MigrationStep` (frozen dataclass) — a single
    migration from one schema version to another.
    Fields: `from_version`, `to_version`, `description`,
    `migrate_record` (a `Callable[[dict], dict]`).
  - `MigrationResult` (frozen dataclass) — the outcome
    of applying a chain. Fields: `bundle`,
    `target_version`, `steps_applied` (a tuple of
    `(from, to)` pairs), `records_migrated`,
    `records_unchanged`, `errors` (reserved for future
    per-record-error-collection).
  - `SchemaMigrator` (mutable registry) — registers
    `MigrationStep` objects; provides
    `register` / `registered_steps` / `has_path` /
    `find_path` (BFS over the registered edges) /
    `migrate_bundle`.
  - `apply_migrations(records, steps, *, target_version)`
    — the lower-level primitive that walks a record
    through a chain of steps, setting `schema_version`
    correctly at each step.
  - `default_migrator()` — returns a fresh
    `SchemaMigrator` with no built-in migrations. The
    library's schemas are stable at "1.0.0" as of
    v1.0.0-alpha.2; v1.1.0 will register the first
    concrete migration as part of the schema bump.
  - `migrate_bundle(bundle, *, target_version,
    migrator=None)` — the top-level convenience
    function.
  - `CURRENT_SCHEMA_VERSION` — the constant
    `"1.0.0"`, exposed for the product to reference.

### Behavioral notes

- **Forward-only.** A bundle at schema_version="1.0.0"
  can be migrated to "1.1.0"; the reverse raises
  `ValueError`. The library's stability promise is
  "forward-migrations ship with the change," not
  "downgrades are supported."
- **Idempotent.** Calling `migrate_bundle` twice on
  the same bundle with the same `target_version` is a
  no-op the second time. Records already at the
  target version are passed through unchanged.
- **Mixed-version bundles supported.** Each record is
  migrated independently from its current version to
  the target. The `MigrationResult` reports the
  per-record counts.
- **Dataclass input.** Dataclass records are converted
  to dicts via `dataclasses.asdict` before the
  migration is applied. The output is a dict; the
  product can re-hydrate to dataclasses if desired.
- **Fail-fast.** A migration that raises propagates the
  exception out of `migrate_bundle`; no partial
  results. The product is expected to wrap the call
  in a try/except.
- **No built-in migrations.** The framework ships
  empty. The first concrete migration will be
  registered as part of the v1.1.0 schema bump.

### Test discipline

- 38 new tests in `tests/test_migrations.py` covering:
  `MigrationStep` invariants, `MigrationResult`
  frozen-ness, `SchemaMigrator.register` (with
  double-registration detection), `find_path` BFS
  (direct, chained, no-path, empty versions),
  `has_path`, `apply_migrations` (direct chain,
  records-already-at-target, mixed-version bundle,
  missing schema_version, unknown version raises,
  migration raising propagates, empty bundle),
  `migrate_bundle` top-level integration, dataclass
  input conversion, `default_migrator()` empty
  registry, `CURRENT_SCHEMA_VERSION` constant, and
  public API exports.
- Total: 477 tests passing (was 439 in
  v1.0.0-alpha.1), 1 expected skip. `mypy --strict`
  clean on all 26 source files.

### Out of scope for v1.0.0-alpha.2

- No concrete schema migrations (the schemas are
  stable). v1.1.0 will add the first one.
- No auto-detection of source version. The caller
  passes `target_version`; the framework assumes
  the bundle is at the version inferred from its
  records.
- No validation of migrated records. A migration
  might produce an invalid record; the framework
  applies the migration; validation is the product's
  responsibility.
- No persistence of migration state. The product
  persists the migrated bundle.
- No CLI subcommand. Primitives are library-only.

## [1.0.0-alpha.1] - 2026-06-07

### Added

- New module `agent_memory_contracts.embedding` with the
  embedding input + text-rendering primitives:
  - `EmbeddingInput` (frozen dataclass) — the canonical
    input to an embedding pipeline. Fields: `record_id`,
    `record_type`, `text`, `privacy_class`,
    `content_hash_sha256`, `char_count`, `metadata`,
    `truncated`, `plane`. Construction validates the
    invariants.
  - `record_to_embedding_input(record, *, max_chars=8192)`
    — dispatches on record type and returns a frozen
    `EmbeddingInput`. Supports both dataclass and dict
    record forms.
  - `text_for_record_type(record)` — the public per-type
    renderer. 12 hand-crafted renderers (SourceRecord,
    EpisodeRecord, EvidenceSpan, CandidateClaim,
    CandidateDecision, CandidatePreference, CandidateTask,
    CandidateTasteSignal, FactLedgerEntry,
    DecisionLedgerEntry, PreferenceLedgerEntry, TasteCard,
    ContextPack) plus a generic "key: value" fallback.
  - `embedding_input_to_dict(ei)` and
    `embedding_input_from_dict(data)` — JSON-friendly
    round-trip helpers for the product to persist
    embedding inputs to disk between runs.
  - `DEFAULT_MAX_CHARS` — the default 8192-char limit on
    the rendered text. Generous for most embedding models,
    conservative for the long-input models.

### Behavioral notes

- **Determinism.** Two records with the same content
  produce the same `text` and the same
  `content_hash_sha256`. List-of-strings fields are sorted
  before joining; the generic renderer sorts fields by
  key. Same text -> SHA-256 hash -> dedup key.
- **Truncation.** Text exceeding `max_chars` is cut at
  the last sentence boundary within the last 200 chars of
  the cut point, with a hard cut as fallback. The
  `"...[truncated]"` marker is appended. The
  `truncated=True` field is set on the result.
- **Privacy surfacing.** The `privacy_class` field on
  `EmbeddingInput` defaults to `"internal"` for records
  that don't carry the field. The product uses
  `BundleScope` from v0.9.0 to decide which records to
  embed.
- **Metadata structure.** Flat `Mapping[str, str | int |
  float | bool]`. No nested dicts, no lists. Compatible
  with any vector DB filter API (Pinecone, Weaviate,
  Qdrant, FAISS, etc.).
- **Audit records embeddable.** `MemoryReducerDecision`,
  `ContextPackBuildReceipt`, etc. fall back to the
  generic renderer. The product decides whether to
  actually embed them (typically no).

### Test discipline

- 39 new tests in `tests/test_embedding.py` covering:
  frozen-dataclass surface and invariants, all 12
  per-type renderers, the generic fallback, determinism
  (dataclass and dict forms), truncation at the sentence
  boundary, privacy class surfacing, metadata structure,
  to_dict / from_dict round-trip including JSON-clean
  serialization, empty-record handling, and public API
  exports.
- Total: 439 tests passing (was 400 in v0.9.0), 1
  expected skip. `mypy --strict` clean on all 25 source
  files.

### Out of scope for v1.0.0-alpha.1

- No embedding model integration. The library stops at
  the input boundary.
- No vector storage or similarity search. The product
  chooses the vector store.
- No batched embedding API. Batched wrapping is a
  trivial utility; defer to v1.0.0-alpha.2 if needed.
- No CLI subcommand. Primitives are library-only.
- No schema changes. The 23 JSON Schemas stay
  unchanged.

## [0.9.0] - 2026-06-06

### Added

- New module `agent_memory_contracts.access` with the
  access control + bundle scope primitives:
  - `PRIVACY_CLASS_ORDER` (tuple of strings) — the
    canonical linear ordering of the 5 privacy classes
    from least to most restricted:
    `public < internal < private < sensitive < highly_sensitive`.
  - `BundleScope` (frozen dataclass) — a description of
    the "view" of a bundle. Three fields:
    `max_privacy_class`, `allowed_record_types`
    (optional), and `name` (human-readable label).
    Validates `max_privacy_class` at construction.
  - `AccessDecision` (frozen dataclass) — the per-record
    outcome of a scope check, with `record_id`, `action`
    (`"allow" | "redact" | "drop"`), and `reason`
    (human-readable English).
  - `AccessSummary` (frozen dataclass) — aggregate
    counts from a list of `AccessDecision`s: `total`,
    `allowed`, `redacted`, `dropped`,
    `by_privacy_class`, `by_action`.
  - `check_access(record, scope)` — per-record scope
    check. Returns an `AccessDecision`. Raises
    `ValueError` on an unknown `privacy_class` (a
    contract violation).
  - `scope_bundle(bundle, scope)` — whole-bundle
    filter. Returns a 2-tuple `(filtered_bundle,
    decisions_list)`. The filtered bundle preserves
    the input order; the decisions list is the
    per-record decisions in the same order.
  - `summarize_access(decisions)` — aggregate counts
    from a list of decisions into an `AccessSummary`.
  - Four scope factories:
    - `public_scope()` — allows only `public` records
    - `team_scope()` — allows up to `internal` (the default)
    - `customer_scope()` — allows up to `private`
    - `private_scope()` — allows all 5 classes
- New `examples/access.py` — worked example covering
  all 4 scope factories and a custom record-type
  filter.

### Behavioral notes

- **"Drop, never redact"** is the v0.9.0 default.
  Whole-record only: a record is allowed or dropped,
  not partially redacted. The `action="redact"` enum
  value is reserved in the public surface for a
  future sprint that adds field-level redaction.
- **Linear privacy-class ordering** is the v0.9.0
  shape. The 5 classes in `PRIVACY_CLASSES` are
  ordered strictly.
- A record without a `privacy_class` field is
  treated as `"internal"` (the library's working
  default for un-classified data).
- Both dataclass records and dict/Mapping records
  are accepted by `check_access` and `scope_bundle`
  via shape-based dispatch.

### Test discipline

- 32 new tests in `tests/test_access.py` covering:
  privacy class ordering, scope construction
  validation, the 4 scope factories, per-record
  access checks, dict-form records, whole-bundle
  filtering with order preservation, summary
  aggregation, and public API exports.
- Total: 400 tests passing (was 368 in v0.8.0),
  1 expected skip. `mypy --strict` clean on all
  24 source files.

### Out of scope for v0.9.0

- No field-level redaction. Whole-record only.
- No user/team/role model. `BundleScope` is a
  *classification* primitive, not a *principal*
  primitive. The product maps principals to
  scopes.
- No signed envelopes / encryption / decryption.
  Scope filtering is a data-classification layer.
- No audit log. `AccessDecision` is returned; the
  product persists it.
- No new privacy classes. The 5 in `PRIVACY_CLASSES`
  are the surface; adding a class is a schema change.
- No `access` CLI subcommand. Primitives are
  library-only.

## [0.7.0] - 2026-06-06

### Added

- New module `agent_memory_contracts.conflict` with the
  `ConflictResolution` schema and three primitives:
  - `resolve_conflict(conflict, chosen, *, resolved_by,
    rationale, resolved_at=None, metadata=None)` — build a
    `ConflictResolution` audit record for one surface-form
    conflict. Three policies: pick-one (int variant index),
    `"merge"` (synthetic merged record, last-write-wins per
    field by bundle index), `"split"` (no new record; both
    variants flagged with rationale).
  - `apply_resolutions(bundle, resolutions, *, now=None)` —
    apply a list of resolutions to a bundle, returning a new
    bundle. Per the user's "keep the variants" rule, the
    chosen record is **added** to the bundle (never in-place
    replacement); the rejected variants stay in the bundle
    with `superseded_by_conflict_resolution` and
    `superseded_by` fields updated. The audit chain is
    visible in the bundle itself.
  - `validate_resolutions(bundle, resolutions)` —
    non-raising validation, returns a list of error messages.
    Designed for product UIs that show validation issues inline.
  - Content-derived id: `confres_<sha256 hex>` (24 hex chars).
- New module `agent_memory_contracts.hygiene` with the
  `MemoryHygieneReport` schema and two primitives:
  - `compute_hygiene_report(bundle, *, window_start=None,
    window_end=None, now=None, conflicts=None)` — compute a
    snapshot of the bundle's health: per-plane / per-type /
    per-privacy counts, temporal state (active / stale /
    expired / superseded), evidence integrity (missing /
    orphan), and optional conflict counts. Returns a
    `MemoryHygieneReport`.
  - `hygiene_report_to_markdown(report)` — format the
    report as a Markdown document (pure function, no I/O).
  - Default window: the bundle's full time range (earliest /
    latest ISO timestamp found in the records, or `now` if no
    timestamps).
  - Content-derived id: `hygiene_<sha256 hex>` (24 hex chars).
- New example `examples/conflict_resolution.py` (~500 lines,
  runnable) with five worked scenarios: pick-one, merge,
  split, weekly hygiene report, windowed + diff-augmented
  hygiene report.
- New CLI subcommand `hygiene <path> [--from ISO] [--to ISO]
  [--json]`. Default output is the Markdown report; with
  `--json`, a structured JSON envelope suitable for
  programmatic consumption.
- 86 new tests across `tests/test_conflict.py` (39 tests),
  `tests/test_hygiene.py` (36 tests), and
  `tests/test_cli_hygiene.py` (11 tests). The full suite is
  325 passed + 1 expected skip on Python 3.10/3.11/3.12.

### Changed

- The CLI's top-level `--help` now lists five subcommands
  (`validate`, `fingerprint`, `diff`, `merge`, `hygiene`)
  instead of four.
- The CLI's top-level description and module docstring are
  updated to mention the `hygiene` subcommand.
- `pyproject.toml`: `version = "0.7.0"`.
- `__init__.py`: `__version__ = "0.7.0"`, and the new public
  API symbols are exported (see "Added" above).

### Design notes

- The user said "keep the variants, we'll compress them at
  some point." This drove the `apply_resolutions` design:
  the chosen record is added, the rejected variants stay
  in the bundle flagged. The audit chain is preserved in
  the bundle itself, not in a side-table. Compressing the
  rejected variants into a single audit record is
  deferred to a future release.
- The "merge" and "split" sentinel cases were designed as
  best-judgment choices (the user said "use best
  judgment, these are truly unknowns"). Future releases
  may tighten or relax these policies based on product
  feedback.

## [0.6.0] - 2026-06-06

### Added

- New example `examples/reference_reducer.py` (~1010 lines), a
  complete reference reducer with three worked scenarios:
  happy-path promotion, rejection of low-confidence / no-evidence
  / stale candidates, and a deliberate validator-enforcement
  case (a `MemoryReducerDecision` that doesn't authorize its
  claimed ledger entry is caught by `validate_ledger_bundle`).
  The reducer is reusable as a function:
  `reduce_candidates_to_trusted_memory(...)`. This is the
  canonical answer to "what does a contracts-library reducer
  look like in production?"
- New tests `tests/test_reference_reducer.py` (6 tests) covering
  the happy path, the three rejection cases, the partial
  promotion, the validator enforcement, and the script-running
  smoke test.
- New example `docs/migration_example.py` (~477 lines), a worked
  end-to-end migration from a synthetic 3-row SQLite store to a
  validated contracts JSONL fileset. Demonstrates content-derived
  deduplication (3 rows → 2 distinct ledger ids), synthetic
  reducer fabrication, pre-validation dedup, JSONL fileset
  output, and a stable bundle fingerprint.
- New doc `docs/migration.md` (~580 lines), the migration guide
  that walks a reader from SQLite-style memory to contracts-
  shaped records. Includes: who-this-is-for, a synthetic
  "before" stack with five failure modes, the "after" stack
  using the library, a side-by-side comparison of five common
  operations, three incremental adoption patterns (library
  alongside / library as the schema / library as the truth),
  what you give up, what you gain, and a reference to the
  worked example.
- New tests `tests/test_migration_example.py` (5 tests) that
  smoke-test the migration example: it runs end-to-end,
  collapses the duplicate preferences, writes the expected
  JSONL fileset, the ledger validates against the library's
  expected shape, and the fingerprint is stable across runs.
- New `benchmarks/` directory with a stdlib-only benchmark
  suite for `bundle_fingerprint`, `bundle_diff`, and
  `merge_bundles` at 100/1k/10k/50k records. Numbers confirm
  O(n) scaling (flat `per_record_us`):
  - fingerprint: 2.5us/record, 50k records in ~135ms
  - diff: ~10us/record, 50k records in ~550ms
  - merge: 8-16us/record, 10k records in ~85ms
  Includes a `run_all.py` driver and committed `RESULTS.md` for
  a baseline. No new dependencies.

### Changed

- **CI now runs `mypy --strict` on `src/agent_memory_contracts`.**
  The mypy job runs on the 3.10/3.11/3.12 matrix. 116 mypy errors
  were found and fixed in this release (no public-API change):
  `Protocol` for class-lookup tables, `TypeVar` on `_build_record`,
  `cast()` at post-narrow sites, explicit `dict[str, Any]`
  generics, and a real bug catch in `__main__.py` (the `errors`
  var was reassigned between `list[tuple]` and `list[str]` in
  the same scope — renamed to `instance_errors`).
- The existing `test` job now iterates `for f in examples/*.py`
  as a smoke test, so any new example added to the repo is
  automatically CI-checked.
- `pyproject.toml` now has a `[tool.mypy]` section pinning the
  configuration (`python_version = "3.10"`, `strict = true`,
  `files = ["src/agent_memory_contracts"]`).
- `mypy>=1.10` is in the `dev` optional-dependency (not runtime).
- 16 new tests (6 reference-reducer + 5 migration example + 5
  mypy-related harness). The full suite is 239 passed + 1
  expected skip on Python 3.10/3.11/3.12.

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
