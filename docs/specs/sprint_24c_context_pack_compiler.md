# Sprint 24c / v1.0.0-alpha.3 spec: ContextPack compiler

**Status:** awaiting your review (the same "go" pattern).
Defaults applied per the user's "best judgment" mandate.
Decisions captured inline at the bottom.

**Branching decision:** staying on `main`.

---

## Problem

The library has shipped the `ContextPack`,
`ContextPackBuildReceipt`, and `ContextPackValidationReport`
schemas since v0.4.0. The fields are stable, the JSON
Schemas are at "1.0.0", and the records are content-
derived. The library has the **integrity layer** (the
reducer, the citation graph, the access control, the
embedding input). What it does NOT have is a function
that takes a bundle of trusted memories and produces a
task-ready context pack.

That function is the **ContextPack compiler**, and it is
the bridge between "integrity" and "company brain." A
compiler accepts a bundle of records, a task description,
an optional access scope, and a compilation policy; it
returns a `ContextPack` with a `BuildReceipt` (what was
selected, what was excluded, why) and a `ValidationReport`
(what passed integrity checks). Hermes's competitive
analysis (June 2026) confirmed that neither Mem0 nor
LangGraph has a structured context assembly primitive —
Mem0 concatenates strings, LangGraph returns raw search
hits. The compiler is a first-in-market primitive.

The compiler's design constraints (set by the v1.0.0
integrity commitment):

- **Every claim in the pack is source-backed.** The
  compiler builds a `CitationGraph` and uses BFS to
  verify that every claim has a path to a
  `SourceRecord` (or `EpisodeRecord`). A claim with no
  source backing is excluded, and the exclusion is
  recorded in the `BuildReceipt.excluded` list.
- **Scope filtering is enforced.** The compiler applies
  the `BundleScope` (v0.9.0) before selecting records;
  records outside the scope are dropped, and the
  drop is recorded.
- **Schema integrity is verified.** The compiler
  validates every selected record against its JSON
  Schema (using the optional `jsonschema` validator if
  installed, else a stdlib structural check). A record
  that fails validation is excluded, and the failure
  is recorded in the `ValidationReport.errors`.
- **Truncation is bounded.** The compiler respects the
  policy's `max_records` cap. The selection algorithm
  scores records and picks the top N; the rest are
  excluded.
- **Determinism.** Two compilation runs with the same
  inputs produce the same output (same selected
  records, same BuildReceipt, same ValidationReport).
  The `pack_hash_sha256` is content-derived and matches
  the same hash on both runs.

What the compiler does **not** do:
- Call embedding models or compute vectors. That's the
  product's job, fed by the embedding input from v1.0.0a1.
