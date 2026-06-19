# Sprint Review Packet: v1.3.0 Stability and Release Readiness

## Scope

Base: `3c7f7f7da01e7671086a75cdf4de57ee6439c5c2`

Branch: `codex/amc-v130-stability-release-readiness`

This sprint closes the v1.3.0 trust-kernel hardening line and prepares the
public repo for a reviewed release.

Changes:

- bump package metadata from `1.2.0` to `1.3.0`;
- move the accumulated v1.3.0 hardening work from `[Unreleased]` into a dated
  `CHANGELOG.md` release section;
- add `docs/RELEASE-v1.3.0.md` and make release-artifact verification require
  it in the sdist;
- replace the README's static TestPyPI badge with a live PyPI version badge;
- modernize package license metadata to use SPDX `license` and
  `license-files` with `setuptools>=77`;
- define stability tiers in README and `docs/STABILITY.md`:
  stable core, reference runtime, optional integrations;
- update architecture, roadmap, and migration docs so product work is framed
  as a separate governed-memory wrapper/control plane with
  observe/overlay/enforce modes;
- correct and enforce LangChain integration behavior: `ContractsMemory`
  records validator-valid source/episode/evidence trace and returns a legacy
  `context_pack` session-trace envelope; it does not promote turns into
  trusted ledger facts or return a full `ContextPack` record;
- correct the packaged historical Sprint 25 LangChain spec with a v1.3.0
  errata note so the sdist does not ship stale fact-ledger/reducer claims;
- apply `ContractsMemoryConfig.privacy_class` to generated SourceRecord and
  EvidenceSpan records and fail closed when two shared-session adapters use
  conflicting privacy classes;
- allocate LangChain turn indices from the shared `MemoryStore`, so two
  adapters writing the same session do not generate duplicate episode/span ids
  and then lose the second turn during merge de-duplication;
- repair the documented top-level bundle-diff API by exporting
  `BundleDiff` and `bundle_diff` from `agent_memory_contracts`;
- add regression coverage for LangChain trace-vs-ledger behavior and privacy
  propagation, including validator round-trips for generated trace records.

## Non-Goals

- No schema or ID-byte changes.
- No schema-level public surface expansion; `BundleDiff` and `bundle_diff`
  are now exported at the top level to match the existing README/STABILITY
  documentation.
- No new mandatory dependencies.
- No product control plane, review queue, hosted service, or adapter framework.
- No claim that optional integrations enforce a whole agent memory stack.
- No release tag or publication inside this PR review packet.

## Compatibility Contract

Existing v1.2.0 users can upgrade without migration:

- core semantic ID helpers are unchanged; newly generated LangChain adapter
  trace IDs change because the adapter now uses valid source/episode/locator
  vocabularies;
- schema versions are unchanged;
- public constructors and positional calling conventions are unchanged;
- bundle fingerprint defaults remain legacy-compatible;
- stricter duplicate handling remains opt-in;
- `AccessDecision` structured metadata remains additive and equality-safe;
- LangChain generated trace records now validate against the evidence-plane
  contracts and honor the configured privacy class, which makes the existing
  config field effective without changing its type or constructor shape.

## Local Gates

Run on Python 3.12.12 in `/tmp/amc-release-check-venv312` before PR:

```bash
/tmp/amc-release-check-venv312/bin/python -m pytest -q tests/test_integrations_langchain.py
# 25 passed

/tmp/amc-release-check-venv312/bin/python -m pytest -q
# pass; expected Arthashila dataset-gated skips and the jsonschema-installed
# missing-path skip

/tmp/amc-release-check-venv312/bin/python -m pytest --collect-only -q
# 849 collected tests

/tmp/amc-release-check-venv312/bin/python -m mypy src/agent_memory_contracts
# Success: no issues found in 39 source files

PYTHONPATH=src /tmp/amc-release-check-venv312/bin/python scripts/audit_public_api.py
# exit 0 with known STABILITY-vs-__all__ warnings

PYTHONPATH=src /tmp/amc-release-check-venv312/bin/python -m compileall -q src
git diff --check
# pass

/tmp/amc-release-check-venv312/bin/python scripts/check_release_artifacts.py --run-sdist-tests --run-examples
# OK: release artifacts verified
# sdist: agent_memory_contracts-1.3.0.tar.gz (202 members)
# wheel: agent_memory_contracts-1.3.0-py3-none-any.whl (69 members)
```

Fix-pass after independent review:

- corrected the remaining stale LangChain `save_context` docstring;
- corrected the LangChain adapter to emit validator-valid `SourceRecord`,
  `EpisodeRecord`, and `EvidenceSpan` records instead of invalid
  conversation/turn/verbatim-shaped pseudo-records;
- made invalid `ContractsMemoryConfig.privacy_class` values fail closed;
- made shared-store/shared-session privacy conflicts fail closed;
- made shared-store/shared-session turn allocation store-scoped so two
  adapters cannot silently overwrite a distinct turn by generating duplicate
  episode/span ids;
- changed the returned LangChain `context_pack` value wording and tests so it
  is treated as a legacy session-trace envelope, not a full `ContextPack`;
- exported the documented top-level `BundleDiff` / `bundle_diff` names;
- tightened `StorageBackend` docs so it is a read/query port surface plus
  invariant target, not a drop-in replacement for private sqlite gate/store
  internals;
- corrected packaged `docs/specs/sprint_25_langchain_memory.md` claims so it
  describes trace records rather than trusted facts or reducer decisions;
- cleaned residual `langchain.memory` pseudocode in the packaged Sprint 25
  spec so it matches `langchain_classic.base_memory`;
- replaced the unresolved release-provenance placeholder with a release-tag
  provenance statement;
- reran targeted LangChain tests, full pytest, mypy, API audit, compileall,
  diff check, and release artifact verification before pushing the fix-pass
  head.

## Reviewer Questions

1. Are the stability tiers honest and narrow enough for a public release?
2. Does the roadmap now point to the right product wedge without pulling
   product responsibilities into this library?
3. Is the LangChain behavior/doc correction sufficient now that generated
   trace records validate, or should this adapter be marked more explicitly
   experimental in code-level docs?
4. Is v1.3.0 appropriate as a normal minor release, given no schema/ID break and
   only additive behavior plus one config-field effectiveness fix?
5. Are there any remaining release blockers before tagging and publishing after
   merge?
