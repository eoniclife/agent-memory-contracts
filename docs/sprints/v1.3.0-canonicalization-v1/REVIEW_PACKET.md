# v1.3.0 Canonicalization v1 Review Packet

Date: 2026-06-19

## Objective

Make the repository's content-derived identity bytes explicit, shared, and
testable before product adapters start depending on them.

Several modules already used the same canonical JSON rule locally:

```python
json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

This sprint centralizes that rule in one private helper, keeps all existing
public wrapper functions working, and publishes golden vectors for the current
ID and fingerprint surface.

## Scope

Changed surfaces:

- `src/agent_memory_contracts/_canonical.py`: private canonical JSON and SHA-256
  helper with `CANONICALIZATION_VERSION = "canonical-json-v1"`.
- `src/agent_memory_contracts/evidence_ids.py`: delegates `_canonical_json` and
  `sha256_hex` to the shared helper while preserving the existing functions.
- `src/agent_memory_contracts/candidate_ids.py`,
  `src/agent_memory_contracts/ledger_ids.py`,
  `src/agent_memory_contracts/taste_ids.py`,
  `src/agent_memory_contracts/state_ids.py`,
  `src/agent_memory_contracts/contextpack_ids.py`: preserve existing
  `canonical_payload` wrappers and delegate to the shared helper.
- `src/agent_memory_contracts/bundles.py`: uses the shared helper for bundle
  fingerprint record serialization.
- `src/agent_memory_contracts/runtime/store.py`: preserves the module-level
  `canonical_json` wrapper and delegates to the shared helper.
- `src/agent_memory_contracts/runtime/anchors.py`: preserves legacy stored scope
  serialization for replay descriptors; anchor fingerprints continue to use
  `bundle_fingerprint(...)`.
- `docs/CANONICALIZATION-v1.md`: durable byte contract and golden vector table.
- `tests/test_canonicalization.py`: regression tests for canonical JSON bytes,
  existing ID vectors, and existing bundle fingerprint vectors.
- `CHANGELOG.md`: Unreleased entry.

## Non-Goals

- No schema version change.
- No public export from `agent_memory_contracts.__init__`.
- No change to current ID prefixes or digest truncation lengths.
- No change to embedding stable-text rendering, integration response JSON, CLI
  fixture JSON, or migration example output.
- No semantic identity split or duplicate-mode behavior in this sprint.

## Compatibility

This sprint is intended to be byte-for-byte compatible for existing IDs, bundle
fingerprints, and runtime payload comparisons. It is a refactor plus
documentation/test hardening.

Callers importing existing helper functions continue to work:

- `agent_memory_contracts.evidence_ids.sha256_hex`
- `agent_memory_contracts.evidence_ids._canonical_json`
- `agent_memory_contracts.<plane>_ids.canonical_payload`
- `agent_memory_contracts.runtime.store.canonical_json`

The new `_canonical` module is private. Product ports may mirror the documented
rule and golden vectors; they should not treat `_canonical` as a stable public
API.

## Local Verification

Passed locally from the worktree with `PYTHONPATH=src`:

```bash
python -m pytest tests/test_canonicalization.py tests/test_bundles.py tests/test_evidence.py tests/test_candidate.py tests/test_ledger.py tests/test_taste.py tests/test_state.py tests/test_contextpack.py tests/test_runtime_anchors.py tests/test_runtime_store.py -q
python -m pytest tests/invariants -q
python -m mypy src/agent_memory_contracts
python -m pytest -q
python scripts/audit_public_api.py
git diff --check
python -m compileall -q src
```

Results:

- Focused canonicalization, bundle, ID, runtime anchor, and runtime store tests
  passed.
- Invariant tests passed with expected Arthashila dataset skips.
- Full suite passed with expected Arthashila dataset skips and the existing
  jsonschema-installed skip.
- `mypy` passed: `Success: no issues found in 39 source files`.
- `git diff --check` passed.
- `compileall` passed.
- Public API audit exited `0`. It printed existing STABILITY-vs-`__all__`
  warnings; this branch adds no public names.

## Review Questions

1. Does the private helper placement reduce drift without freezing a public API
   too early?
2. Are the golden vectors broad enough to protect the identity and fingerprint
   surfaces that external ports are likely to reimplement?
3. Are any remaining local `json.dumps` sites actually identity-bearing and
   therefore missing from this centralization pass?
4. Is it right that stored audit-anchor scope text stays on legacy JSON bytes,
   while the anchor fingerprint remains governed by `bundle_fingerprint(...)`?
5. Should `docs/CANONICALIZATION-v1.md` become part of the packaged wheel, or is
   repository-level documentation enough for v1.3.0?
6. Is the compatibility story clear enough for adapters that already imported
   the old wrapper helpers?

## Residual Risks

- Python's JSON behavior is the reference implementation here. Ports in other
  runtimes need to match escaping, key ordering, Unicode handling, numeric
  rendering, and separator bytes against the golden vectors.
- The central helper is intentionally private; that avoids freezing too much
  public surface, but it also means external users rely on docs and tests rather
  than a formal import.
- This sprint does not address semantic identity vs full-record fingerprint
  boundaries. That remains a separate hardening candidate.

## Next Sprint Candidates

1. separate semantic identity from full-record fingerprint and add strict
   duplicate modes;
2. add machine-readable access decisions and summaries;
3. correct integration claims and stability tiers;
4. cut `v1.3.0` once the hardening PRs are reviewed and merged.
