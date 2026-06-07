# Sprint 24d / v1.0.0-alpha.4 spec: end-to-end demo

**Status:** awaiting the post-24c review. Defaults applied
per the user's "best judgment" mandate.

**Branching decision:** staying on `main`.

---

## Problem

The library has 5 working examples
(`quickstart.py`, `extract_taste_cards.py`,
`reference_reducer.py`, `conflict_resolution.py`,
`citations.py`, `access.py`, `embedding.py`,
`migrations.py`, `compilation.py`). Each one
demonstrates a single module. What the library does
NOT have is **one script that runs the whole pipeline**
— the story you tell an accelerator partner in 5
minutes.

This sprint ships that script. It is the demo. It is
what the user shows in the interview. It is not a
feature; it is the marketing artifact for the
library's v1.0.0 story.

The demo runs the pipeline that an accelerator
partner can recognize as "the company brain primitive":

1. **Ingest** — load 3 raw sources (simulated as
   dicts: a chat transcript, a Notion doc, an email).
2. **Extract** — simulate an LLM extraction producing
   5-8 candidate claims (the candidates are
   synthetic; the demo doesn't call an LLM, but the
   output shape is what an LLM would produce).
3. **Reduce** — run a simulated reducer that promotes
   some candidates to trusted ledger entries and
   rejects others.
4. **Cite** — build the citation graph; verify all
   trusted claims have a path to a source.
5. **Access** — apply `team_scope` to filter; record
   the per-record decision.
6. **Embed** — render each trusted record as an
   `EmbeddingInput` (the input boundary; the demo
   doesn't call an embedding model, but shows the
   text and metadata that would be embedded).
7. **Compile** — produce a `ContextPack` for a sample
   task. Show the `BuildReceipt` and `ValidationReport`.

The demo is a single Python script. It runs in <5
seconds. It uses the library's public API only — no
test fixtures, no internal helpers. The output is a
human-readable trace of each step.

---

## What's in this sprint

### New example: `examples/company_brain_demo.py`

A single Python script that runs the full pipeline.

**Structure:**

```python
# Stage 0: simulate 3 raw sources
sources = [simulated_source_chat, simulated_source_doc, simulated_source_email]

# Stage 1: ingest (record -> SourceRecord + EvidenceSpan)
ingested = [ingest(s) for s in sources]
# Output: 3 SourceRecord, 5 EvidenceSpan dicts

# Stage 2: extract (simulated LLM output -> CandidateClaim)
candidates = [simulated_candidate(...) for ... in ingested]
# Output: 8 CandidateClaim dicts

# Stage 3: reduce (candidates -> FactLedgerEntry)
# Simulated reducer: keep high-confidence, drop low
promoted, rejected = simulated_reduce(candidates)
# Output: 5 FactLedgerEntry dicts, 3 rejected candidates

# Stage 4: cite (CitationGraph.from_bundle)
graph = CitationGraph.from_bundle(promoted)
# Output: graph.size, node_count_by_kind, dangling_refs count

# Stage 5: access (team_scope -> filtered bundle)
filtered, decisions = scope_bundle(promoted, team_scope())
# Output: filtered list, AccessDecision per record

# Stage 6: embed (EmbeddingInput per filtered record)
embeddings = [record_to_embedding_input(r) for r in filtered]
# Output: EmbeddingInput per record (text + metadata)

# Stage 7: compile (ContextPack for a sample task)
result = compile_context_pack(bundle, task=task)
# Output: CompilationResult (pack, receipt, report)
```

The demo prints a stage-by-stage trace to stdout:

```
=== Stage 1: Ingest ===
Loaded 3 sources, produced 3 SourceRecord + 5 EvidenceSpan

=== Stage 2: Extract ===
Simulated LLM produced 8 candidate claims

=== Stage 3: Reduce ===
Promoted 5 to trusted ledger, rejected 3 (low confidence / stale)

=== Stage 4: Cite ===
CitationGraph: 8 nodes (1 source, 5 evidence, 2 claim) + 0 dangling refs
All promoted claims have valid source chains

=== Stage 5: Access ===
team_scope filtered: 5 of 8 records (1 dropped: sensitive)

=== Stage 6: Embed ===
Rendered 5 EmbeddingInputs; sample text preview:
  fact_aaa: "Spec-first beats no spec..."

=== Stage 7: Compile ===
Compiled ContextPack for task "what is the spec-first approach?"
  pack_id: ctx_xxx
  selected: 3 records
  excluded: 0 records
  validation: pass
  build_receipt: agent-memory-contracts (mode=compiler)
```

The demo is a real script. It runs as part of the
"all examples" smoke test in CI. It can also be run
directly:

```bash
PYTHONPATH=src python examples/company_brain_demo.py
```

---

## What's NOT in this sprint

- **No new library code.** The demo uses the existing
  public API only. The library is feature-complete for
  v1.0.0; the demo is a customer-facing artifact, not
  a feature.
- **No LLM calls.** The demo simulates LLM output
  with hard-coded candidates. The product is
  expected to call an LLM and pass the real output
  into the library.
- **No embedding model calls.** The demo renders
  `EmbeddingInput` but doesn't call an embedding
  model. The product is expected to call its model
  of choice.
- **No vector storage.** The demo prints the
  `EmbeddingInput` for each record; the product
  stores the resulting vectors.
- **No state snapshot in the demo.** The 24c compiler
  requires a state record; the demo includes a
  minimal `ProjectStateSnapshot` dict.

---

## Decisions applied to this sprint

Applied 2026-06-07 per the user's "best judgment" mandate.

### 9 small decisions (all defaults)

1. **File name:** `examples/company_brain_demo.py`.
2. **Source simulation:** hard-coded dicts (no
   external file). The demo is self-contained.
3. **Number of sources:** 3 (chat, doc, email).
4. **Number of candidates per source:** 2-3.
5. **Reducer policy:** promote `high` confidence;
   reject `low` confidence and `stale` claims.
6. **Citation graph check:** report `size`,
   `node_count_by_kind`, and `dangling_refs`.
7. **Access scope:** `team_scope()`.
8. **Sample task:** "what is the spec-first approach?".
9. **Output format:** plain-text stage-by-stage
   trace (no JSON, no loguru). The product can
   reformat as needed.

### 3 bigger-question decisions (all defaults)

- **The demo is deterministic.** Hard-coded
  candidates, no LLM randomness, no time-based
  behavior. Two runs produce the same output. (The
  compiler's `created_at` and `validated_at`
  timestamps do change second-by-second; the demo
  asserts on stable fields like `pack_id` and
  `selected_record_ids`, not timestamps.)
- **The demo is short (<200 LOC).** It demonstrates
  the pipeline in 7 stages without going deep on
  any one. The 9 existing examples are the deep
  dives; the demo is the overview.
- **The demo prints to stdout, not a file.** The
  output is the trace; the user can pipe it to a
  file if they want.

### Minor implementation choices

- **No external data files.** The demo's input is
  hard-coded; no fixtures, no JSON loading.
- **Use of `private_scope()` for the access check is
  NOT in the demo.** The demo applies `team_scope`
  (the common case), not `private_scope` (the
  owner-only case).
- **The demo's `task` is `task_type="research"`.** The
  schema requires a valid `task_type`; "research" is
  in the library's `TASK_TYPES` set.
- **The reducer's "rejected" output is a list of
  candidate dicts, not deleted from the bundle.**
  This mirrors the real reducer pattern: rejected
  candidates stay in the bundle as audit records.
- **The demo's bundle includes a state record.**
  The compiler requires one; the demo includes a
  minimal `ProjectStateSnapshot` dict.
