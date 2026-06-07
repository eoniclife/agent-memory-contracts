# Sprint Decisions Log

**Purpose:** durable record of the design decisions taken on each
sprint, especially the cases where I went with my "best judgment"
instead of waiting for the user's explicit sign-off. Review this
file (or the matching "Decisions applied" section in each spec
doc) to audit what was built and why.

**Cadence:** appended after each sprint ships. Source of truth for
the spec is `docs/specs/sprint_NN_*.md`; this file is the index.

---

## Sprint 22 / v0.8.0 — citation graph + provenance traversal

**Decided:** 2026-06-06
**Mandate:** "go on the next 2 sprints with your best judgement,
note the decisions you've taken somewhere persistent" (per
2026-06-06 user message).
**Spec:** `docs/specs/sprint_22_citation_graph.md`
**Built at:** commit TBD (will be backfilled on release).

### 9 small questions (all defaults)

1. **Module name:** `citations.py`.
2. **Node-kind discriminator:** `node_kind`.
3. **Default traversal direction:** `"forward"`.
4. **`traverse` return shape:** `list[CitationPath]`.
5. **Default claim predicate:** any record with `evidence_span_ids`
   or `evidence_id` (includes candidates, ledger entries, taste
   cards, and context packs).
6. **Default source predicate:** `SourceRecord OR EpisodeRecord`.
7. **Dangling-ref shape:** tuple on graph + free function
   `find_dangling_refs(bundle)`.
8. **Export `DanglingRef` from `__init__.py`:** yes.
9. **CLI subcommand for citations:** deferred to v0.8.1.

### 3 bigger questions (all defaults)

- **"Claim = any record with `evidence_span_ids`"** — inclusive
  default kept. `ContextPack`s are first-class claims; products
  that want to exclude them pass a custom `claim_predicate`.
- **`find_unused_sources` includes `EpisodeRecord`s by default**
  — inclusive default kept. Unused episodes are a real cost;
  products can filter to `SourceRecord`-only with a custom
  `source_predicate`.
- **No cycle handling** — `from_bundle` raises `ValueError` on
  cycle. A silent elision would hide the bug that introduced the
  cycle; the library's role is to surface the problem.

### Minor implementation choices

- `DanglingRef` has no `id` field (transient analysis artifact,
  not a record type).
