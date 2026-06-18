# Architecture

This document explains the design of `agent-memory-contracts` at a level
deeper than the README. It is written for someone who is going to build
on top of these contracts, or evaluate whether they fit their agent
memory architecture.

## The problem

Most AI agent memory work makes one of two mistakes:

1. **No reducer.** LLM extraction writes directly to "memory." The
   agent's current state silently becomes whatever the last LLM call
   produced. Memory pollution is invisible until the agent confidently
   asserts something false.

2. **Reducer, but no boundary.** A reducer exists, but the rest of the
   system is free to read or copy untrusted candidate fields into
   trusted planes. The reducer becomes a checkbox.

The contracts in this library enforce the boundary in the type system,
not as a runtime check you can forget.

## The six planes

```
  raw sources
      |
      v
  +---------+
  |EVIDENCE |  SourceRecord, EpisodeRecord, EvidenceSpan
  +---------+
      |
      v
  +-----------+      untrusted extraction
  | CANDIDATE |  CandidateClaim, CandidatePreference,
  +-----------+  CandidateDecision, CandidateTask, CandidateTasteSignal
      |                |
      |                |  (LLM extraction; reducer-required to promote)
      v                v
  +-----------+   +-----------+
  |  LEDGER   |   |  CANDIDATE |  (untrusted until reducer approves)
  | fact/pref|   |   (held)   |
  |   /dec   |   +-----------+
  +-----------+        |
        |              v (reducer decision authorizes promotion)
        v
  +-----------+        +-----------+
  |  TASTE    |<-------| REDUCER   |  MemoryReducerDecision,
  | TasteCard |        | DECISIONS |  TasteReducerDecision,
  +-----------+        +-----------+  StateReducerDecision
        |
        v
  +-----------+
  |   STATE   |  ProjectStateSnapshot, CoreStateSnapshot
  +-----------+
        |
        v
  +-----------+
  |CONTEXTPACK|  ContextPack, BuildReceipt, ValidationReport
  +-----------+
        |
        v
  task execution
```

Each plane has:
- a fixed record type with a stable ID prefix that announces plane membership
- a Python `frozen=True` dataclass with a `from_dict` constructor that validates
- a `validate_*_bundle` function that checks cross-plane reference integrity
- a JSON Schema (Draft 2020-12) for non-Python implementations

## Why the planes are separated

The architectural invariant is:

> **An untrusted candidate cannot become trusted memory without a reducer decision.**

Each plane enforces this by carrying fields only the next plane down
would accept, and refusing to validate if you mix them.

Concretely:
- A `Candidate*` record carries `candidate_type`, `extracted_by`, `natural_language_summary`, `review`. A `LedgerEntry` is forbidden to carry these fields; the validator rejects it. (`CANDIDATE_ONLY_FIELDS` and `LEDGER_ONLY_FIELDS` in the source.)
- A `LedgerEntry` carries `valid_from`, `valid_until`, `stale_after`, `supersedes`, `superseded_by`, `reducer_decision_id`. A candidate is forbidden to carry any of these.
- A `MemoryReducerDecision` lists the candidate ids and ledger entry ids it authorizes. A `LedgerEntry` whose `reducer_decision_id` doesn't authorize it -- or whose status and `decision_type` don't match -- is rejected.

This means: if a future refactor accidentally lets a worker write a
candidate field into a ledger entry, the bundle validator will catch it
before the entry reaches a downstream consumer.

## The reducer pattern

The reducer is the only authority that can move records between planes:

```
candidate     --[reducer.promote]-->  ledger entry
ledger entry  --[reducer.supersede]--> new ledger entry
ledger entry  --[reducer.retract]-->  (no new entry; status=retracted)
ledger entry  --[reducer.contest]-->  (no new entry; status=contested)
ledger entry  --[reducer.archive]-->  (no new entry; status=archived)
ledger entries --[state reducer]-->  project_state_snapshot / core_state_snapshot
ledger + taste --[taste reducer]-->  taste_card
project_state --[context pack compiler]--> context_pack
```

Every reducer decision is itself a record with:
- `id` (content-derived)
- `decision_type`
- `target_candidate_ids`, `target_ledger_entry_ids`, etc.
- `evidence_span_ids` (must be a non-empty subset of what the targets cite)
- `rationale` (free text, but required)
- `decided_by` (agent, model, tool, prompt_ref)
- `decided_at` (ISO-8601)
- `confidence`, `risk_class` (each: low / medium / high)
- `checks` (per-decision-type key set, each value: pass / fail / unknown)

The `checks` field is the part the human operator reviews. It is
required, not optional, and the validator checks that the right keys
are present for the decision type.

## Temporal validity

