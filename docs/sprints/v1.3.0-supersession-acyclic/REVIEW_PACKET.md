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
  deterministic acyclicity validation.
- `src/agent_memory_contracts/ledger_contracts.py`: calls the helper after
  ledger supersession link and temporal checks.
- `src/agent_memory_contracts/taste_contracts.py`: calls the helper for
  TasteCards and adds visited-set protection to `taste_supersession_chain`.
- `src/agent_memory_contracts/state_contracts.py`: calls the helper for project
  and core state families.
- `src/agent_memory_contracts/state_queries.py`: adds visited-set protection to
  state supersession-chain helpers.
- `tests/test_ledger.py`, `tests/test_taste.py`, `tests/test_state.py`: focused
  cycle rejection and malformed-chain tests.
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

Query helper behavior is also stricter for malformed raw dictionaries:
`taste_supersession_chain` and state supersession-chain helpers now raise
`ValueError` when the followed chain loops.

## Local Verification

Passed locally from worktree:

```bash
PYTHONPATH=src /Users/a/Documents/Wiki/agent-memory-contracts/.venv/bin/python -m pytest tests/test_ledger.py tests/test_taste.py tests/test_state.py -q
PYTHONPATH=src /Users/a/Documents/Wiki/agent-memory-contracts/.venv/bin/python -m pytest tests/invariants -q
PYTHONPATH=src /Users/a/Documents/Wiki/agent-memory-contracts/.venv/bin/python -m mypy src/agent_memory_contracts
PYTHONPATH=src /Users/a/Documents/Wiki/agent-memory-contracts/.venv/bin/python -m pytest -q
PYTHONPATH=src /Users/a/Documents/Wiki/agent-memory-contracts/.venv/bin/python scripts/audit_public_api.py
git diff --check
/Users/a/Documents/Wiki/agent-memory-contracts/.venv/bin/python -m compileall -q src
```

Results:

- Focused plane tests passed: `36 passed`.
- Focused plane + invariant tests passed: `62 passed, 3 skipped`.
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
4. Should ledger chain query helpers exist in a future sprint, or is Taste/state
   coverage sufficient for current public helpers?

## Residual Risks

- Runtime `MemoryGate` relies on the library validators through closure
  validation, so it should inherit this protection. A reviewer should still
  check whether there is any raw store materialization path that needs an
  additional direct test.
- The helper follows `superseded_by` edges for acyclicity because those point
  from older to newer records. Reciprocal `supersedes` checks remain in the
  plane-specific validators.

## Next Sprint Candidates

Per GPT Pro's architecture review, the next core hardening candidates are:

1. centralize canonicalization v1 and publish golden vectors;
2. separate semantic identity from full-record fingerprint and add strict
   duplicate modes;
3. add machine-readable access decisions and summaries;
4. correct integration claims/stability tiers and cut `v1.3.0`.
