# agent-memory-contracts

**JSON Schemas and Python contracts for AI agent memory integrity.**

[![CI](https://github.com/eoniclife/agent-memory-contracts/actions/workflows/ci.yml/badge.svg)](https://github.com/eoniclife/agent-memory-contracts/actions/workflows/ci.yml)
[![mypy](https://github.com/eoniclife/agent-memory-contracts/actions/workflows/ci.yml/badge.svg?job=mypy)](https://github.com/eoniclife/agent-memory-contracts/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agent-memory-contracts)](https://pypi.org/project/agent-memory-contracts/)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/downloads/)
[![Standard library only](https://img.shields.io/badge/dependencies-none-success)](https://github.com/eoniclife/agent-memory-contracts)
[![Schemas](https://img.shields.io/badge/JSON_Schemas-23-blue)](https://github.com/eoniclife/agent-memory-contracts/tree/main/src/agent_memory_contracts/schemas)
[![Tests](https://img.shields.io/badge/tests-325_passing-brightgreen)](https://github.com/eoniclife/agent-memory-contracts/tree/main/tests)

> The core design question this library answers: *if an LLM extracts
> something from raw sources, how do you keep that extraction from
> silently becoming "memory" the agent treats as truth?*

## Install

```bash
pip install agent-memory-contracts
```

The library has zero runtime dependencies (stdlib only). For
`python -m agent_memory_contracts validate` and the optional
JSON Schema validator, install with the `jsonschema` extra:

```bash
pip install agent-memory-contracts[jsonschema]
```

After install, the CLI is available as both a module and a console
script:

```bash
python -m agent_memory_contracts --version
agent-memory-contracts --version    # same, via the [project.scripts] entry point
```

This library was extracted from a 30+ sprint falsification-first build
of a private agent memory kernel. The schemas and id formats are
stable and treated as `1.0.0` in this initial release.

## The six memory planes

```
  raw sources
      |
      v
  +---------+      +-------------+      +-----------+
  | EVIDENCE| ---> |  CANDIDATE  | ---> |   LEDGER  |  (trusted memory)
  +---------+      +-------------+      +-----------+
       ^              untrusted extraction
       |
  +-------------+     +-----------+
  |   TASTE     |     |  REDUCER  |  (only authority that can promote)
  |  TasteCard  |<----| DECISIONS |
  +-------------+     +-----------+
       |
       v
  +-----------+        +-----------+
  |   STATE   |------->|CONTEXTPACK|  (task-ready bundles)
  +-----------+        +-----------+
```

See [`docs/architecture.md`](docs/architecture.md) for the full design
document, including the reducer authorization pattern, temporal
validity rules, and the supersession-reciprocity invariant.

## Quickstart

```bash
pip install agent-memory-contracts
```

```python
from agent_memory_contracts import (
    SourceRecord, EvidenceSpan,
    CandidateTasteSignal, PreferenceLedgerEntry, MemoryReducerDecision,
    validate_ledger_bundle,
    make_source_id, make_span_id, make_candidate_id,
    make_ledger_entry_id, make_reducer_decision_id,
)

# 1. Build a SourceRecord and an EvidenceSpan.
source_id = make_source_id("chatgpt_conversation", "https://...", "a" * 64)
span_id = make_span_id(source_id, "line_range", "10-15")

# 2. An LLM extracts a candidate interpretation (untrusted).
ctsig_id = make_candidate_id("taste_signal", [span_id], {"signal_kind": "principle", ...})

# 3. The reducer promotes it to a trusted ledger entry.
ledger_id = make_ledger_entry_id("preference", [span_id], {"ledger_type": "preference", ...})
reducer_id = make_reducer_decision_id("promote", [ctsig_id], [ledger_id], [span_id], "...")

# 4. Build all three; the bundle validator checks the whole graph.
validate_ledger_bundle(
    source_records=[...], evidence_spans=[...], candidate_records=[...],
    reducer_decisions=[...], ledger_entries=[...],
)
```

Three runnable end-to-end examples:

- [`examples/quickstart.py`](examples/quickstart.py) -- minimal source -> span -> candidate -> ledger
- [`examples/extract_taste_cards.py`](examples/extract_taste_cards.py) -- full transcript -> multiple taste cards, with contrast pairs
- [`examples/reference_reducer.py`](examples/reference_reducer.py) -- complete reference reducer (~1000 lines) with three worked scenarios: happy path, rejection of low-confidence / no-evidence / stale candidates, and a deliberate validator-enforcement case. This is the canonical answer to "what does a contracts-library reducer look like in production?"
- [`examples/conflict_resolution.py`](examples/conflict_resolution.py) -- five worked scenarios: pick-one resolution, merge resolution, split resolution, weekly hygiene report, windowed + diff-augmented hygiene report.

## What's in the box

- **23 JSON Schemas** (Draft 2020-12) in `src/agent_memory_contracts/schemas/`
- **17 Python modules** of `frozen=True` dataclasses and validators
- **5 bundle validators** that reject the bundle on dangling references,
  non-reciprocal supersession, candidate/ledger field leakage, and
  reducer authorization mismatch
- **10 temporal query helpers** for the taste and state planes
- **Content-derived ID helpers** for every record type, using SHA-256
  of canonical JSON. Same payload = same ID, forever.
- **`bundle_fingerprint(records)`** for content-addressed bundle digests.
  Set-semantic, order-insensitive, last-write-wins on duplicate ids.
  Same primitive the id helpers use, applied to the bundle as a
  whole. Useful as a cache key, idempotency token, or change-detection
  digest.
- **`bundle_diff(a, b)`** for set-semantic diff between two bundles.
  Returns a `BundleDiff(added, removed, changed, unchanged_count)`
  with full pre/post records for the changed entries. Short-circuits
  via `bundle_fingerprint` when both bundles hash equal, so the
  "no changes" case is one hash comparison.
- **`merge_bundles(*bundles, ..., prefer=...)`** for many-to-one
  bundle union. Returns a `BundleMerge(records, conflicts,
  duplicate_ids)`. Records are deduplicated by `id_field`; conflicts
  are surfaced for the reducer to triage. `prefer='last'` (default)
  / `'first'` resolve silently, `prefer='raise'` fails loudly. Useful
  for multi-source ingest, bidirectional sync, and backfill.
- **Opt-in `jsonschema` validator** (`pip install agent-memory-contracts[jsonschema]`)
  for polyglot producers that want to validate Python dataclasses
  against the bundled JSON Schemas before the record leaves the
  producing system. Includes `validate_jsonl` and
  `iter_validated_jsonl` for streaming.
- **CLI** (`python -m agent_memory_contracts`) for the non-Python
  use case: validate a JSON or JSONL file against a schema, compute
  a bundle fingerprint, diff two bundles, or merge N bundles into one.
  Stdlib `argparse`. Optional `--json` flag on every subcommand for
  programmatic consumption.
- **Zero runtime dependencies** (stdlib only)
- **~5,000 lines of Python**, ~600 lines of JSON Schema
- **325 tests** covering id derivation, contract validation, bundle
  integrity, temporal queries, the bundle fingerprint, diff, and
  merge primitives, the optional JSON Schema validator, the
  CLI (including `--json` mode), the reference reducer, the
  SQLite-to-contracts migration example, conflict resolution,
  and the memory hygiene report
- **`mypy --strict` clean** on the library code (CI gate)
- **Stdlib-only benchmark suite** at `benchmarks/` for the three
  bundle primitives (100/1k/10k/50k records, ~135ms for 50k
  fingerprints)
- **Migration guide** at `docs/migration.md` for adopting the
  library from a SQLite-style memory store, with a worked
  end-to-end example (`docs/migration_example.py`)
- **Conflict resolution** (`resolve_conflict` /
  `apply_resolutions` / `validate_resolutions`): pick-one,
  merge, and split policies for surfaced bundle conflicts.
  Audit-trail records with content-derived ids; rejected
  variants stay in the bundle flagged.
- **Memory hygiene report** (`compute_hygiene_report` /
  `hygiene_report_to_markdown`): structural snapshot of a
  bundle's health — per-plane / per-type / per-privacy counts,
  temporal state, evidence integrity. CLI subcommand
  `hygiene <path>` produces a Markdown report (or JSON with
  `--json`).

## Design principles

These are the rules the contracts enforce, in the type system, because
the alternative is a runtime bug you'll only catch in production.

1. **Untrusted extraction cannot become memory without a reducer.**
   Candidates have ID prefixes `cand_*`; ledger entries have `fact_` /
   `pref_` / `dec_`. A `Candidate*` object physically cannot carry the
   fields a ledger entry needs, and a `LedgerEntry` will refuse to
   validate if it carries candidate-only fields.
2. **Every trusted record is authorized by an explicit reducer decision.**
   A `MemoryReducerDecision` lists the candidate ids, ledger entry ids,
   and evidence span ids it authorizes. A ledger entry whose
   `reducer_decision_id` doesn't authorize it -- or whose status and
   decision_type don't match -- is rejected.
3. **Supersession is a directed graph, not a flag.** If entry B
   supersedes entry A, then A's `superseded_by` must contain B and B's
   `supersedes` must contain A. The bundle validator checks reciprocity
   and temporal ordering.
4. **IDs are content-derived, not assigned.** A taste card with the
   same evidence and the same normalized payload has the same id
   forever. Reproducible, deduplicatable, falsifiable.
5. **Generated views are views, not memory.** A `ContextPack` is a
   task-ready bundle; it carries a `BuildReceipt` (what was selected)
   and a `ValidationReport` (what passed). The receipt, not the
   context pack, is the audit trail.
6. **Bundles are content-addressed.** `bundle_fingerprint(records)`
   returns a deterministic SHA-256 of the whole bundle, set-
   semantic and order-insensitive. Same records in any order,
   same fingerprint. A bundle is treated as a set of records
   keyed by ``id``; duplicate ids are collapsed (last write wins)
   before hashing.

## Bundle fingerprint

(Since 0.3.0 — see also [Bundle diff](#bundle-diff) and [CLI](#cli) below.)

Since 0.3.0, the library ships a `bundle_fingerprint` primitive
that hashes a set of records into a single 64-char hex digest.
The same records in any order produce the same hash; any byte
change in any record changes the hash.

```python
from agent_memory_contracts import bundle_fingerprint

records = [source_record, evidence_span, taste_card, ...]
digest = bundle_fingerprint(records)
# '4f3a2b1c...'  (64 hex chars; SHA-256 of canonical-JSON bundle)

# Re-running the same pipeline on the same records gives the
# same digest, so a downstream write layer can dedupe on it.
assert bundle_fingerprint(records) == digest

# A bundle of dataclass instances and a bundle of equivalent
# dicts hash to the same value -- the canonical form is the
# same in both cases.
assert bundle_fingerprint(records) == bundle_fingerprint(
    [dataclasses.asdict(r) for r in records]
)
```

Use cases: cache key for ContextPack rebuilds, idempotency token
for sync writes, change-detection digest, audit-chain anchor.

## Bundle diff

Since 0.4.0. `bundle_diff(a, b)` returns a `BundleDiff(added, removed,
changed, unchanged_count)` describing the set-semantic difference
between two bundles. Same primitive the id helpers use, applied to
"what changed between bundle A and bundle B":

```python
from agent_memory_contracts import bundle_diff, BundleDiff
from dataclasses import asdict

diff = bundle_diff(bundle_a, bundle_b)
print(diff.added, diff.removed, len(diff.changed), diff.unchanged_count)

# When both bundles hash to the same fingerprint, the function
# short-circuits and returns the empty diff without iterating
# records. The common "no changes" case is one hash comparison.
```

For the non-Python case, the CLI exposes the same primitive:

```bash
python -m agent_memory_contracts diff bundle-a.json bundle-b.json
# 1 added, 0 removed, 0 changed, 12 unchanged
# + src_new_id
```

Use cases: cache invalidation ("did this rebuild's inputs change?"),
audit chains ("what changed in the last cycle?"), UI rendering of
diffs in a ContextPack inspector.

## CLI

Since 0.4.0. The library is usable from the command line without
writing Python. Stdlib `argparse`, no extra dependencies:

```bash
# Validate a JSON or JSONL file against one of the bundled schemas.
python -m agent_memory_contracts validate records.json --schema taste_card
python -m agent_memory_contracts validate records.jsonl --schema source_record --jsonl

# Content-addressed digest of a bundle (JSON or JSONL).
python -m agent_memory_contracts fingerprint bundle.json
# 4f3a2b1c...   (64 hex chars)

# Diff two bundles.
python -m agent_memory_contracts diff before.json after.json

# Merge N bundles.
python -m agent_memory_contracts merge a.json b.json c.json --prefer last

# Memory hygiene report.
python -m agent_memory_contracts hygiene weekly.jsonl
python -m agent_memory_contracts hygiene bundle.jsonl --from 2026-04-01 --to 2026-06-30 --json

# Misc.
python -m agent_memory_contracts --help
python -m agent_memory_contracts --version
```

Exit codes: 0 on success, 1 on validation error, 2 on usage error.
The CLI uses the same public Python API as the library, so anything
you can do from Python you can do from the shell.

## Install

```bash
pip install agent-memory-contracts
```

Or from source:

```bash
git clone https://github.com/eoniclife/agent-memory-contracts.git
cd agent-memory-contracts
pip install -e ".[dev]"
```

Requires Python 3.10+. No runtime dependencies.

## Development

```bash
pip install -e ".[dev]"
pytest -q                            # 325 tests
PYTHONPATH=src python examples/quickstart.py
PYTHONPATH=src python examples/extract_taste_cards.py
PYTHONPATH=src python examples/reference_reducer.py
PYTHONPATH=src python examples/conflict_resolution.py
PYTHONPATH=src python docs/migration_example.py
python -m mypy src/agent_memory_contracts        # strict-clean
PYTHONPATH=src python benchmarks/run_all.py      # ~3.8s
```

Tests are stdlib `unittest` (no test framework dependency at runtime).
CI runs on Python 3.10, 3.11, 3.12 via GitHub Actions. The CI workflow
includes a `mypy` job (strict) and a smoke-test step that iterates
`for f in examples/*.py` so any new example is auto-checked.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Origin and provenance

These contracts are extracted from `avs-memory-kernel` (private), a
governance-heavy AI agent memory kernel built with a falsification-first
sprint protocol. Each sprint was scoped, falsified against a bench,
and sealed by GPT review before merging. The extracted slice here is
what the rest of the kernel (workers, runtime, retrieval substrate)
builds on top of -- published first because it is the most generally
useful part.

If you build on this and want the upstream kernel to track your
changes, open an issue; if you want to see the full design history
(sprints, review packets, evals), the `eoniclife/avs-memory-kernel`
review packets are part of the public record of how these schemas got
where they are.