Every trusted record carries:
- `valid_from`: when this version became current
- `valid_until`: when this version stopped being current (null = still current)
- `stale_after`: when this version should be re-validated (null = never)
- `created_at`, `updated_at`: bookkeeping

`valid_until` is what makes supersession work. When entry B supersedes
entry A, the bundle validator checks:
- A's `superseded_by` includes B
- B's `supersedes` includes A
- A's `valid_until` is set
- B's `valid_from` is set
- A's `valid_until` <= B's `valid_from`

If any of these is wrong, the bundle is rejected. This means a bad
supersession cannot quietly make A "still current" -- the temporal
constraint is enforced.

## ID derivation

All IDs are content-derived using SHA-256 of canonical JSON. Same
payload = same ID, forever. The deterministic IDs are what make:
- **reproducibility**: re-running a pipeline gives the same ids
- **deduplication**: identical candidates collapse to one id
- **falsification**: the bench can refer to records by id without
  recomputing anything

The id derivation takes a `normalized_payload` dict that mirrors the
record's `semantic_payload()`. The mapping from "what's in the record"
to "what's in the id" is one-to-one and explicit, so the test suite
can lock it down.

## What this library is not

To set expectations:

- **Not a vector store.** The schemas describe memory; how you store
  and retrieve spans is your problem. The contracts assume a separate
  retrieval substrate.
- **Not an agent runtime.** Workers, queues, leases, scheduling live
  in other projects.
- **Not a model-call wrapper.** The contracts describe memory; how
  candidates are produced by an LLM is your problem. The contracts do
  carry `extracted_by` (agent / model / tool / prompt_ref) for audit.
- **Not a complete second-brain system.** This is the contract
  surface. The integration, the workers, the substrate, the eval
  bench, and the falsification protocol all live in the upstream
  kernel (private).

## Origin and provenance

These contracts are extracted from `avs-memory-kernel`, a
governance-heavy AI agent memory kernel built with a falsification-first
sprint protocol over 30+ sprints. Each sprint was scoped, falsified
against a bench, and sealed by GPT review before merging. The
extracted slice here is what the rest of the kernel (workers,
runtime, retrieval substrate) builds on top of -- published first
because it is the most generally useful part.

If you want to read the design history -- sprint review packets,
falsification benches, the policy that no architecture change happens
inside an implementation sprint -- those live in the upstream kernel
review packets. The contract IDs and the supersession-reciprocity
rules were each locked across multiple sprints before any worker was
allowed to depend on them.

## Future plans

The contracts are frozen at `1.0.0` for the planes currently
shipped; v1.1.0 added a `freshness_score` field (with the
v1.0.0 → v1.1.0 migration step registered in
`default_migrator()`); v1.2.0 added the audit pack generator
and the reference runtime (sqlite3, stdlib-only) under
`agent_memory_contracts.audit` and
`agent_memory_contracts.runtime`. Subsequent `1.x` releases
are backwards-compatible; breaking changes require a `2.0.0`
with a 3-minor-version deprecation window per the policy in
`docs/STABILITY.md`.

Additions shipped in v1.x so far:

- **Audit pack** (`compute_audit_pack` /
  `audit_pack_to_markdown`) — chain-of-custody report behind
  any bundle.
- **Reference runtime** (`agent_memory_contracts.runtime`) —
  MemoryStore + MemoryGate + verify_chain + verify_coverage +
  answer + build_context_pack. stdlib-only; the conformance
  test for any product-side port.
- **LangChain and MCP integrations** under
  `agent_memory_contracts.integrations`.
- **Decay primitives** (`apply_decay`, `DecayPolicy`,
  `DecayScore`) — freshness scoring for the context_pack
  compiler.
- **Schema migration framework** (`MigrationStep`,
  `SchemaMigrator`, `default_migrator`) — the v1.0.0 →
  v1.1.0 step is the first concrete migration.
- **PyPI publication** — `agent-memory-contracts` is on
  TestPyPI as of v1.2.0; promotion to real PyPI is held
  pending external review.

Future directions:

- **Postgres backend** behind `StorageBackend` (product repo,
  mapped in `docs/ROADMAP-to-product.md`).
- **LLM answerer** at the template boundary in
  `runtime.grounding` (the deterministic template is the
  conformance test; the LLM is the product insertion point).
- **A `taste-card` focused sub-package** with a smaller
  surface for teams that only need taste/preference memory.
- **A `worker_output_claim` schema** describing what a worker
  can claim it produced (separate from `CompletionArtifact`
  which is kernel-internal).

The five differentiator invariants in
`tests/invariants/` are the contract for any of these
additions. If a port must weaken an invariant to pass a
feature, the rule is **stop and escalate**.
