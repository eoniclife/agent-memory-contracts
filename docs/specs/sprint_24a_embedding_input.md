# Sprint 24a / v1.0.0-alpha.1 spec: embedding input

**Status:** awaiting your review. I'll start implementation after
you sign off (per the established "go" pattern).

**Branching decision:** staying on `main`.

---

## Problem

The library ships the **provenance** layer (citation graph,
access control, conflict resolution) but not the **retrieval**
layer. A company-brain product cannot answer "find me the
three most semantically similar facts to this question"
without building its own ad-hoc text-extraction layer on top
of every record type.

The natural shape of that layer is:

> Given a record, produce a canonical, deterministic, privacy-
> aware text representation that any text-embedding model
> (OpenAI, Cohere, sentence-transformers, etc.) can consume.

What the library does **not** do:
- Call embedding models. That's a product concern.
- Store or index vectors. That's a product concern (vector
  DB, similarity search, etc.).
- Decide which records to embed. That's a product concern
  (typically via `BundleScope` from v0.9.0).

What the library **does** do:
- Define a frozen `EmbeddingInput` dataclass that captures
  everything an embedding pipeline needs about a record.
- Provide a single function `record_to_embedding_input(record)`
  that handles every record type the library ships.
- Make the text rendering **deterministic** (same record
  content → same text → same SHA-256) so deduplication is
  trivial.
- Surface the privacy class so the product can apply
  `BundleScope` first, before deciding what to embed.
- Support a configurable `max_chars` truncation so embedding
  pipelines that have token limits don't blow up.
- Be library-style: stdlib only, frozen dataclass, mypy
  --strict clean.

This sprint ships the canonical "input boundary" between
the library and the product's embedding pipeline.

---

## What's in this sprint

### New module: `src/agent_memory_contracts/embedding.py`

A single new module. The deliverable is one frozen dataclass
and a few free functions.

#### `EmbeddingInput` (frozen dataclass)

The canonical input to an embedding model.

```python
@dataclass(frozen=True)
class EmbeddingInput:
    record_id: str                # source record's id (sha256_<hex>)
    record_type: str              # "source_record", "fact_ledger_entry", etc.
    text: str                     # the canonical natural-language rendering
    privacy_class: str            # from PRIVACY_CLASS_ORDER
    content_hash_sha256: str      # SHA-256 of canonical text (deduplication)
    char_count: int               # len(text) at construction time
    metadata: Mapping[str, str | int | float | bool] = field(...)
    truncated: bool = False       # True iff text was clipped to max_chars
```

The `text` field is the headline. It is a deterministic,
natural-language rendering of the record, suitable for any
text-embedding model. The product feeds `text` (and
optionally the structured `metadata` for vector-store
filtering) to its embedding model of choice.

The `content_hash_sha256` is the deduplication key. Two
records with the same content (same source, same span, same
text) produce the same `text` and the same
`content_hash_sha256`. The product can dedupe at the embedding
input layer without re-embedding.

The `metadata` field is structured (not text) so the product
can store it alongside the vector in any vector DB that
supports metadata filtering (Pinecone, Weaviate, Qdrant, etc.).
The default `metadata` includes:
- `record_id`
- `record_type`
- `privacy_class`
- `plane` (which of the 6 memory planes this record belongs to)

Per-type renderers may add more fields (e.g., for a
`SourceRecord`, `source_type`; for a `FactLedgerEntry`,
`subject` / `predicate` / `object`).

#### `record_to_embedding_input(record, *, max_chars=8192) -> EmbeddingInput`

The headline function. Renders any record (dataclass, dict,
or Mapping) as an `EmbeddingInput`.

Args:
- `record`: a record (dataclass, dict, or Mapping).
- `max_chars`: maximum text length. If the rendered text
  exceeds this, it is truncated deterministically (cut at
  the nearest sentence boundary, falling back to a hard cut
  at `max_chars` if no sentence boundary is found within
  the last 200 chars). Default 8192 — generous for most
  embedding models, conservative for the long-input models.

Returns:
- A frozen `EmbeddingInput`.

Raises:
- `ValueError` if the record cannot be rendered (no
  recognizable record type and no text-equivalent fields).
  In v1.0.0-alpha.1, the only records that raise are those
  with neither a `text` field nor a known record type. The
  default behavior for unknown types is to fall back to a
  generic "key: value" dump of the record's fields, sorted
  by key, with the first 50 fields. A record that is empty
  after this fallback raises.

