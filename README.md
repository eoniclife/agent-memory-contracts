# agent-memory-contracts

**JSON Schemas and Python contracts for AI agent memory integrity.**
With a stdlib-only reference runtime and a 5-invariant acceptance
suite for any product-side port.

[![CI](https://github.com/eoniclife/agent-memory-contracts/actions/workflows/ci.yml/badge.svg)](https://github.com/eoniclife/agent-memory-contracts/actions/workflows/ci.yml)
[![mypy](https://github.com/eoniclife/agent-memory-contracts/actions/workflows/ci.yml/badge.svg?job=mypy)](https://github.com/eoniclife/agent-memory-contracts/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-849_collected-brightgreen)](https://github.com/eoniclife/agent-memory-contracts/tree/main/tests)
[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)](https://www.python.org/downloads/)
[![Standard library only](https://img.shields.io/badge/dependencies-none-success)](https://github.com/eoniclife/agent-memory-contracts)
[![Schemas](https://img.shields.io/badge/JSON_Schemas-23-blue)](https://github.com/eoniclife/agent-memory-contracts/tree/main/src/agent_memory_contracts/schemas)
[![Public API](https://img.shields.io/badge/public_names-164-blue)](https://github.com/eoniclife/agent-memory-contracts/blob/main/docs/STABILITY.md)
[![PyPI](https://img.shields.io/pypi/v/agent-memory-contracts.svg)](https://pypi.org/project/agent-memory-contracts/)

> The core design question this library answers: *if an LLM extracts
> something from raw sources, how do you keep that extraction from
> silently becoming "memory" the agent treats as truth?*

The contracts enforce five rules in the type system, because the
alternative is a runtime bug you'll only catch in production:

1. **Untrusted extraction cannot become memory without a reducer.**
   Candidates have id prefix `cand_*`; ledger entries have `fact_` /
   `pref_` / `dec_`. A `Candidate*` cannot carry the fields a ledger
   entry needs, and a `LedgerEntry` will refuse to validate if it
   carries candidate-only fields.
2. **Every trusted record is authorized by an explicit reducer
   decision.** The validator rejects a ledger entry whose
   `reducer_decision_id` doesn't authorize it.
3. **Supersession is a directed graph, not a flag.** If entry B
   supersedes entry A, then A's `superseded_by` must contain B and
   B's `supersedes` must contain A. The bundle validator checks
   reciprocity and temporal ordering.
4. **IDs are content-derived, not assigned.** A record with the
   same evidence and the same normalized payload has the same id
   forever. Reproducible, deduplicatable, falsifiable.
5. **Generated views are views, not memory.** A `ContextPack` is a
   task-ready bundle that carries a `BuildReceipt` (what was
   selected) and a `ValidationReport` (what passed). The receipt,
   not the pack, is the audit trail.

The same contracts ship a stdlib-only **reference runtime** (sqlite3,
6 planes, single transactional write path, hash-chained audit
anchors, cite-or-refuse answer) and a 5-invariant acceptance suite
([`tests/invariants/`](tests/invariants/)) that doubles as the
port-spec for any product-side implementation. See
[`docs/ROADMAP-to-product.md`](docs/ROADMAP-to-product.md) for the
ADRs that map to the runtime.

## Install

```bash
pip install agent-memory-contracts
```

The library has zero runtime dependencies (stdlib only). The
optional extras are:

```bash
pip install agent-memory-contracts[jsonschema]   # for the JSON Schema validator + CLI validate
pip install agent-memory-contracts[langchain]    # for the BaseMemory integration
pip install agent-memory-contracts[mcp]          # for the FastMCP server
pip install agent-memory-contracts[all]          # everything
```

After install, the CLI is available as both a module and a console
script:

```bash
python -m agent_memory_contracts --version
agent-memory-contracts --version    # same, via the [project.scripts] entry point
```

This library was extracted from a 30+ sprint falsification-first build
of a private agent memory kernel. The schemas, id formats, and
public API are frozen at v1.0.0; subsequent 1.x releases are
backwards-compatible.

## Stability tiers

`agent-memory-contracts` is a trust kernel, not a hosted product.
Its public surface is split into three tiers:

- **Stable core.** JSON Schemas, dataclasses, ID helpers,
  canonicalization, validators, bundle fingerprint/diff/merge,
  migrations, audit packs, access decisions, and the CLI follow the
  SemVer policy in [`docs/STABILITY.md`](docs/STABILITY.md).
- **Reference runtime.** `agent_memory_contracts.runtime` is the
  executable sqlite3 reference for governed-memory semantics and the
  conformance target for product-side ports. It is not a production
  service recommendation.
- **Optional integrations.** LangChain and MCP extras are adapter-tier
  surfaces. They make the contracts easier to exercise from existing
  tools, but they do not by themselves make an agent's whole memory
  stack poisoning-resistant or enforce governed writes.

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
- [`examples/decay.py`](examples/decay.py) -- freshness scoring on a small bundle of facts; first concrete schema migration.
- [`examples/company_brain_demo.py`](examples/company_brain_demo.py) -- the full 7-stage pipeline (ingest → extract → reduce → cite → access → embed → compile) end to end.
- [`examples/langchain_memory.py`](examples/langchain_memory.py) -- LangChain `BaseMemory` integration for classic chains; records turns as source/episode/evidence trace and returns a legacy `context_pack` session-trace envelope. It does not promote turns into trusted facts or return a full `ContextPack` record.
- [`examples/mcp_server.py`](examples/mcp_server.py) -- expose schema validation, bundle-integrity validation, access-scope evaluation, ContextPack compilation, and compatibility tool aliases over stdio.
- [`examples/poisoning_demo/run.py`](examples/poisoning_demo/run.py) -- one memory-poisoning attack against two stores: a naive extract-append-retrieve store (silently poisoned) and a contracts-governed store (forgery rejected with a receipt). See [Poisoning demo](#poisoning-demo).
- [`examples/arthashila_demo/build.py`](examples/arthashila_demo/build.py) -- the real NBFC dataset through the contracts end to end, with the `--runtime` flag running it through the reference runtime. Skips cleanly if the dataset isn't available.

## What's in the box

- **23 JSON Schemas** (Draft 2020-12) in `src/agent_memory_contracts/schemas/`
- **37 Python modules** of `frozen=True` dataclasses and validators
- **5 bundle validators** that reject the bundle on dangling references,
  non-reciprocal supersession, candidate/ledger field leakage, and
  reducer authorization mismatch
- **10 temporal query helpers** for the taste and state planes
- **Content-derived ID helpers** for every record type, using SHA-256
  of canonical JSON. Same payload = same ID, forever.
- **`record_fingerprint(record)`** for full-record payload digests.
  This separates semantic identity (`id`) from content equality, so
  importers can detect divergent same-id payloads without changing IDs.
- **`bundle_fingerprint(records)`** for content-addressed bundle digests.
  Set-semantic and order-insensitive for distinct semantic ids and
  identical duplicates. Divergent duplicate ids preserve legacy
  last-write-wins behavior by default, with opt-in duplicate modes
  (`last`, `identical`, `raise`).
  Same primitive the id helpers use, applied to the bundle as a whole.
  Useful as a cache key, idempotency token, or change-detection digest.
- **`bundle_diff(a, b)`** for set-semantic diff between two bundles.
  Returns a `BundleDiff(added, removed, changed, unchanged_count)`
  with full pre/post records for the changed entries. It uses
  `bundle_fingerprint` to short-circuit the classification loop when
  both bundles hash equal.
- **`merge_bundles(*bundles, ..., prefer=...)`** for many-to-one
  bundle union. Returns a `BundleMerge(records, conflicts,
  duplicate_ids)`. Records are deduplicated by `id_field`; intra-bundle
  duplicates use `duplicate_mode='last'` by default, and cross-bundle
  conflicts are surfaced for the reducer to triage. `prefer='last'`
  (default) / `'first'` resolve silently, `prefer='raise'` fails loudly.
  Useful for multi-source ingest, bidirectional sync, and backfill.
- **Opt-in `jsonschema` validator** (`pip install agent-memory-contracts[jsonschema]`)
  for polyglot producers that want to validate Python dataclasses
  against the bundled JSON Schemas before the record leaves the
  producing system. Includes `validate_jsonl` and
  `iter_validated_jsonl` for streaming.
- **CLI** (`python -m agent_memory_contracts`) for the non-Python
  use case: validate a JSON or JSONL file against a schema, compute
  a bundle fingerprint, diff two bundles, merge N bundles into one,
  compute a hygiene report, or compute an audit pack. Stdlib
  `argparse`. Optional `--json` flag on every subcommand for
  programmatic consumption.
- **Zero runtime dependencies** (stdlib only)
- **~17,000 lines of Python**, ~600 lines of JSON Schema
- **849 collected tests** (dataset-gated Arthashila cases skip when
  the external corpus is unavailable) covering
  id derivation, contract validation, bundle integrity, temporal
  queries, the bundle fingerprint, diff, and merge primitives, the
  optional JSON Schema validator, the CLI (including `--json` mode),
  the reference reducer, the SQLite-to-contracts migration example,
  conflict resolution, the memory hygiene report, the audit pack,
  the decay primitives, the LangChain and MCP integrations, the
  schema migration framework, the 5 differentiator invariants, the
  reference runtime (store, gate, anchors, grounding), the
  poisoning demo, and the Arthashila demo
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
- **Audit pack** (`compute_audit_pack` /
  `audit_pack_to_markdown`): the chain of custody behind a
  bundle — every trusted ledger entry with its full
  authorization chain (entry → decision → candidates →
  evidence → sources), rejections in the period, and the
  supersession changelog. CLI subcommand `audit <path>`
  produces a Markdown report (or JSON with `--json`).

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
   returns a deterministic SHA-256 of the whole bundle. Unique
   semantic ids and identical duplicate replays are order-insensitive:
   same records in any order, same fingerprint. A bundle is treated
   as a set of records keyed by `id`; divergent duplicate ids preserve
   legacy last-write-wins behavior by default, and can be rejected with
   `duplicate_mode="identical"` or `duplicate_mode="raise"`.

## Poisoning demo

What does the reducer boundary actually buy you? The runnable demo at
[`examples/poisoning_demo/`](examples/poisoning_demo/) attacks two
memory stores with the same forged document (a spoofed-sender payout
memo, the classic BEC shape):

```bash
PYTHONPATH=src python examples/poisoning_demo/run.py
```

- **Store A** ("silent", ~80 lines of the usual architecture:
  extract dict -> append list -> keyword retrieve) swallows the
  forgery and starts serving the attacker's number as truth.
- **Store B** (the same extraction fixtures routed through the real
  contracts) rejects it in the candidate plane -- untrusted source
  tier, no authorizing chain -- and emits a rejection receipt (a
  real `MemoryReducerDecision`). The trusted ledger's
  `bundle_fingerprint` is byte-identical before and after the attack.

Deterministic, no API keys, no network. Outputs a console table and
a generated `report.md`. For the same governance run against a full
synthetic NBFC corpus (30 emails, 5 policy documents, 2 poisoned
documents, a supersession trap, and an Audit Pack export), see
[`examples/arthashila_demo/build.py`](examples/arthashila_demo/build.py)
-- it requires the demo dataset directory (`--dataset`) and answers
the corpus's walkthrough question from the trusted ledger with every
clause cited.

## Audit packs

The audit pack is the report you hand an auditor, a regulator, or a
CFO. Where the hygiene report says what the bundle *looks like*, the
audit pack says how every trusted entry *got there*: it walks each
ledger entry's full authorization chain (entry → authorizing reducer
decision → candidates → evidence spans → sources), collects every
rejected candidate decision with its rationale, and renders the
supersession changelog — all tied to the bundle's
`bundle_fingerprint`. The default title is the promise: *"Every fact
your AI relies on, who authorized it, and the evidence behind it."*

```python
from agent_memory_contracts import compute_audit_pack, audit_pack_to_markdown

pack = compute_audit_pack(records, as_of="2026-06-30T00:00:00Z")
print(pack.complete_chain_count, pack.incomplete_chain_count, pack.rejected_count)
markdown = audit_pack_to_markdown(pack)
```

Broken chains are findings, not errors: a missing decision, a
dangling candidate, or an unresolved span flags the entry
`INCOMPLETE` (with exactly what is missing) instead of raising. The
pack id is content-derived, so the same bundle and `as_of` reproduce
the same pack — an attested pack can be re-verified later. From the
shell:

```bash
python -m agent_memory_contracts audit bundle.jsonl
python -m agent_memory_contracts audit bundle.jsonl --as-of 2026-06-30T00:00:00Z --json
```

## Runtime (reference implementation)

The contracts say what a governed memory record *is*; the
`agent_memory_contracts.runtime` package is a complete, stdlib-only
reference implementation of how a runtime holds and moves them —
sqlite3-backed, no new dependencies:

- **`runtime.store.MemoryStore`** — six-plane persistence with
  immutable payloads, insert-only edge tables, and SQL triggers
  that make silent writes impossible at the schema level.
  Supersession (and retraction) live in edge tables and are
  materialized at read, so the append-only ledger still satisfies
  the library's reciprocity validators — including time travel
  (`active_entries(as_of=...)`: what was true on May 3rd).
- **`runtime.gate.MemoryGate`** — the only write path. Idempotent,
  server-verified ingestion (same payload = no-op; forged id =
  structured `id_mismatch`), and one transactional `promote()`
  that carries a reducer decision + its entries + its
  supersessions as a single validated closure. There is no API
  that writes a ledger entry outside it. Conflicts (double
  promotion, double supersession) resolve first-commit-wins with
  the winning decision id in the error.
- **`runtime.anchors`** — a hash-chained audit anchor on every
  committed batch. `verify_chain()` recomputes every anchor and
  returns the first divergence (edited payload, deleted row,
  reordered history); `verify_coverage()` flags any trusted row no
  anchor accounts for.
- **`runtime.grounding`** — deterministic, receipted ContextPack
  builds (same memory + same request ⇒ byte-identical fingerprint)
  and a cite-or-refuse `answer()`: every factual sentence carries
  a `[Fi]` citation checked *mechanically*, and below-threshold
  support returns a structured refusal with the unreviewed
  candidates that might be relevant. No model calls — the
  reference answerer is a deterministic template, so the whole
  contract is testable.

The five differentiator invariants are executable in
[`tests/invariants/`](tests/invariants/) — no silent writes (fuzzed),
full provenance (chain-walked), reciprocal supersession (raced),
deterministic receipts (tamper-tested), grounded-or-refused
(adversarially checked). To see the runtime run the full NBFC
corpus end to end (poisons quarantined, 12.50% answered with
citations from a receipted pack, audit pack emitted from store
state, anchor chain verified):

```bash
PYTHONPATH=src python examples/arthashila_demo/build.py --runtime --dataset /path/to/05-demo-dataset
```

The runtime is the *semantics*, not the service: product work belongs
in a separate governed-memory wrapper/control-plane repo. That product
can implement a backend/gate/store port around the `StorageBackend`
read surface, expose a service API, wrap existing memory stores in
observe/overlay/enforce modes, and use these invariants as its
acceptance gate. The map is in
[`docs/ROADMAP-to-product.md`](docs/ROADMAP-to-product.md).

## Bundle fingerprint

(Since 0.3.0 — see also [Bundle diff](#bundle-diff) and [CLI](#cli) below.)

Since 0.3.0, the library ships a `bundle_fingerprint` primitive
that hashes a set of records into a single 64-char hex digest.
The same unique records in any order produce the same hash; any
byte change in any effective record changes the hash.

```python
from agent_memory_contracts import bundle_fingerprint, record_fingerprint

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

# Semantic id and full-record payload equality are separate checks.
tampered_record = dict(dataclasses.asdict(records[0]), metadata={"tampered": True})
assert record_fingerprint(records[0]) != record_fingerprint(tampered_record)

# Safe imports can replay identical records but fail on divergent
# same-id payloads instead of silently applying legacy last-write-wins.
safe_digest = bundle_fingerprint(records, duplicate_mode="identical")

# Fully strict imports can reject any repeated semantic id, even an
# identical replay.
strict_digest = bundle_fingerprint(records, duplicate_mode="raise")
```

Use cases: cache key for ContextPack rebuilds, idempotency token
for sync writes, change-detection digest, audit-chain anchor,
duplicate-payload detection.

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
# short-circuits and returns the empty diff without running the
# per-record added/removed/changed classification loop.
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

# Audit pack (authorization chains, rejections, supersessions).
python -m agent_memory_contracts audit bundle.jsonl
python -m agent_memory_contracts audit bundle.jsonl --as-of 2026-06-30T00:00:00Z --json

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
pytest -q                            # 852 collected tests
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
