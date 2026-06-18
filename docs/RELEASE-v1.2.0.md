# v1.2.0 — "Pilot readiness"

**One-paragraph pitch.** `agent-memory-contracts` is a
JSON-Schema + Python library that enforces five rules about
AI agent memory in the type system, so a memory
poisoning attack, an ungoverned supersession, or an
unauthorized promotion can't silently become "truth" the
agent serves. v1.2.0 adds the two missing pieces for
external review: an **audit pack** (a CFO-readable
chain-of-custody report behind any bundle) and a
**reference runtime** (a complete stdlib-only sqlite3
implementation of how a runtime holds and moves the six
memory planes). Plus a 5-invariant acceptance suite
that's also the port-spec for any product-side
implementation.

---

## 30-second value prop

The contract:
- **Untrusted extraction cannot become memory without a
  reducer.** Candidates have id prefix `cand_*`; ledger
  entries have `fact_` / `pref_` / `dec_`. The dataclass
  `from_dict` will refuse to build a `LedgerEntry` from a
  candidate payload.
- **Every trusted record is authorized by an explicit
  `MemoryReducerDecision`.** A ledger entry whose
  `reducer_decision_id` doesn't authorize it is rejected
  by `validate_ledger_bundle`.
- **Supersession is a directed graph, not a flag.** If B
  supersedes A, the bundle validator checks that A's
  `superseded_by` contains B and B's `supersedes` contains
  A. Reciprocity is mechanical, not aspirational.
- **IDs are content-derived, not assigned.** A record with
  the same evidence and the same normalized payload has
  the same SHA-256-prefixed id forever. Reproducible,
  deduplicatable, falsifiable.
- **Generated views are views, not memory.** A
  `ContextPack` carries a `BuildReceipt` (what was
  selected) and a `ValidationReport` (what passed). The
  receipt, not the pack, is the audit trail.

---

## What's in the box (v1.2.0)

| Layer | Surface | Notes |
|---|---|---|
| **23 JSON Schemas** | `src/agent_memory_contracts/schemas/` | Draft 2020-12, content-derived ids, frozen at "1.0.0" |
| **37 Python modules** | `src/agent_memory_contracts/` | Frozen dataclasses, mypy --strict clean |
| **5 bundle validators** | `validate_*_bundle` | Dangling refs, non-reciprocal supersession, field leakage, reducer authorization |
| **10 temporal queries** | `taste_contracts`, `state_contracts` | Time-travel on taste cards + state snapshots |
| **3 bundle primitives** | `bundle_fingerprint`, `bundle_diff`, `merge_bundles` | Content-addressed, set-semantic, last-write-wins |
| **4 provenance + access modules** | `citations`, `access`, `compilation`, `embedding` | Citation graph, scope/privacy, ContextPack compiler, embedding input |
| **3 cross-cutting** | `conflict`, `hygiene`, `decay` | Conflict resolution, hygiene report, freshness scoring |
| **2 schema migration** | `migrations` | `MigrationStep`, `SchemaMigrator`, `default_migrator` (first concrete step: v1.0.0 → v1.1.0) |
| **1 audit module** | `audit` (8 names) | `AuditPack`, `compute_audit_pack`, `audit_pack_to_markdown` |
| **1 reference runtime** | `runtime` (27 names) | `MemoryStore`, `MemoryGate`, `verify_chain`/`verify_coverage`/`answer` |
| **2 integrations** | `integrations.langchain`, `integrations.mcp` | Drop-in `BaseMemory`; FastMCP server |
| **9 examples** | `examples/*.py` | Including a poisoning demo + a real NBFC corpus demo |
| **5 invariant tests** | `tests/invariants/` | The acceptance gate for any product-side port |

---

## How to evaluate in 5 minutes

```bash
# 1. install
pip install agent-memory-contracts[all]

# 2. run the headline demo
PYTHONPATH=src python examples/poisoning_demo/run.py
# → Store A (silent) is poisoned; Store B (governed) rejects
#   the forgery with a real MemoryReducerDecision receipt.
#   Trusted ledger's bundle_fingerprint is byte-identical
#   before and after. Report: examples/poisoning_demo/out/report.md

# 3. run the differentiator invariants
python -m pytest tests/invariants/ -v
# → 23 tests: no-silent-writes, full-provenance, reciprocal-
#   supersession, deterministic-receipts, grounded-or-refused.
#   Each is positive + adversarial.

# 4. run the full NBFC corpus end to end
PYTHONPATH=src python examples/arthashila_demo/build.py
# → 11 facts extracted, 7 promoted, 2 poisoned rejected
#   with kill chains, audit pack emitted.

# 5. read the success metrics
cat examples/arthashila_demo/out/audit-pack.md
```

---

## What the v1.2.0 release is honest about

- **No production runtime.** The reference runtime is the
  *semantics* test for any product-side port (Postgres
  backend behind `StorageBackend`); the docs/ROADMAP-to-
  product.md file maps the ADRs.
- **No LLM in the answer path.** The runtime's `answer()`
  is a deterministic template answerer so the contract is
  testable. A product inserts the LLM at the template
  boundary; the same `verify_grounding` post-check runs.
- **No claims of completeness.** The five invariants in
  `tests/invariants/` are the contract — if a port must
  weaken one to pass a feature, the rule is **stop and
  escalate**. The runtime is the conformance test, not a
  product recommendation.
- **Stdlib-only at runtime.** `[jsonschema]`, `[langchain]`,
  `[mcp]` are the three optional extras; none are required.

---

## Numbers

| | v1.0.0 | v1.1.0 | v1.2.0 |
|---|---|---|---|
| Tests (passing) | 501 | 549 | 729 |
| Python modules | 28 | 31 | 37 |
| Source LOC (Python) | ~9,500 | ~10,500 | ~17,000 |
| Test LOC (Python) | ~6,500 | ~7,000 | ~12,500 |
| Public names (`__all__`) | 117 | 122 | 162 |
| Frozen at "1.0.0" | yes | yes | yes (v1.2.0 is additive) |
| `mypy --strict` | clean | clean | clean |
| Audit script | passes | passes | passes |
| CI matrix | 3.10/3.11/3.12 | 3.10/3.11/3.12 | 3.10/3.11/3.12 |
| CI status (this commit) | green | green | **green** |

---

## Where to look first

1. `README.md` — 30-second value prop, install, the 5 rules,
   9 runnable examples, audit packs section, runtime section.
2. `docs/architecture.md` — design document with the full
   six-plane model, reducer authorization pattern, and
   supersession-reciprocity invariant.
3. `docs/STABILITY.md` — the v1.0.0 freeze contract: every
   public name listed by plane, with the SemVer policy and
   the audit procedure.
4. `docs/ROADMAP-to-product.md` — successor map: every ADR
   (1-14) mapped to what exists in this repo and what
   remains for the product repo.
5. `docs/specs/sprint_28_audit_and_runtime.md` — the
   durable rationale for the v1.2.0 expansion.
6. `tests/invariants/` — 5 differentiator tests with
   product-language headers. These are the acceptance
   gate for any product-side port.

---

## Provenance

- **Repository:** https://github.com/eoniclife/agent-memory-contracts
- **License:** Apache-2.0
- **v1.2.0 commit:** `6598124` on `main`
- **v1.2.0 wheel:** TestPyPI (https://test.pypi.org/project/agent-memory-contracts/)
- **First stable:** v1.0.0 on 2026-06-07
- **Maintainer:** eoniclife