#### `text_for_record_type(record) -> str`

The per-type renderer. Returns the canonical text for a
record. Public so the product can pre-render text without
constructing an `EmbeddingInput` (useful for batched
embedding pipelines that want to stream the text).

Per-type renderers (one per record type, hand-crafted):

- **`SourceRecord`**: title, author_or_sender, source_type,
  origin_uri or raw_ref.value, captured_at, key metadata
  fields.
- **`EvidenceSpan`**: span id, source id, locator kind +
  value, text_excerpt (or fallback if excerpt_policy forbids
  quoting), privacy class.
- **`EpisodeRecord`**: title, episode_type, source_id,
  summary, actors, topics, time range, evidence span ids.
- **`CandidateClaim`**: claim_text, subject/predicate/object,
  claim_scope, confidence, evidence span ids.
- **`CandidateDecision`**: decision_text, decision_scope,
  owner_hint, rationale_text, alternatives_mentioned,
  reversibility, evidence span ids.
- **`CandidatePreference`**: preference_text, subject,
  domain, scope, strength_hint, evidence span ids.
- **`CandidateTask`**: task_text, task_kind, project_refs,
  owner_hint, urgency_hint, safety_lane, evidence span ids.
- **`CandidateTasteSignal`**: taste_text, domain,
  signal_kind, example_span_ids, contrast_span_ids,
  strength_hint.
- **`FactLedgerEntry`**: fact_text, subject/predicate/object,
  scope, confidence, valid_from, evidence span ids.
- **`DecisionLedgerEntry`**: decision_text, decision_scope,
  owner, alternatives_considered, rationale_text,
  reversibility, evidence span ids.
- **`PreferenceLedgerEntry`**: preference_text, subject,
  domain, scope, strength_hint, evidence span ids.
- **`TasteCard`**: taste_text, subject, taste_type, domain,
  applicability, evidence span ids.
- **`ContextPack`**: context_pack_kind (or "general"),
  primary_evidence_span_ids, evidence_span_ids, topics,
  project_refs, natural_language_summary.
- **`MemoryReducerDecision`** and other audit records:
  fall back to the generic renderer.

The generic renderer (used for unknown types and audit
records): walks the record's fields, sorts by key, and emits
"key: value" lines, capped at 50 lines.

#### `embedding_input_to_dict(input) -> dict` and `embedding_input_from_dict(data) -> EmbeddingInput`

Round-trip helpers. `to_dict` returns a JSON-serializable
dict. `from_dict` reconstructs an `EmbeddingInput` (used by
the product to persist embedding inputs to disk between
runs).

---

## What's NOT in this sprint

- **No embedding model integration.** The library does not
  call OpenAI, Cohere, sentence-transformers, or any other
  model. The product chooses the model and passes the
  `EmbeddingInput.text` to it.
- **No vector storage / similarity search.** The library
  does not provide a vector store or a similarity-search
  function. The product chooses the store (Pinecone,
  Weaviate, Qdrant, FAISS, etc.) and stores the
  embedding-result-vector alongside the `EmbeddingInput`'s
  metadata.
- **No batched embedding API.** A batched function
  (`records_to_embedding_inputs(*records) -> list`) is a
  trivial wrapper, but the spec defers it. The product can
  trivially batch in its own code.
- **No CLI subcommand.** Primitives are library-only.
- **No changes to the 23 JSON Schemas.** The
  `EmbeddingInput` is a transient analysis artifact, not a
  record type. It is not persisted in bundles.

---

## Public API placement

```python
# src/agent_memory_contracts/__init__.py — added in v1.0.0-alpha.1
from .embedding import (
    EmbeddingInput,
    record_to_embedding_input,
    text_for_record_type,
    embedding_input_to_dict,
    embedding_input_from_dict,
)
```

This is a public API commitment for the v1.0.0 line.
Renaming any of these names after v1.0.0 is a breaking
change.

---

## Semantics

### Determinism

Two records with the same content produce the same
`EmbeddingInput`:

```python
# Same record content, two different records (one dataclass, one dict):
src_dc = SourceRecord.from_dict({...})
src_dict = src_dc.to_dict()  # equivalent dict
e1 = record_to_embedding_input(src_dc)
e2 = record_to_embedding_input(src_dict)
assert e1.text == e2.text
assert e1.content_hash_sha256 == e2.content_hash_sha256
```

