# ROADMAP: from this reference runtime to the product

**Audience:** the engineer (or agent) building the product repo
("Brainiac" working title) on top of this library.

This repository now contains two layers:

1. **The contracts** (`src/agent_memory_contracts/*.py`) — schemas,
   content-derived ids, validators. Frozen surface; the product
   imports them and never reimplements canonicalization.
2. **The reference runtime** (`src/agent_memory_contracts/runtime/`)
   — a stdlib-only (sqlite3) implementation of the product ADRs'
   storage, gating, anchoring, and grounding semantics, with the
   ADR-14 invariants executable in `tests/invariants/`.

The runtime is deliberately the *semantics*, not the *service*: the
product repo wraps it in Postgres, FastAPI, a console, and MCP. The
table below maps every ADR to what exists here and what remains.
Treat the invariant suite as the acceptance tests for the port —
**if an invariant test must be weakened to make a product feature
pass, stop and escalate.**

## ADR-by-ADR map

| ADR | What exists in this repo (file / symbol) | What remains for the product repo (implementation note) |
|---|---|---|
| **ADR-1** Server is the id authority | `runtime/gate.py` — every `submit_*` and `promote()` recomputes ids by parsing through the contracts classes; `IdMismatchError(expected, got)`; idempotent no-op upserts (`IngestReceipt.created`/`PromoteReceipt.created`) | HTTP mapping only: 400 `id_mismatch` / 200 `created: false`. One exception handler per error class in FastAPI; the gate already produces the structured errors. |
| **ADR-2** Immutable payloads + relational edges | `runtime/store.py` — one table per plane (all six planes + `answers`), extracted columns, insert-only edge tables (`supersessions`, `status_overrides`, `decision_authorizations`, `candidate_evidence`, `entry_evidence`), `check_extracted_columns()` drift check | Postgres DDL: translate `_SCHEMA` (JSONB payload, `tsvector` generated column, real indexes); schedule `check_extracted_columns` as the nightly drift job. |
| **ADR-3** Supersession as an edge, materialized at read | `store.materialize_entry()` (one shared definition for read path and gate closure), `active_ledger` view, time-travel `active_entries(as_of=...)`; `status_overrides` extends the same pattern to retract/contest/archive (deliberate adaptation — the contracts require those statuses; the ADRs didn't enumerate a mechanism) | Port the view + materialization to SQL/SQLAlchemy; keep `materialize_entry` semantics byte-compatible (the invariant suite validates the materialized form against the library validators). |
| **ADR-4** Validation closure per write | `gate.MemoryGate._validate_closure()` — the 5-step closure run to a fixpoint, hypothetical edge materialization, library errors verbatim; `full_revalidation()` is the global net | Closure load with `SELECT ... FOR SHARE`; map `ValidationRejectedError.errors` to 422 verbatim; cron the nightly `full_revalidation` (it already appends the `verified` anchor). |
| **ADR-5** Single transactional write protocol | `gate.MemoryGate.promote(decision, entries, supersessions)` — the ONLY ledger-writing API; structured `ConflictError` with `winning_decision_id`; `uq_single_promotion` partial index + supersessions PK as backstops; race test in `tests/invariants/test_reciprocal_supersession.py` | `POST /decisions` = a thin wrapper over `promote()` (and `/sources`, `/spans`, `/candidates` over `submit_*`). Deliberately do NOT add `POST /ledger`. Map `ConflictError` to 409 with the winning decision id in the body. |
| **ADR-6** Audit anchor chain | `runtime/anchors.py` — `append_anchor` on every committed batch, replayable scopes over *stored* payloads, `verify_chain()` returns first divergence (incl. the hand-armed-guard tripwire), `verify_coverage()` flags unanchored rows + derived-index drift; tamper tests for edited/deleted/reordered | `GET /audit/verify` = `verify_chain` + `verify_coverage`; publish the chain head fingerprint off-box nightly (tail-truncation is the one tamper a chain cannot self-detect — documented in the module docstring). |
| **ADR-7** Deterministic, receipted pack builds | `runtime/grounding.py` — `build_context_pack()`: active-at-as_of + scope + privacy filters, keyword+recency ranking (weights logged in `RuntimeBuildReceipt`, taste stubbed 0 per O-2), greedy chars/4 budget, candidates segregated, pack + receipts persisted content-addressed and anchored; deterministic across processes (a hash-seed bug in `compilation.py` was found and fixed by this work) | Swap token proxy for tiktoken; swap keyword score for Postgres FTS (`search_tsv` is already in the ADR DDL); turn on the taste weight when the taste reducer ships. Determinism rule stays: same state + request ⇒ same pack id ⇒ same fingerprint. |
| **ADR-8** /ask grounding contract | `grounding.answer()` — refusal threshold, structured refusal with `related_candidate_ids`, deterministic template answerer, **mechanical** `verify_grounding()` post-check (degrades to refusal, never to an uncited answer), every answer/refusal persisted with pack fingerprint (`answers` table = the time-travel audit) | Insert the LLM: prompt Claude with the numbered `[Fi]` fact blocks, then run the SAME `verify_grounding` post-check (regenerate once, then degrade — the checker does not care who wrote the sentences). Add `model_id`, `prompt_version`, latency columns. |
| **ADR-9** Conflict detection | `subject_key` extracted column + index on `candidates` and `ledger_entries` (`store._SCHEMA`); the library's `conflict.py` resolution primitives | The `conflicts` table + same-subject_key flagging at candidate submit; console side-by-side with both evidence chains; resolution through `resolve_conflict`, recorded as decisions. No auto-resolution, ever. |
| **ADR-10** Proposer contract | Contracts enforce span-backed candidates (`evidence_span_ids` non-empty) and `extracted_by` audit fields; `trust_tier` pattern demonstrated in `examples/arthashila_demo/build.py` (`metadata.trust_tier`, used by the reject kill-chains) | The LLM proposer pipeline: claim schema, normalized-offset spans with `normalization_version`, quote==slice check at submit, `trust_tier` as an extracted column on sources, `auto_reject_floor` as recorded system decisions through `promote()` (reject decisions already flow through the gate). |
| **ADR-11** MCP tool schemas | `store.search_entries()` returns the exact `search_ledger` output shape (`LedgerSearchHit.to_dict()`); `build_context_pack` / `submit_candidate` provide the other two tools' semantics; existing `integrations/mcp.py` shows the FastMCP wiring pattern | A thin `/mcp` server: three tools calling the service API with a bearer token, zero business logic. Freeze the v1 schemas as written in the ADR. |
| **ADR-12** Console | Nothing (out of scope for a library, deliberately) | Next.js + React Query against FastAPI; no optimistic updates on decisions; `review_leases` table for the queue state machine; budget real effort on span-highlight evidence rendering — it is the screenshot. |
| **ADR-13** Tenancy, privacy, retention | `tenant_id` on every table, all reads/writes tenant-scoped (`MemoryStore(tenant_id=...)`, isolation tested); privacy clearance filter — entry tier derives from cited evidence, fail-closed (`store._effective_privacy_index`), tested down to "low-clearance pack can never contain a high-tier fact" | Postgres RLS with per-tenant roles (schema needs no change); crypto-shredding: add `content_encrypted` + `key_id` columns on sources/spans, per-source key wrapper, deletion = key destruction (ids + anchor fingerprints survive). |
| **ADR-14** The differentiator suite | `tests/invariants/` — all five invariants executable, each with positive + adversarial tests, headers in product language; runs in the normal pytest suite | Run the same suite against the Postgres backend in CI on every PR (the suite is the port's acceptance gate); wire the "weakened invariant ⇒ stop and escalate" rule into review policy. |

## Deliberate adaptations (library-pure vs ADR-as-written)

- **sqlite3, not Postgres.** Stdlib-only is a house rule of this
  library. The semantics (append-only triggers, partial unique
  indexes, single-writer transactions standing in for `FOR SHARE`)
  are implemented sqlite-natively; `store.StorageBackend` is the
  protocol seam, and the invariant suite is the conformance test
  for any other backend.
- **`status_overrides` edge table.** The contracts support
  retract/contest/archive; the ADRs only specced supersession
  against append-only rows. The runtime extends the same
  edge-materialized pattern (one terminal override per entry,
  first commit wins).
- **Synthesized `ProjectStateSnapshot` per pack.** The library
  compiler requires a state reference; the state *reducer* is
  product scope. The runtime derives a deterministic,
  content-addressed snapshot from the pack's selection
  (`grounding._synthesize_state_snapshot`). Replace with real
  state-reducer output when it exists; pack ids will change, which
  is correct (different state, different pack).
- **Deterministic template answerer.** No LLM calls in this repo,
  so the whole ADR-8 contract is testable. The product swaps the
  text generator and keeps the mechanical checker.
- **Guarded ingestion planes.** The ADRs only require gating the
  trusted plane; the runtime gates every plane table so that every
  committed batch is anchored and `verify_coverage` can account
  for every row. Strictly stronger; keep it.

## Suggested build order for the product repo

1. Postgres `StorageBackend` + gate port; run `tests/invariants/`
   against it until green (this is most of the risk).
2. FastAPI service: thin handlers over gate/grounding; error-class
   to status-code mapping (400/409/422 already structured).
3. Proposer pipeline (ADR-10) + conflicts (ADR-9).
4. MCP server (ADR-11) — thin client of the service.
5. Console (ADR-12).
6. Phase-3 hardening: RLS, crypto-shredding, off-box anchor head.
