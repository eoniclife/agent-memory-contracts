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