The text rendering is order-stable (fields are emitted in
the same order every time; lists of strings are sorted
before joining; nested dicts are sorted by key).

### Truncation

If the rendered text exceeds `max_chars`:

1. Find the last sentence boundary (`.`, `!`, `?`, or `\n`)
   within the last 200 chars of the truncation point.
2. If found, cut at that boundary and append a `...[truncated]`
   marker.
3. If not found, hard cut at `max_chars` and append the
   marker.

`truncated=True` is set on the `EmbeddingInput`. The product
can re-render with a larger `max_chars` for the same record
to get the full text.

### Privacy surfacing

`EmbeddingInput.privacy_class` is the record's privacy class
(or "internal" if missing). The product's embedding pipeline
uses this to decide whether to embed:

```python
scope = team_scope()  # max_privacy_class = "internal"
filtered, decisions = scope_bundle(bundle, scope)
for record in filtered:
    if check_access(record, scope).action != "allow":
        continue
    embedding_input = record_to_embedding_input(record)
    # Safe to embed: the record is allowed at the team scope.
    vector = product.embed(embedding_input.text)
    product.vector_store.upsert(embedding_input, vector)
```

The library does not enforce this — the product is
responsible for the gate. The library just surfaces the
class so the product can decide.

### Metadata structure

The default `metadata` field on `EmbeddingInput` is:

```python
{
    "record_id": str,           # source record's id
    "record_type": str,         # "source_record", "fact_ledger_entry", etc.
    "privacy_class": str,       # "public" / "internal" / etc.
    "plane": str,               # "evidence" / "candidate" / "ledger" / "taste" / "state" / "contextpack"
    # Per-type extras (only present when the type is recognized):
    # SourceRecord: "source_type": str
    # Claim-like records: "subject": str, "predicate": str, "object": str
    # Ledger entries: "scope": str, "confidence": str
    # ContextPack: "context_pack_kind": str
}
```

Values are restricted to `str | int | float | bool` (no
nested dicts, no lists) so the metadata can be passed
verbatim to any vector DB's filter API.

---

## Failure modes and edge cases

1. **Empty record.** Raises `ValueError` from
   `record_to_embedding_input`. The product should
   pre-filter empty records.
2. **Record with no recognizable type.** Falls back to the
   generic "key: value" renderer. If even that produces
   empty text, raises `ValueError`.
3. **Dict vs dataclass.** Both accepted; the type dispatch
   is shape-based, not class-name-based.
4. **Records with the `evidence_id` (singular) field.**
   Treated the same as `evidence_span_ids` (plural) in
   the renderer.
5. **Truncation to a very small `max_chars`** (e.g., 50).
   The sentence-boundary search is bounded by the last 200
   chars; with very small `max_chars`, the hard-cut path
   always fires. `truncated=True` is set; the marker is
   still appended.
6. **Records with non-ASCII text.** Treated as opaque
   strings; no encoding normalization. Embedding models
   handle Unicode natively; the library does not
   transliterate.
7. **Records with very long lists** (e.g., 1000 evidence
   span ids). Lists are sorted then joined; no truncation
   at the list level. If the joined list is the bulk of the
   text and exceeds `max_chars`, the truncation logic
   applies.
8. **`MemoryReducerDecision` and other audit records.**
   Fall back to the generic renderer. The product can
   decide to skip them (reducer decisions are not
   "facts" to embed).
9. **Records with `metadata` containing non-primitive
   values.** The metadata walker coerces values to
   `str | int | float | bool`; nested dicts become JSON
   strings; lists become comma-joined strings.

---

## Test plan

### Synthetic fixtures

1. **One record per type.** One record from each of the
   13+ record types above. Used for the cross-type
   coverage test.
2. **Dict-form records.** Mirror the dataclass records as
   dicts. Used for the dict-vs-dataclass parity test.
3. **Long-text record.** A record with text exceeding
   `max_chars`. Used for the truncation test.
4. **Empty record.** A record with no fields. Used for the
   empty-record test.
5. **Custom-type record.** A dict that doesn't match any
   known record type. Used for the generic-renderer test.

### Test cases

- `record_to_embedding_input(record)` returns the expected
  `EmbeddingInput` for each fixture.
- The `text` field for two equivalent records (dataclass +
  dict) is identical.
- The `content_hash_sha256` for two equivalent records is
  identical.