- `from_bundle` accepts both `Bundle` and `dict` (the typed
  `Bundle` is canonical; raw `dict` is supported for callers
  that haven't gone through the typed constructors).
- Traversal returns `list[CitationPath]`, not a generator —
  typical results are small and stable iteration order matters
  for product UX.

## Sprint 26 / v1.0.2 — MCP server

**Decided:** 2026-06-07
**Spec:** `docs/specs/sprint_26_mcp_server.md`

### 9 small defaults (all applied)

1. **Server name: `agent-memory-contracts`.** The MCP
   `Server.name` field; users see this in their client UI.
2. **Default transport: `stdio`.** The default MCP
   transport. HTTP/SSE is opt-in via
   `MCPConfig(transport="http", port=8765)`.
3. **Tool descriptions are short.** ~1-2 sentences. Long
   descriptions live in the docstring; the MCP descriptor
   uses a one-liner.
4. **Resource URIs use the `agent-memory-contracts://`
   scheme.** The `://` is the MCP convention for custom
   resource schemes.
5. **The server is stateless.** No caching, no per-session
   state. v1.1.0+ consideration is a stateful mode.
6. **Tool input validation uses the library's own
   validators.** The server does not duplicate
   validation logic; it delegates to the library.
7. **Tool errors return JSON-RPC error responses.** The
   server does not silently swallow exceptions.
8. **The server does not log to stdout by default.**
   stdout is the JSON-RPC channel; logging goes to
   stderr.
9. **Schema resources are read-only.** The server exposes
   the JSON Schemas as resources but does not allow
   modification. Schemas are content-addressed in the
   library; modification is a v1.1.0+ concern.

### 3 bigger defaults

10. **The integration uses `fastmcp`, not the raw MCP
    SDK.** FastMCP is the de facto framework for building
    MCP servers in Python; it's by the same team (Prefect)
    and the official Anthropic SDK uses it under the hood
    for many examples. Using FastMCP gives a ~250-LOC
    server instead of a 2000-LOC one.

11. **The server exposes 3 tools, not the full library
    surface.** 3 tools = `validate_bundle`,
    `compile_context`, `check_access`. Other functions
    (fingerprint, diff, merge, hygiene) are client-side
    operations; they don't need an MCP round-trip. A
    user with full bundle access can run them locally.

12. **The server does not implement a "store" tool.**
    MCP has a `Store` resource type for read/write
    persistent state. The library is not a store; the
    server is stateless. A store-based adapter is a
    v1.1.0+ consideration (and would couple to
    LangGraph's `Store` API).

---

## Sprint 25 / v1.0.1 — LangChain memory backend

**Decided:** 2026-06-07
**Spec:** `docs/specs/sprint_25_langchain_memory.md`

### 9 small defaults (all applied)

1. **Default privacy class: `internal`.** Most LangChain
   chains are for internal tooling. `customer` and `private`
   are opt-in via `ContractsMemoryConfig(privacy_class="private")`.
2. **Default `max_bundles: 100`.** Soft cap with FIFO
   eviction; matches the v0.7.0 hygiene cap convention.
3. **Default `max_records_per_load: 20`.** Most session
   memory is short; this prevents the LLM prompt from
   growing unbounded.
4. **Default `exclude_stale` / `exclude_retracted` /
   `exclude_contested: True`.** Matches the v0.8.0+
   compiler defaults.
5. **`save_context` records an EpisodeRecord, NOT a
   FactLedgerEntry.** A conversation turn is a sequence of
   episodes, not a structured fact. The full reducer/ledger
   pipeline is intentionally not engaged.
6. **`load_memory_variables` returns a session-shaped
   context_pack, NOT a compiled ContextPack.** The compiler
   requires a state record (a long-term ledger shape),
   which is the wrong fit for a session memory. The
   integration shapes the bundle directly.
7. **Source is a single `conversation` SourceRecord per
   session.** Not one per turn. The session is the unit of
   memory; the turn is a sub-episode.
8. **In-memory store only; no file/DB backend.** v1.1.0+
   consideration. The `MemoryStore` API is shaped to make
   this a drop-in replacement later.
9. **No LLM calls in `save_context`.** The integration does
   not introduce LLM calls. v1.1.0+ consideration is
   LLM-based extraction (preference detection, etc.).

### 3 bigger defaults

10. **The integration is `langchain-classic`, not
    `langchain-core`.** `BaseMemory` lives in
    `langchain_classic.base_memory` in modern LangChain
    (0.3+). The `[langchain]` extra installs
    `langchain-classic>=1.0`. We do not depend on the
    legacy `langchain<0.1` package.

11. **The integration does not implement `BaseChatMemory`.**
    `BaseChatMemory` adds message-list semantics; the
    library is ledger-shaped, not chat-shaped. A
    `BaseChatMemory` adapter would distort the library's
    API. Out of scope for v1.0.1.

12. **`BaseMemory` is deprecated in modern LangChain (since
    0.3.3, removal in 2.0.0).** The recommended replacement
    is `langchain.agents.create_agent` with checkpointing
    or the `Store` API. The integration is still useful
    today and works for users on the classic memory
    pattern. A v1.x+ consideration is a `Store`-based
    adapter for the modern LangGraph path. We ship the
    integration anyway because the proof-of-value is
    strong, and the alternative is to ship nothing.

---

## Sprint 22 / v0.9.0 — re-pacing decision (2026-06-07)

**Decided:** 2026-06-07
**Source:** User shared a Hermes analysis of the library
vs. Mem0 / LangGraph. The analysis identified the
`ContextPack` compiler as a real gap and recommended
prioritizing it over more library modules.
**Stance:** proceeded with 24b (schema migration) per
the user's stated direction; re-paced the post-24b
roadmap to put the `ContextPack` compiler next.

### Decisions applied

- **24b (schema migration) proceeds as planned.** The
  library's v1.0.0 stability promise needs the migration
  framework to be real. Hermes's argument that "schemas
  are stable and treated as 1.0.0" is true *today* but
  doesn't stay true on its own; the migration framework
  is the safety net for any future schema bump.
- **Post-24b roadmap is re-shuffled** (was: 24b →
  v1.0.0 final → v1.1.0 decay → ...; now: 24b →
  ContextPack compiler → end-to-end demo → v1.0.0 final →
  LangChain backend → MCP server → v1.1.0 decay). The
  ContextPack compiler jumps from "later" to "next",
  because it's the bridge between integrity (what we
  have) and company brain (what we're building toward),
  and Hermes's competitive analysis confirmed neither
  Mem0 nor LangGraph has it.
- **"Stop adding new library modules" is adopted as a
  discipline going forward.** 25 modules + 23 schemas +
  439 tests is enough for a v1.0.0 commitment. New work
  from here should be (a) v1.0.0 meta-sprints, (b)
  compiler/orchestration, or (c) integrations.
- **Sprint specs are kept.** Hermes initially called
  them "process theater" and retracted it on second
  look. The spec doc demonstrates structured product
  thinking and is the audit trail for "why was this
  built this way" review.

### Where Hermes was right

- The `ContextPack` schema exists but no function compiles
  a trusted bundle + a task + a scope into a `ContextPack`.
  That's the missing primitive for the company-brain story.
- The library is over-engineered if we keep adding
  modules; the discipline going forward is to ship the
  v1.0.0 commitment and pivot to integration.
- The integration story (LangChain, MCP) is more
  important than another library module.

### Where Hermes was wrong

- The library is not "overbuilt" *as a v1.0.0 artifact*.
  The citation graph, access control, embedding input,
  conflict resolution are each first-in-market primitives.
  Hermes retracted the "over-engineered" claim when
  comparing to Mem0/LangGraph.
- Schema migration is not "process theater" — it's the
  v1.0.0 commitment. Without it, the stability promise is
  fake.
- "Get 10 users" is good advice but not a sprint scope.
  The user research can run in parallel with the v1.0.0
  work.

### Re-paced roadmap (2026-06-07)

- **24b / v1.0.0-alpha.2** — schema migration framework
  (in flight)
- **24c / v1.0.0-alpha.3** — ContextPack compiler
  (jumped from "v1.1.0 decay"); the bridge between
  integrity and company brain
- **24d / v1.0.0-alpha.4** — end-to-end demo (one script,
  full pipeline)
- **v1.0.0 final** — stability commitment (SemVer policy
  doc, public API freeze, CHANGELOG discipline); depends
  on 24c + 24d
- **v1.0.1** — LangChain memory backend
- **v1.0.2** — MCP server
- **v1.1.0** — decay primitives (was Sprint 25; bumped
  to make room)
- **v1.2.0** — stream-friendly diff/merge + storage
  adapters (unchanged from prior roadmap)

---

## Sprint 21 / v0.7.0 — conflict resolution + memory hygiene

**Decided:** 2026-06-06
**Mandate:** "go build it" (per 2026-06-06 user message after
spec review).
**Spec:** `docs/specs/sprint_21_conflict_resolution.md`
**Built at:** commits b375a8d (primitives) + 01bae82
(tests+example) + b70fe88 (CLI) + c689f8e (release).

### Decisions applied (carry-over from the spec-time Q&A)

- **"Keep the variants" on `apply_resolutions`** — chosen record
  is *added* (never in-place replaced); rejected variants stay
  in the bundle with `superseded_by_conflict_resolution` +
  `superseded_by` set. Audit chain visible in the bundle itself.
  Compression of rejected variants deferred.
- **Synthetic merged record shape for `"merge"` policy** — used
  last-write-wins per field across the variants. Caller can
  always construct an explicit merged record and pass it as the
  `chosen_record` if they want stricter semantics.
- **`MemoryHygieneReport` shape** — 13 fields (plane counts,
  type counts, privacy histogram, temporal state, evidence
  integrity, supersession metrics, etc.). If the product's UX
  needs different cuts, they'd be added in a later sprint.

### Minor implementation choices

- **Id prefix for `ConflictResolution`:** `confres_` (24 hex
  chars, content-derived).
- **Id prefix for `MemoryHygieneReport`:** `hygiene_` (24 hex
  chars, content-derived).
- **Minimum `rationale` length:** 10 characters (filter out "ok"
  / "fix" / single-word rationales).
- **`apply_resolutions` returns a new bundle** (no mutation);
  callers can rebind to the new list.
- **CLI `--json` mode for `hygiene`:** the raw `MemoryHygieneReport`
  dict, not a wrapper object. Matches the other subcommands' shapes.

---

## Sprint 20 / v0.6.0 — reference reducer + mypy strict + benchmarks

**Built at:** commits shipped as v0.6.0 (no separate spec doc;
the v0.6.0 work was the "A- to A" polish sprint and used a
4-track team plan).

### Decisions applied

- **`ReferenceReducer` scope:** a single-pass reducer that
  resolves `reference_text` placeholders across ledger entries
  and taste cards to canonical record ids. One primitive, not a
  framework.
- **Mypy strict mode in CI:** added a separate `mypy` job
  (matrix 3.10/3.11/3.12) that runs `mypy --strict` on
  `src/agent_memory_contracts` only; tests and examples are
  excluded.
- **Benchmark suite:** stdlib-only (`time.perf_counter`), no
  pytest-benchmark dependency. Lives in `benchmarks/`.
- **Migration guide:** `docs/migration.md` +
  `docs/migration_example.py`. Step-by-step, runnable end-to-end.

---

## Sprint 19 / v0.5.0 — bundle merge + global --json mode

**Built at:** commits shipped as v0.5.0.

### Decisions applied

- **`merge_bundles(*bundles, prefer=...)` with three policies:**
  `first` (default), `last`, `explicit` (per-id override dict).
- **Conflict surfacing on the result:** the merged bundle has a
  `conflicts` field listing the records that differed across
  inputs. Does NOT auto-resolve — that's v0.7.0's job.
- **Global `--json` mode for the CLI:** parser-level flag that
  applies to any subcommand. Emits one JSON object to stdout;
  errors go to stderr as a separate JSON object.

---

## Sprint 18 / v0.4.0 — bundle diff + validate_jsonl + CLI

**Built at:** commits shipped as v0.4.0.

### Decisions applied

- **`bundle_diff(a, b)` is set-semantic, not order-sensitive.**
  Records compared by `id` (content-derived SHA-256), not by
  position.
- **`validate_jsonl(file)` for streaming large corpora:** doesn't
  load the whole file into memory; one record at a time, raises
  on first invalid line.
- **CLI as `python -m agent_memory_contracts`** (not a separate
  package): thin wrapper around the public API; all real work
  stays in the library modules.

---

## Sprint 17 / v0.3.0 — bundle fingerprint

**Built at:** commits shipped as v0.3.0.

### Decisions applied

- **`bundle_fingerprint(bundle)` is a SHA-256 over the sorted
  canonical-JSON serialization.** Order of records in the bundle
  does not affect the fingerprint. This is the property that
  makes fingerprints comparable across writes.

---

## Sprint 16 / v0.2.0 — JSON Schema validator + validate_jsonl

**Built at:** commits shipped as v0.2.0.

### Decisions applied

- **`jsonschema_validator` is in `[project.optional-dependencies]`.**
  The library's core validation (structural shape, id format,
  type checks) is stdlib-only; the JSON Schema validator is the
  optional strict-mode validator.

---

## Sprint 15 / v0.1.0 — initial extraction

**Built at:** the original public release.

### Decisions applied (the foundational contract surface)

- **Content-derived SHA-256 IDs** for every record type (every
  `id` field is the SHA-256 of the canonical-JSON
  serialization, not an assigned id).
- **6 record planes** (evidence, candidate, ledger, taste,
  state, ContextPack) with cross-plane references by id.
- **23 JSON Schemas** under `src/agent_memory_contracts/schemas/`,
  versioned as `1.0.0` and treated as stable.
- **Stdlib-only at runtime.** All optional dependencies in
  `[project.optional-dependencies]`.
- **Apache-2.0 license.**
