# v1.3.0 Supersession Acyclicity Review Packet

Date: 2026-06-19

## Objective

Close the remaining supersession graph integrity hole: ledger entries,
TasteCards, and state snapshots already validate dangling references,
reciprocity, and temporal handoff, but they did not explicitly reject cycles.

This sprint adds acyclicity as a graph invariant without changing schemas, ID
bytes, public APIs, or runtime storage semantics.

## Scope

Changed surfaces:

- `src/agent_memory_contracts/_supersession.py`: private shared graph helper for
  iterative, deterministic acyclicity validation.
- `src/agent_memory_contracts/ledger_contracts.py`: calls the helper after
  ledger supersession link and temporal checks.
- `src/agent_memory_contracts/taste_contracts.py`: calls the helper for
  TasteCards and adds visited-set protection to `taste_supersession_chain`.
- `src/agent_memory_contracts/state_contracts.py`: calls the helper for project
  and core state families.
- `src/agent_memory_contracts/state_queries.py`: adds visited-set protection to
  state supersession-chain helpers.
- `tests/test_supersession.py`: long-chain, long-cycle, self-loop, and
  deterministic-message coverage for the shared helper.
- `tests/test_ledger.py`, `tests/test_taste.py`, `tests/test_state.py`: focused
  cycle rejection and malformed-chain tests across ledger, TasteCard, project
  state, and core state.
- `tests/test_runtime_gate.py`: verifies that a gate-level supersession cycle is
  rejected as a structured validation error and leaves the store unchanged.
- `CHANGELOG.md`: Unreleased entry.

## Non-Goals

- No schema version change.
- No content-derived ID change.
- No new public export.
- No change to supersession semantics for valid acyclic histories.
- No product/adapter/review-queue work in the core package.

## Compatibility

This is a stricter validator behavior. Bundles with cyclic supersession graphs
now fail validation even if each individual link is reciprocal and each local
temporal comparison passes. Valid acyclic bundles remain accepted.

Deep valid acyclic histories are accepted without depending on Python recursion
depth; cycle detection uses an explicit stack.

Query helper behavior is also stricter for malformed raw dictionaries:
`taste_supersession_chain` and state supersession-chain helpers now raise
`ValueError` when the followed chain loops.

## Local Verification

Passed locally from worktree with `PYTHONPATH=src`:

```bash
python -m pytest tests/test_supersession.py tests/test_ledger.py tests/test_taste.py tests/test_state.py tests/test_runtime_gate.py -q
python -m pytest tests/invariants -q
python -m mypy src/agent_memory_contracts
python -m pytest -q
python scripts/audit_public_api.py
git diff --check
python -m compileall -q src
```

Results:

- Focused helper, plane, and runtime tests passed.
- Invariant tests passed with expected Arthashila dataset skips.
- Full suite passed with expected Arthashila dataset skips and the existing
  jsonschema-installed skip.
- `mypy` passed: `Success: no issues found in 38 source files`.
- `git diff --check` passed.
- `compileall` passed.
- Public API audit exited `0`. It printed existing STABILITY-vs-`__all__`
  warnings; this branch adds no public names.

## Review Questions

1. Is the acyclicity helper placed at the right private layer, or should it live
   inside each plane module?
2. Are the cycle error messages deterministic and specific enough for external
   implementers?
3. Do the tests prove that cycles are rejected after existing reciprocity and
   temporal gates pass, rather than merely hitting older validation failures?
4. Is the runtime-gate regression sufficient for this sprint's production path,
   or should broader runtime cycle fixtures be added?
5. Should ledger chain query helpers exist in a future sprint, or is Taste/state
   coverage sufficient for current public helpers?

## Residual Risks

- The helper silently ignores successors not present in `records_by_id`; current
  callers reject dangling supersession references before calling it.
- The helper follows `superseded_by` edges for acyclicity because those point
  from older to newer records. Reciprocal `supersedes` checks remain in the
  plane-specific validators.

## Next Sprint Candidates

Next core hardening candidates:

1. centralize canonicalization v1 and publish golden vectors;
2. separate semantic identity from full-record fingerprint and add strict
   duplicate modes;
3. add machine-readable access decisions and summaries;
4. correct integration claims/stability tiers and cut `v1.3.0`.
