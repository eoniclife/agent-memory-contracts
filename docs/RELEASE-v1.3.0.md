# v1.3.0 - Trust-kernel hardening

`agent-memory-contracts` is the trust kernel for governed agent memory:
schemas, content-derived ids, validators, canonicalization, bundle
operations, audit receipts, access decisions, and a stdlib-only reference
runtime. v1.3.0 tightens that kernel before product work moves into a
separate governed-memory wrapper/control-plane repo.

## What changed

- Supersession validation now rejects cycles in every trusted graph.
- Canonical JSON v1 is centralized and documented with golden vectors.
- `record_fingerprint(record)` separates full-record payload equality from
  semantic ids.
- Bundle fingerprint, diff, and merge support explicit duplicate modes:
  `last`, `identical`, and `raise`.
- `AccessDecision` and `AccessSummary` now expose machine-readable reason,
  privacy, and record-type metadata while preserving legacy constructors and
  equality behavior.
- LangChain integration claims are corrected: `ContractsMemory` records
  conversation turns as source/episode/evidence trace and returns a
  ContextPack-shaped memory variable. It does not promote turns into trusted
  ledger facts.
- `ContractsMemoryConfig.privacy_class` is now applied to generated
  source/span trace records.
- Public docs now define three stability tiers: stable core, reference
  runtime, and optional integrations.
- Package metadata now uses modern SPDX license fields for cleaner release
  builds.

## What this release is honest about

- The stable core is SemVer-stable and suitable as a trust-kernel dependency.
- The reference runtime is an executable conformance target, not a hosted
  production service recommendation.
- LangChain and MCP extras are adapter-tier surfaces. They make the contracts
  easier to exercise from existing tools, but they do not by themselves make an
  agent's whole memory stack governed or poisoning-resistant.
- Product work belongs in a separate wrapper/control-plane repo that can expose
  observe, overlay, and enforce modes around existing memory systems.

## Compatibility

- Backwards-compatible with v1.2.0.
- No schema migration required.
- Existing public constructors and positional calling conventions are
  preserved.
- Existing semantic ID bytes are preserved.
- Existing bundle fingerprint defaults are preserved; stricter duplicate
  handling is opt-in.

## Release checks

Before tagging or publishing v1.3.0, run:

```bash
PYTHONPATH=src python3 -m pytest -q
python -m mypy src/agent_memory_contracts
PYTHONPATH=src python3 scripts/audit_public_api.py
PYTHONPATH=src python3 -m compileall -q src
git diff --check
python scripts/check_release_artifacts.py --run-sdist-tests --run-examples
```

The review packet for this release-readiness sprint lives at
`docs/sprints/v1.3.0-stability-release-readiness/REVIEW_PACKET.md`.

## Provenance

- Repository: https://github.com/eoniclife/agent-memory-contracts
- License: Apache-2.0
- v1.3.0 commit: to be filled from the reviewed merge commit
- Package: https://pypi.org/project/agent-memory-contracts/