- Truncation: long text is cut at the sentence boundary
  and marked `truncated=True`.
- Privacy class is surfaced correctly.
- Metadata has the expected fields per record type.
- The generic renderer produces deterministic text for an
  unknown record type.
- `embedding_input_to_dict` and `embedding_input_from_dict`
  round-trip cleanly.
- `__init__.py` exports all v1.0.0-alpha.1 names.
- `mypy --strict` clean on the new module.

Target: **35+ new tests** in `tests/test_embedding.py`.
Total target: **435+ tests** (400 + ~35).

### `examples/embedding.py`

A worked example covering:

- Build a small bundle with one record per type.
- Render each as an `EmbeddingInput`.
- Print the text rendering for each.
- Demonstrate privacy class surfacing.
- Demonstrate the round-trip `to_dict` / `from_dict`.
- (Hypothetical) show the shape of the embedding pipeline
  that the product would build on top: feed `text` to a
  model, store the vector alongside the metadata.

---

## Bottom line

The embedding input primitive is a thin text-rendering
layer on top of the existing record types. It does not
need new schemas, new ids, or a vector database. It needs
~13 hand-crafted per-type renderers, ~50 LOC of generic
fallback, and ~35 tests.

The deliverable is the canonical "input boundary" between
the library and the product's embedding pipeline. After
this sprint, the product can do:

```python
for record in bundle:
    if check_access(record, scope).action == "allow":
        ei = record_to_embedding_input(record)
        vector = my_embedder(ei.text)
        my_vector_store.upsert(ei.content_hash_sha256, vector, ei.metadata)
```

That's the whole v1.0.0-alpha.1 story.

---

## Open questions for you

1. **Module name:** `embedding.py` (default) vs `embeddings.py`
   vs `vectors.py`. Default: `embedding.py` (matches the
   public API name `EmbeddingInput`).
