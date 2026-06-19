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
- correct LangChain integration claims: `ContractsMemory` records
  source/episode/evidence trace and returns a ContextPack-shaped memory
  variable; it does not promote turns into trusted ledger facts;
- correct the packaged historical Sprint 25 LangChain spec with a v1.3.0
  errata note so the sdist does not ship stale fact-ledger/reducer claims;
- apply `ContractsMemoryConfig.privacy_class` to generated SourceRecord and
  EvidenceSpan records;
- add regression coverage for LangChain trace-vs-ledger behavior and privacy
  propagation.

## Non-Goals

- No schema or ID-byte changes.
- No new public names.
- No new mandatory dependencies.
- No product control plane, review queue, hosted service, or adapter framework.
- No claim that optional integrations enforce a whole agent memory stack.
- No release tag or publication inside this PR review packet.

## Compatibility Contract

Existing v1.2.0 users can upgrade without migration:

- semantic IDs are unchanged;
- schema versions are unchanged;
- public constructors and positional calling conventions are unchanged;
- bundle fingerprint defaults remain legacy-compatible;
- stricter duplicate handling remains opt-in;
- `AccessDecision` structured metadata remains additive and equality-safe;
- LangChain generated trace records now honor the configured privacy class,
  which makes the existing config field effective without changing its type or
  constructor shape.

## Local Gates

Run on Python 3.12.12 in `/tmp/amc-release-check-venv312` before PR:

```bash
/tmp/amc-release-check-venv312/bin/python -m pytest -q tests/test_integrations_langchain.py
# 21 passed

/tmp/amc-release-check-venv312/bin/python -m pytest -q
# pass; expected Arthashila dataset-gated skips and the jsonschema-installed
# missing-path skip

/tmp/amc-release-check-venv312/bin/python -m pytest --collect-only -q
# 845 collected tests

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
- corrected packaged `docs/specs/sprint_25_langchain_memory.md` claims so it
  describes trace records rather than trusted facts or reducer decisions;
- replaced the unresolved release-provenance placeholder with a release-tag
  provenance statement;
- reran targeted LangChain tests, mypy, API audit, compileall, diff check, and
  release artifact verification before pushing the fix-pass head.

## Reviewer Questions

1. Are the stability tiers honest and narrow enough for a public release?
2. Does the roadmap now point to the right product wedge without pulling
   product responsibilities into this library?
3. Is the LangChain behavior/doc correction sufficient, or should this adapter
   be marked more explicitly experimental in code-level docs?
4. Is v1.3.0 appropriate as a normal minor release, given no schema/ID break and
   only additive behavior plus one config-field effectiveness fix?
5. Are there any remaining release blockers before tagging and publishing after
   merge?