- Store or index the resulting pack. The product
  persists the `ContextPack` (the JSON form is small,
  the embedded form is the product's concern).
- Decide which task to run. The product knows the
  task; the compiler takes a task and produces a pack.
- Optimize for retrieval (semantic similarity, BM25,
  etc.). The compiler is a structured-assembly
  primitive, not a retrieval primitive. The product
  can pre-filter the bundle by retrieval before
  passing it to the compiler.

---

## What's in this sprint

### New module: `src/agent_memory_contracts/compilation.py`

A single new module. The deliverable is three frozen
dataclasses and one free function.

#### `ContextPackTask` (frozen dataclass)

The task description the compiler is producing a pack
for. Mirrors the `task` field on the `ContextPack`
schema.

```python
@dataclass(frozen=True)
class ContextPackTask:
    task_id: str
    task_title: str
    task_type: str  # "question" | "task" | "decision" | "summary" | "code_change" | "other"
    task_summary: str
    project_id: str
    risk_class: str  # "low" | "medium" | "high" | "critical"
    sensitivity: str  # from PRIVACY_CLASS_ORDER
```

The library's existing `ContextPack.task` field is
expected to be a dict with these seven keys. The
`ContextPackTask` dataclass is the typed wrapper.

#### `CompilationPolicy` (frozen dataclass)

The compiler's configuration.

```python
@dataclass(frozen=True)
class CompilationPolicy:
    max_records: int = 50
    require_source_coverage: bool = True
    selection_strategy: Literal["recent", "diverse", "frequent"] = "recent"
    prefer_confidence: tuple[str, ...] = ("high", "medium", "low")
    exclude_stale: bool = True
    exclude_retracted: bool = True
    exclude_contested: bool = False
```

The `selection_strategy`:
- `"recent"`: order by `asserted_at` descending.
  Pick the most recent N records. Default.
- `"diverse"`: pick one record per `record_type`,
  rotating through the planes (evidence, candidate,
  ledger, taste, state, contextpack). Maximizes
  plane coverage.
- `"frequent"`: order by how many times the record
  has been cited (via the citation graph's incoming
  edge count). Pick the most-cited N records. Useful
  for "what does the system already know about this
  topic?" queries.

The `prefer_confidence` tuple: when sorting by recency,
records with a confidence at index 0 are preferred over
records with a confidence at index 1, etc. Records
without a confidence field are treated as "medium."

The exclude flags:
- `exclude_stale`: skip records with `status="stale"`.
- `exclude_retracted`: skip records with
  `status="retracted"`.
- `exclude_contested`: skip records with
  `status="contested"`. Default False (contested
  records are useful for "what's the disagreement
  here?" queries; the product can opt to exclude
  them).

#### `CompilationResult` (frozen dataclass)

The compiler's output.

```python
@dataclass(frozen=True)
class CompilationResult:
    context_pack: ContextPack
    build_receipt: ContextPackBuildReceipt
    validation_report: ContextPackValidationReport
    selected_record_ids: tuple[str, ...]
    excluded_record_ids: tuple[str, ...]
    selection_score_by_id: Mapping[str, float]  # the score each selected record got
```

`selection_score_by_id` is exposed so the product can
debug the selection (e.g., "why was this fact chosen
over that fact?").

#### `compile_context_pack(bundle, *, task, scope=None, policy=..., builder="agent-memory-contracts-v1.0.0") -> CompilationResult`

The headline function. Takes a bundle of records, a
task, an optional scope, and a policy. Returns a
`CompilationResult` with a `ContextPack`, a
`BuildReceipt`, a `ValidationReport`, and the per-record
selection diagnostics.

The algorithm:
1. **Filter by scope.** Apply `BundleScope` (v0.9.0) to
   the bundle. Records outside the scope are dropped
   and their ids are added to `excluded_record_ids`.
2. **Validate the bundle.** For each record, run the
   JSON Schema validator (if `jsonschema` is installed)
   or the stdlib structural check. Records that fail
   are added to `excluded_record_ids` and the failure
   is recorded in `ValidationReport.errors`.
3. **Filter by status.** Drop records whose status
   matches one of the policy's exclude flags
   (`stale`, `retracted`, `contested`).
4. **Filter by source coverage.** If
   `policy.require_source_coverage` is True, build a
   `CitationGraph` and drop claims that have no path
   to a `SourceRecord` (or `EpisodeRecord`).
5. **Score.** Apply the `selection_strategy` to
   produce a score for each remaining record.
6. **Select.** Pick the top `policy.max_records` by
   score. The rest are added to `excluded_record_ids`
   with reason "exceeded max_records".
7. **Build the `ContextPack`.** The selected records
   populate `trusted_memory` (for ledger entries),
   `evidence` (for evidence spans and source records),
   `candidate_context` (for candidate records), and
   `stale_or_superseded` (for records that were
   filtered out but are still relevant for context).
   The `task` field is the input `ContextPackTask`
   serialized. The `pack_hash_sha256` is the
   content-derived hash of the canonical pack.
8. **Build the `BuildReceipt`.** The `input_refs`
   list every record id in the input bundle. The
   `selection_policy` field is the policy's
   serialized form. The `excluded` field is a list
   of `{record_id, reason}` dicts for every excluded
   record.
9. **Build the `ValidationReport`.** The `checks`
   field is a dict of check-name → "pass"/"fail" for
   each integrity check (schema validation, source
   coverage, scope filtering, freshness filter). The
   `errors` field is a list of `{record_id, error}`
   dicts. The `status` field is `"pass"` if all
   checks pass, `"warn"` if some non-critical checks
   fail, `"fail"` if schema validation fails.
10. **Return the `CompilationResult`.**

---

## What's NOT in this sprint

- **No embedding model integration.** The compiler
  produces a `ContextPack`; the product embeds it
  (using `record_to_embedding_input` from v1.0.0a1 on
  the selected records, then aggregating).
- **No vector storage or similarity search.** The
  product stores the pack and its vector; the compiler
  is retrieval-agnostic.
- **No retrieval pre-filtering.** The compiler
  accepts a bundle and selects from it. The product
  can pre-filter by semantic similarity before
  calling the compiler; the compiler does not do
  retrieval itself.
- **No CLI subcommand.** Primitives are library-only.
- **No schema changes.** The `ContextPack`,
  `BuildReceipt`, and `ValidationReport` schemas stay
  unchanged.
- **No iterative refinement.** The compiler produces
  one `CompilationResult` per call. A future sprint
  could add `refine_context_pack` for follow-up
  refinement.

---

## Public API placement

```python
# src/agent_memory_contracts/__init__.py — added in v1.0.0-alpha.3
from .compilation import (
    CompilationPolicy,
    CompilationResult,
    ContextPackTask,
    compile_context_pack,
)
```

This is a public API commitment for the v1.0.0 line.

---

## Semantics

### Selection strategies

**`"recent"`** (default): sort by `asserted_at` (or
`valid_from` if `asserted_at` is missing, or `created_at`
if both are missing), descending. Pick the top N.

**`"diverse"`**: pick one record per `record_type`,
rotating through the planes. With `max_records=50` and
5 record types, this picks ~10 per type. The rotation
is by plane (evidence → candidate → ledger → taste →
state → evidence → ...). This strategy is useful when
the product wants a balanced view of the corpus
without biasing toward the most-recent plane.

**`"frequent"`**: sort by the count of incoming citation
graph edges (i.e., how many times the record has been
cited by other claims). Pick the top N. This strategy
is useful for "what does the system already know about
this topic?" queries — the most-cited records are the
ones the system has used most often.

### Source coverage

When `require_source_coverage=True` (default), the
compiler builds a `CitationGraph` from the bundle and
checks every claim's `is_supported()` property. Claims
with no path to a `SourceRecord` (or `EpisodeRecord`)
are excluded. The exclusion is recorded in the
`BuildReceipt.excluded` list with reason
`"no_source_backing"`.

This is the integrity principle: the compiler
guarantees that every claim in the pack is source-
backed. A `ContextPack` with an unsupported claim is
a bug, not a feature.

### Scope filtering

When `scope` is passed (a `BundleScope`), the compiler
applies it via `scope_bundle(bundle, scope)` (v0.9.0).
Records outside the scope are excluded. The exclusion
is recorded in the `BuildReceipt.excluded` list with
reason `"outside_scope"`.

When `scope` is `None` (default), no scope filtering is
applied. The product is responsible for pre-filtering
the bundle by scope if it wants to.

### Schema validation

The compiler runs the JSON Schema validator on every
selected record. The validator is the optional
`jsonschema` validator (per v0.2.0's `[jsonschema]`
extra). If `jsonschema` is not installed, the
compiler falls back to a stdlib structural check (id
field present, schema_version field present, no
unexpected fields, etc.).

Records that fail validation are excluded, and the
failure is recorded in `ValidationReport.errors`. The
`status` field on the `ValidationReport` is `"fail"`
if any selected record fails validation; `"warn"` if
only freshness checks fail; `"pass"` otherwise.

### Freshness filter

When `exclude_stale=True` (default), records with
`status="stale"` are excluded. When
`exclude_retracted=True` (default), records with
`status="retracted"` are excluded. When
`exclude_contested=False` (default), records with
`status="contested"` are included (they're useful for
"what's the disagreement?" queries).

The exclude flags respect the `LEDGER_STATUSES` set
on the library: `{"active", "stale", "superseded",
"retracted", "contested", "archived"}`.

### Determinism

The compiler is deterministic:
- Two calls with the same `(bundle, task, scope,
  policy)` produce the same `CompilationResult`.
- The `pack_hash_sha256` is the same on both calls.
- The `BuildReceipt.excluded` list is sorted by id
  for determinism.
- The `selected_record_ids` list is sorted by score
  (descending), with id as a tiebreaker (ascending).

The determinism property is what makes the compiler
testable: the test suite asserts specific
`selected_record_ids` and `excluded_record_ids`
for each fixture, and the assertions hold
deterministically.

### Idempotency

Calling `compile_context_pack` twice with the same
inputs produces the same `CompilationResult`. The
compiler is a pure function (no I/O, no randomness,
no state).

---

## Failure modes and edge cases

1. **Empty bundle.** The compiler returns a
   `CompilationResult` with an empty `ContextPack`
   (all list fields empty), an empty
   `selected_record_ids`, and a `BuildReceipt` that
   lists the empty input. The `ValidationReport.status`
   is `"pass"` (vacuously).
2. **`max_records=0`.** No records are selected; the
   result is empty. The product should pass `max_records >= 1`.
3. **All records excluded by scope.** The result is
   empty; the `BuildReceipt.excluded` list contains
   every input record id.
4. **All records excluded by source coverage.** Same
   as above; reason is `"no_source_backing"`.
5. **Bundle has duplicate ids.** The compiler
   de-duplicates by id (the first occurrence wins).
6. **Bundle has records with `schema_version` not
   equal to `"1.0.0"`.** The compiler logs a warning
   but does not exclude. The product is responsible
   for migrating the bundle (via `migrate_bundle` from
   v1.0.0a2) before passing it to the compiler.
7. **`task.sensitivity` is not a valid privacy
   class.** The compiler raises `ValueError` at
   construction. The product is responsible for
   passing a valid class.
8. **Dataclass input.** Records that arrive as
   dataclasses are converted to dicts via
   `dataclasses.asdict` for the schema validator and
   the `BuildReceipt.input_refs` field. The result's
   `ContextPack` is itself a dataclass; the
   `BuildReceipt` and `ValidationReport` are also
   dataclasses.
9. **Records without an `asserted_at` field.** The
   `"recent"` strategy falls back to `valid_from`,
   then `created_at`, then `observed_at`. Records
   without any timestamp are placed at the end of
   the sort (lowest priority).
10. **The citation graph has dangling refs.** The
    compiler does not exclude records on the basis of
    dangling refs. A claim whose evidence span is
    missing from the bundle is still selected; the
    source-coverage check (which uses the in-bundle
    graph) determines whether the claim is supported.

---

## Test plan

### Synthetic fixtures

1. **Trusted bundle with sources and evidence.** 3
   sources, 5 evidence spans, 10 ledger entries
   (mix of facts, preferences, decisions, taste
   cards). All supported by the citation graph.
2. **Bundle with unsupported claims.** Same as (1)
   but with 2 additional claims whose evidence
   spans are missing.
3. **Bundle with stale/retracted/contested
   records.** Same as (1) but with status flags
   that the policy's exclude flags would catch.
4. **Bundle with mixed privacy classes.** 1
   `public`, 2 `internal`, 1 `sensitive`, 1
   `highly_sensitive`. Apply `team_scope` (max =
   `internal`).
5. **Empty bundle.** Trivial case.
6. **Dataclass vs dict parity.** Same records as (1)
   but built via `dataclasses.asdict` and via
   `SourceRecord.from_dict(...)`. The compiler
   should produce the same `CompilationResult` for
   both (modulo id order).

### Test cases

- `compile_context_pack` with the trusted bundle
  selects the top N by recency.
- The same call produces the same `pack_hash_sha256`
  on two runs (determinism).
- The unsupported claims in fixture (2) are
  excluded with reason `"no_source_backing"`.
- The stale/retracted records in fixture (3) are
  excluded with the corresponding reasons.
- The `team_scope` in fixture (4) excludes the
  sensitive and highly_sensitive records.
- The empty bundle returns an empty result.
- Dataclass and dict inputs produce equivalent
  results.
- `__init__.py` exports all v1.0.0-alpha.3 names.
- `mypy --strict` clean on the new module.

Target: **25+ new tests** in
`tests/test_compilation.py`. Total target:
**502+ tests** (477 + ~25).

### `examples/compilation.py`

A worked example covering:
- Build a trusted bundle with 3 sources, 5 evidence
  spans, 10 ledger entries.
- Compile a `ContextPack` for a question task.
- Print the `selected_record_ids`, the
  `excluded_record_ids` (with reasons), and the
  `pack_hash_sha256`.
- Apply `team_scope` to filter the bundle before
  compilation; print the diff.
- Try the `"diverse"` strategy; print the per-type
  breakdown.

---

## Bottom line

The compiler is the bridge between integrity and
company brain. It is a thin layer on top of the
existing primitives: the citation graph (v0.8.0) for
source coverage, the access control (v0.9.0) for
scope filtering, the JSON Schema validator (v0.2.0)
for structural integrity, and the
`ContextPack`/`BuildReceipt`/`ValidationReport`
schemas (v0.4.0) for the output shape.

The deliverable is small: ~250 LOC of
`compilation.py`, ~50 LOC of test, ~30 LOC of
example. The value is high: the compiler is the
first structured context-assembly primitive in the
agent ecosystem (per Hermes's competitive analysis).

After this sprint, the library has:
- The integrity layer (citations, access, embedding,
  conflict, hygiene).
- The compiler (this sprint).
- The migration framework (v1.0.0a2).
- The end-to-end demo (v1.0.0-alpha.4).
- The stability commitment (v1.0.0 final).

That's v1.0.0.

---

## Decisions applied to this sprint

Applied 2026-06-07 per the user's "best judgment" mandate
and the post-24a re-pacing decision (commit `e99dc63`).

### 9 small decisions (all defaults)

1. **Module name:** `compilation.py`.
2. **Dataclass names:** `ContextPackTask`,
   `CompilationPolicy`, `CompilationResult`.
3. **Default `scope`:** `None` (no scope filtering;
   the product pre-filters the bundle if it wants
   scope enforcement).
4. **Default `max_records`:** 50 (reasonable for a
   context window).
5. **Default `selection_strategy`:** `"recent"`
   (most-recent-by-asserted_at).
6. **Default `require_source_coverage`:** `True`
   (the integrity principle).
7. **Default `prefer_confidence`:**
   `("high", "medium", "low")`.
8. **Default `exclude_stale`/`exclude_retracted`/
   `exclude_contested`:** `True, True, False`
   (contested records are useful for "what's the
   disagreement?" queries).
9. **No `compilation` CLI subcommand.** Primitives
   are library-only.

### 3 bigger-question decisions (all defaults)

- **Source coverage is enforced by default.** Every
  claim in the pack is required to have a path to
  a source. This is the integrity principle; a
  `ContextPack` with an unsupported claim is a
  bug, not a feature. The product can disable
  source coverage (`require_source_coverage=False`)
  for "exploratory" queries, but the default is
  strict.
- **`selection_strategy="recent"` is the default.**
  "Diverse" and "frequent" are useful but
  niche; "recent" is the most common case (a
  question task usually wants the most-recent
  context). The product can opt into "diverse"
  or "frequent" per task type.
- **The compiler is retrieval-agnostic.** It does
  not call embedding models or do semantic search.
  The product pre-filters the bundle by retrieval
  before calling the compiler; the compiler
  selects from the filtered set. This keeps the
  library retrieval-agnostic and lets the product
  choose its vector DB.

### Minor implementation choices

- **Source-coverage check uses the citation graph
  (v0.8.0).** A claim with `is_supported()=True`
  in the in-bundle graph is kept; otherwise
  excluded with reason `"no_source_backing"`.
- **Schema validation falls back to a stdlib
  structural check** when `jsonschema` is not
  installed. The check is: `id` field present
  (string, non-empty), `schema_version` field
  present (string, non-empty), no unexpected
  fields (the record's fields are a subset of the
  schema's `properties`).
- **Selection score for "recent" is a float
  (timestamp as seconds since epoch), descending.**
  Tiebreaker: id ascending.
- **The `BuildReceipt.excluded` field is a list of
  `{record_id, reason}` dicts,** sorted by
  `(reason, record_id)` for determinism.
- **The `ValidationReport.status` is `"pass"`,
  `"warn"`, or `"fail"`.** `"fail"` if any
  selected record fails schema validation.
  `"warn"` if freshness checks fail (e.g., the
  bundle has 5 stale records and the policy
  excludes them, but no schema violations).
  `"pass"` otherwise.
- **The compiler is a pure function.** No I/O,
  no randomness, no global state. Idempotent.