2. **Dataclass name:** `EmbeddingInput` (default) vs
   `EmbeddingRecord` vs `EmbeddingPayload`. Default:
   `EmbeddingInput` (signals "this is the input to an
   embedding pipeline, not a stored record").
3. **Per-type renderers public or private?** Public
   (default — `text_for_record_type(record)` is exported
   so the product can stream text without constructing an
   `EmbeddingInput`) vs private (only the headline function
   is exported). Default: public.
4. **Text format per type:** structured-template (default)
   vs JSON dump vs custom. Default: structured-template
   (most natural-language-friendly; embedding models
   trained on natural text work better with natural text).
5. **Truncation strategy:** by chars with sentence-boundary
   preference (default) vs by tokens (no tokenizer
   available) vs hard cut. Default: by chars with
   sentence-boundary preference.
6. **Content hash:** SHA-256 of canonical text (default)
   — already used everywhere. Same shape as the record
   ids, but distinct (the hash is of the text, not of the
   record).
7. **Privacy class as a field:** yes (default). The product
   uses it to decide whether to embed.
8. **Metadata field structure:** flat key-value with
   primitive values only (default). No nested dicts, no
   lists. Compatible with any vector DB's filter API.
9. **No CLI subcommand** (default; primitives are
   library-only). Defer to v1.0.1.

If you have no overrides, I'll go with all defaults.

---

## What I'd want feedback on

The 9 open questions above are the small ones. The bigger
questions, where I'd most value your pushback:

- **Is the structured-template text the right shape?** A
  per-type renderer that emits "Title: ...\nAuthor: ...\n
  Type: ..." is natural-language-friendly but is
  *engineered* text, not raw content. An alternative is to
  emit the most-natural-text field first (e.g., for a
  `FactLedgerEntry`, the `fact_text` field IS the natural
  text) and put structured metadata on subsequent lines.
  My read is "the structured-template is the right default;
  products that need raw content can pre-process." If you
  disagree and want raw content first, let's talk.
- **Should `MemoryReducerDecision` and other audit records
  be embeddable?** They are audit records, not facts. The
  product typically skips them. My read is "the generic
  renderer makes them embeddable; the product decides
  whether to actually embed them." If you want the library
  to raise on audit records, override here.
- **Should `text_for_record_type` be public?** It's a
  per-type renderer; making it public means the product
  can call it directly without going through
  `record_to_embedding_input`. My read is "yes, public —
  batched pipelines want to stream the text." If you
  prefer the API surface to be just the headline function,
  override question 3.

---

## Implementation order

After sign-off, the work happens in ~6 commits on `main`:

1. `docs(specs): sprint 24a / v1.0.0-alpha.1 — embedding input`
   (this doc, on `docs/specs/sprint_24a_embedding_input.md`).
2. `feat: add EmbeddingInput frozen dataclass and
   embedding_input_to_dict / embedding_input_from_dict
   round-trip helpers`.
3. `feat: add the 13+ per-type renderers and the generic
   fallback in text_for_record_type`.
4. `feat: add record_to_embedding_input with truncation,
   privacy class surfacing, and metadata structure`.
5. `test+example: 35+ tests for embedding input;
   examples/embedding.py`.
6. (No release: this is an alpha. The v1.0.0 release comes
   after 24b lands.)

The work is solo (one main primitive, ~250 LOC of code +
~50 LOC of test). Estimated: 1-2 days solo.

After implementation:
- `pytest -q` reports **435+ tests** (400 + ~35), 0 failures.
- `mypy --strict src/agent_memory_contracts` clean.
- All 7 examples (6 existing + `embedding.py`) run as
  smoke tests in CI.

---

## Decisions applied to this sprint

Applied 2026-06-07 per the user's "go on 24a, best
judgment" mandate. Recorded here so the spec stays the
source of truth for "why was this built this way" review.

### 9 small decisions (all defaults)

1. **Module name:** `embedding.py`.
2. **Dataclass name:** `EmbeddingInput`.
3. **`text_for_record_type` is public** (exported from
   `__init__.py`).
4. **Per-type text format:** structured-template (multi-
   line "Field: value" rendered from the record's typed
   fields). Natural-language-friendly, not raw content
   dump.
5. **Truncation:** by chars with sentence-boundary
   preference (last 200 chars of the cut point, fallback
   to hard cut at `max_chars`). `truncated=True` is set
   on the input.
6. **Content hash:** SHA-256 of canonical text (same
   shape as record ids, but distinct — the hash is of
   the text, not of the record).
7. **Privacy class surfaced as a field** on
   `EmbeddingInput`. The product uses it to gate
   embedding.
8. **Metadata structure:** flat `Mapping[str, str | int
   | float | bool]`, no nested dicts, no lists.
   Compatible with any vector DB filter API.
9. **No `embedding` CLI subcommand.** Primitives are
   library-only; defer to v1.0.1.

### 3 bigger-question decisions (all defaults)

- **Structured-template text is the right shape.** Each
  per-type renderer emits the most-natural-text field
  first (e.g., `fact_text` for a `FactLedgerEntry`,
  `claim_text` for a `CandidateClaim`) and the structured
  metadata on subsequent lines. The product can
  pre-process if it wants raw content.
- **Audit records are embeddable via the generic
  renderer.** `MemoryReducerDecision`,
  `ContextPackBuildReceipt`, etc. fall back to a generic
  "key: value" renderer. The product decides whether
  to actually embed them (typically no).
- **`text_for_record_type` is public.** Batched
  embedding pipelines want to stream the text without
  constructing an `EmbeddingInput` first.

### Minor implementation choices

- **12 hand-crafted per-type renderers.** SourceRecord,
  EpisodeRecord, EvidenceSpan, CandidateClaim,
  CandidateDecision, CandidatePreference, CandidateTask,
  CandidateTasteSignal, FactLedgerEntry,
  DecisionLedgerEntry, PreferenceLedgerEntry, TasteCard,
  ContextPack. Anything else falls back to the generic
  renderer.
- **Field order in the structured-template text:** the
  most-natural-text field first (e.g., `fact_text`,
  `claim_text`, `taste_text`), then key/value pairs in
  schema-declaration order, then `evidence_span_ids`
  joined at the end.
- **List-of-strings rendering:** sort then join with
  `", "`. Determinism + readability.
- **Privacy class default for records without the
  field:** `"internal"` (consistent with v0.9.0's
  `check_access` default).
- **Truncation marker:** `"...[truncated]"`. The
  product can search for this in stored embeddings to
  identify truncated inputs.
- **`content_hash_sha256` is a hex string, not bytes.**
  Consistent with all other ids in the library.
- **`metadata` defaults include `record_id`,
  `record_type`, `privacy_class`, `plane`.** The
  `plane` is one of `"evidence"`, `"candidate"`,
  `"ledger"`, `"taste"`, `"state"`, `"contextpack"`.
  Per-type extras are added in the per-type renderer.
