# Migration guide: from SQLite-style memory to the contracts library

This guide is for teams with an existing agent memory system that
stores memories as key-value rows in SQLite (or a similar generic
store) and want to add **reducer authorization**, **content-derived
IDs**, **schema validation**, and **bundle operations** (fingerprint,
diff, merge) without rewriting from scratch.

The library is a **contracts library**, not a database. It does not
replace your storage layer; it gives you the schema and the validation
that turn "rows in a table" into "a falsifiable memory graph." You
can adopt it incrementally — see [Adopting incrementally](#adopting-incrementally)
for three patterns ordered by leverage.

If you only want the executive summary: the worked migration script
at `docs/migration_example.py` (committed in 0.6.0) is 477 lines, runs
end-to-end, and takes a 3-row synthetic SQLite store through to a
validated contracts JSONL fileset with a stable bundle fingerprint.
Read the script first, then come back here for the "why" and the
adoption trade-offs.


## Contents

1. [Who this is for](#who-this-is-for)
2. [The "before" stack: a synthetic SQLite-based memory](#the-before-stack)
3. [Why that stack is fragile](#why-the-stack-is-fragile)
4. [The "after" stack: contracts-shaped records](#the-after-stack)
5. [Side-by-side: same operation, before and after](#side-by-side)
6. [Adopting incrementally](#adopting-incrementally)
7. [What you give up](#what-you-give-up)
8. [What you gain](#what-you-gain)
9. [Worked end-to-end: `docs/migration_example.py`](#worked-example)
10. [Further reading](#further-reading)


## Who this is for

You have an agent that remembers things. Those things are stored
somewhere — usually a SQLite table with `id, kind, payload, created_at,
source` columns, but the shape doesn't matter much. What matters is
that **the storage layer is generic**: it doesn't know what a "valid"
memory is, doesn't know who authorized it, doesn't know that
preference A was superseded by preference B. It just stores rows.

You want to start enforcing invariants without rewriting the storage
layer. The contracts library gives you:

- **Content-derived record IDs** (SHA-256 of canonical JSON), so the
  same payload always has the same id regardless of where or when it
  was written.
- **Per-plane JSON Schemas** (Draft 2020-12), so the shape of every
  record is checked on the way in.
- **A reducer authorization pattern**: an LLM can extract
  *candidates* (untrusted), but a *reducer* is the only thing that
  can produce *ledger entries* (trusted), and the reducer decision
  is itself a signed record.
- **Bundle operations** — `bundle_fingerprint`, `bundle_diff`,
  `merge_bundles` — so you can ask "did anything change?" with one
  hash compare, audit what changed in the last cycle, or merge
  bundles from multiple sources.
- **A CLI** (`python -m agent_memory_contracts validate | fingerprint
  | diff | merge`) for non-Python callers.

What it does **not** give you:

- A storage engine. You still need a place to persist the records
  (SQLite, JSONL files, S3, your existing DB). The library is
  storage-agnostic.
- An LLM extraction pipeline. The library defines the *shape* of
  extractions, not how to produce them.
- A reducer implementation. The library *requires* a reducer, but
  writing the reducer is the user-facing part. See
  [`examples/reference_reducer.py`](https://github.com/eoniclife/agent-memory-contracts/blob/main/examples/reference_reducer.py)
  for a complete, runnable example with deliberate failure cases.

If you already have all of the above and just need a content-addressed
diff/merge layer, you can adopt the bundle primitives alone without
touching the schemas or the reducer pattern.


## The "before" stack

A realistic minimal SQLite-based agent memory looks roughly like this.
The exact column layout varies, but the shape is typical: one table,
opaque payloads, no schema, no reducer.

```python
import json
import sqlite3
from typing import Any


def add_memory(
    conn: sqlite3.Connection,
    kind: str,
    payload: dict,
    source: str,
) -> int:
    """Insert a memory row. Returns the new SQLite rowid."""
    cur = conn.execute(
        "INSERT INTO memory (kind, payload, created_at, source) "
        "VALUES (?, ?, datetime('now'), ?)",
        (kind, json.dumps(payload), source),
    )
    conn.commit()
    return cur.lastrowid  # an autoincrement integer


def get_memory(conn: sqlite3.Connection, rowid: int) -> dict | None:
    """Load one memory by its SQLite rowid."""
    cur = conn.execute(
        "SELECT id, kind, payload, created_at, source "
        "FROM memory WHERE id = ?", (rowid,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": row[0], "kind": row[1],
        "payload": json.loads(row[2]),
        "created_at": row[3], "source": row[4],
    }


def list_memory(
    conn: sqlite3.Connection, kind: str | None = None
) -> list[dict]:
    """List memories, optionally filtered by kind."""
    if kind is None:
        cur = conn.execute(
            "SELECT id, kind, payload, created_at, source "
            "FROM memory ORDER BY id"
        )
    else:
        cur = conn.execute(
            "SELECT id, kind, payload, created_at, source "
            "FROM memory WHERE kind = ? ORDER BY id", (kind,),
        )
    return [{
        "id": r[0], "kind": r[1], "payload": json.loads(r[2]),
        "created_at": r[3], "source": r[4],
    } for r in cur.fetchall()]
```

This is fine for a system that writes a few memories a day and reads
them by primary key. It is not fine for an agent whose memory becomes
the substrate of its decisions, because the storage layer has no
opinion about what is true.


## Why the stack is fragile

Five concrete failure modes, each with a one-paragraph example. Every
team I have seen hit agent memory at scale has hit at least three of
these.

**1. No content-derived ID.** Two writes of the same payload produce
two different rowids. Deduplication is impossible without an external
canonicalizer; you can't ask "have I seen this preference before?"
without re-implementing the canonicalization in every reader.

```python
# Two writes of the same payload, different rowids, no link.
r1 = add_memory(conn, "preference", {"text": "always read the test first"}, "user")
r2 = add_memory(conn, "preference", {"text": "always read the test first"}, "user")
assert r1 != r2  # the table has no idea these are "the same" memory
```

**2. No schema validation.** Anything that can call `add_memory` can
put garbage in `payload`. A typo in a field name, a missing required
field, a wrong type — all silently accepted. By the time you notice,
the bad data is in your search index, your prompt context, your
rebuild pipeline.

```python
# Both calls succeed; one of them is silently broken.
add_memory(conn, "preference", {"subject": "tests", "text": "..."}, "user")
add_memory(conn, "preference", {"subjet": "tests", "txt": "..."}, "user")  # typos
```

**3. No reducer authorization.** An LLM that calls
`add_memory("preference", {...})` directly writes to "memory" without
a human or policy check. The "preference" tag is just a string;
nothing checks that the agent was authorized to learn this
preference, nothing checks that the payload is consistent with prior
preferences, nothing records *why* this memory was promoted.

```python
# This is the line in your agent that probably exists right now.
add_memory(conn, "preference", llm_extracted_preference_dict, "agent")
# There is no record of who decided this was memory-worthy.
```

**4. No supersession story.** When a preference changes, you need a
separate `superseded_by` column, a separate consistency check, and
discipline to update it. Without reciprocity enforcement, you end up
with chains of `A.superseded_by = B` and `B.supersedes = A` that
drift apart over months.

```python
# Two updates, only one side of the supersession link is recorded.
add_memory(conn, "preference", {"text": "old rule"}, "user")
add_memory(conn, "preference", {"text": "new rule", "supersedes": "old rule"}, "user")
# But "old rule" still has superseded_by=None. The link is one-way.
```

**5. No bundle integrity.** You can't ask "give me the SHA-256 of
the whole memory set" without writing a serializer yourself. You
can't ask "what changed since yesterday?" without a diff. You can't
backfill from a new source without re-running the whole extraction
and trusting the result.

```python
# To detect a change, you have to compare every row pairwise.
old = list_memory(conn, "preference")
new = list_memory(conn, "preference")
added = [r for r in new if r["id"] not in {x["id"] for x in old}]
removed = [r for r in old if r["id"] not in {x["id"] for x in new}]
# And this only works because the ids happen to be SQLite rowids;
# change a payload, the id stays the same, your "diff" misses it.
```


## The "after" stack

The same conceptual system, routed through the contracts library.
The shape of every record is enforced; the id is the unit of
identity; the reducer is the only path from candidate to ledger.

```python
from agent_memory_contracts import (
    SourceRecord, EvidenceSpan, CandidateClaim,
    MemoryReducerDecision, PreferenceLedgerEntry,
    make_source_id, make_span_id, make_candidate_id,
    make_reducer_decision_id, make_ledger_entry_id,
    validate_ledger_bundle,
)


def promote_candidate_to_memory(
    *,
    candidate: CandidateClaim,
    evidence_spans: list[EvidenceSpan],
    source_records: list[SourceRecord],
    reducer_decision: MemoryReducerDecision,
) -> PreferenceLedgerEntry:
    """The reducer has already authorized this; persist the result.

    Returns a fully-typed PreferenceLedgerEntry. The id is content-
    derived from the semantic payload, so calling this twice with
    the same inputs returns an equivalent entry.
    """
    span_ids = [s.id for s in evidence_spans]
    lid = make_ledger_entry_id("preference", span_ids, {
        "ledger_type": "preference",
        "subject": candidate.subject,
        "preference_text": candidate.natural_language_summary,
        "domain": candidate.domain,
        "scope": "global",
        "valid_from": reducer_decision.decided_at,
        "evidence_span_ids": span_ids,
    })
    return PreferenceLedgerEntry.from_dict({
        "id": lid, "schema_version": "1.0.0",
        "ledger_type": "preference", "status": "active",
        "confidence": reducer_decision.confidence,
        "scope": "global",
        "source_record_ids": [s.id for s in source_records],
        "episode_record_ids": [],
        "evidence_span_ids": span_ids,
        "candidate_ids": [candidate.id],
        "reducer_decision_id": reducer_decision.id,
        "subject": candidate.subject,
        "preference_text": candidate.natural_language_summary,
        "domain": candidate.domain,
        "strength": "hard_constraint",
        "observed_at": candidate.extracted_at,
        "asserted_at": reducer_decision.decided_at,
        "valid_from": reducer_decision.decided_at,
        "valid_until": None, "stale_after": None,
        "created_at": reducer_decision.decided_at,
        "updated_at": reducer_decision.decided_at,
        "supersedes": [], "superseded_by": [], "metadata": {},
    })


def validate_graph(
    source_records, evidence_spans, candidate_records,
    reducer_decisions, ledger_entries,
) -> None:
    """Cross-plane integrity check. Raises ValueError on any problem.

    > NOTE: this is the only place you need to call `validate_*_bundle`
    > in production code. The bundle validator is the *contract* —
    > everything else (id derivation, schema validation) is enforced
    > by the typed classes themselves.
    """
    validate_ledger_bundle(
        source_records=source_records,
        episode_records=[],
        evidence_spans=evidence_spans,
        candidate_records=candidate_records,
        reducer_decisions=reducer_decisions,
        ledger_entries=ledger_entries,
    )
```

> NOTE: the typed classes (`PreferenceLedgerEntry.from_dict(...)`,
> `EvidenceSpan.from_dict(...)`, etc.) refuse to instantiate if the
> payload is missing required fields or has the wrong type. The schema
> validation happens at construction, not at the storage boundary.


## Side-by-side

Five common operations, before and after.

| Operation | Before (SQLite) | After (contracts) |
| --- | --- | --- |
| **Append a memory** | `add_memory(conn, "preference", payload, "user")` returns an integer rowid | `promote_candidate_to_memory(...)` returns a typed `PreferenceLedgerEntry` whose id is `sha256(...)` of the semantic payload |
| **Look up by id** | `get_memory(conn, 47)` — looks up by SQLite rowid | `get_ledger_entry("pref_5a3b...")` — looks up by content-derived hex id; the id encodes what the record *is*, not where it sits in the table |
| **List current preferences** | `list_memory(conn, "preference")` returns every row tagged "preference" regardless of validity window | `current_ledger_entries(ledger_type="preference")` (or the equivalent for the state plane) returns only entries where `now in [valid_from, valid_until]` |
| **Replace a preference** | Two writes; manual `superseded_by` bookkeeping; nothing enforces the link | One new entry with `supersedes=[old_id]` + `superseded_by=[new_id]`; the bundle validator checks reciprocity and temporal ordering |
| **Audit: "what changed in the last cycle?"** | Diff every row pairwise; easy to miss content changes that don't change the rowid | `bundle_diff(cycle_n, cycle_n_plus_1)` — set-semantic, content-sensitive, returns `added/removed/changed/unchanged_count` with full pre/post records |

The "before" column is 30-50 lines of generic DB code. The "after"
column is 60-100 lines of contracts-shaped code, but every line
*means* something: every record has a type, every cross-plane
reference is checked, every change is content-addressed.


## Adopting incrementally

You do not have to migrate everything at once. Three patterns,
ordered by leverage.

### Pattern 1: Library alongside (lowest risk)

Keep SQLite as the primary store. **Mirror every write through the
contracts library** to compute the content-derived id and validate
the shape. Your existing readers don't change; you can drop the
mirror if it gets in the way.

```python
def add_memory_with_contract_mirror(conn, kind, payload, source):
    # 1. Write to SQLite as before.
    rowid = add_memory(conn, kind, payload, source)

    # 2. Compute the content-derived id and validate the shape.
    #    If this raises, the SQLite write already happened — the
    #    mirror is a write-time assertion, not a gate.
    try:
        contracts_id = compute_contracts_id(kind, payload)
    except (ValidationError, ValueError):
        log.warning(f"rowid {rowid} failed contracts validation: ...")
        return rowid

    # 3. Optionally store the contracts id alongside the rowid.
    conn.execute("UPDATE memory SET contracts_id = ? WHERE id = ?",
                (contracts_id, rowid))
    conn.commit()
    return rowid
```

This is the **first step** most teams should take. It gives you the
content-derived id (so future dedup works), the schema check (so bad
data is caught at write time), and zero risk to the existing system.
The only cost is one extra `UPDATE` per write.

### Pattern 2: Library as the schema

Stop using the SQLite rowid as the canonical id. Use the
**content-derived contracts id as the primary key** in SQLite; the
rowid becomes an internal optimization.

```sql
CREATE TABLE memory (
    contracts_id TEXT PRIMARY KEY,  -- sha256 hex, content-derived
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,           -- canonical JSON
    created_at TEXT NOT NULL,
    source TEXT NOT NULL
);
```

In Python, every `INSERT` becomes "compute the contracts id, then
insert — or `INSERT OR IGNORE` if the id is already there." Reads
become "look up by contracts id." Writes are inherently idempotent:
two writes of the same payload collapse to one row, with no extra
code.

```python
def add_memory_idempotent(conn, kind, payload, source):
    contracts_id = compute_contracts_id(kind, payload)
    conn.execute(
        "INSERT OR IGNORE INTO memory "
        "(contracts_id, kind, payload, created_at, source) "
        "VALUES (?, ?, ?, datetime('now'), ?)",
        (contracts_id, kind, json.dumps(payload), source),
    )
    conn.commit()
    return contracts_id
```

This is the **biggest practical win** in most migrations. Idempotent
writes mean the "did I already learn this?" question is free.

### Pattern 3: Library as the truth (highest leverage)

Drop SQLite for memory storage. Persist records as **JSONL files per
plane** (sources, spans, candidates, reducer_decisions, ledger,
taste, state) and use the bundle primitives for everything else.
This is the pattern used by the library's own examples and by
[`docs/migration_example.py`](#worked-example).

```python
import json
from pathlib import Path
from agent_memory_contracts import (
    bundle_fingerprint, bundle_diff, merge_bundles,
)


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w") as fh:
        for r in records:
            fh.write(json.dumps(r, sort_keys=True) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# Write
write_jsonl(Path("ledger.jsonl"), ledger_entries)

# Fingerprint: one hash compare to detect "did anything change?"
digest = bundle_fingerprint(read_jsonl(Path("ledger.jsonl")))

# Diff: what changed between this cycle and last?
changes = bundle_diff(read_jsonl(Path("ledger.cycle_n.jsonl")),
                     read_jsonl(Path("ledger.cycle_n_plus_1.jsonl")))

# Merge: combine bundles from multiple sources with conflict surfacing
merged = merge_bundles(local_records, remote_records, prefer="last")
```

This is the **highest-leverage move** but also the most invasive.
The bundle primitives make backups (copy the JSONL), diffs (one
function call), and merges (one function call) tractable. You give
up the ability to do `UPDATE memory SET payload = ...` directly
(updates are "supersede the old, write the new"), but you gain a
storage layer that you can grep, version-control, and reason about
line by line.


## What you give up

Three honest trade-offs.

- **Direct UPDATEs.** Content-derived ids mean updates are
  "supersede the old, write the new." If your existing system uses
  `UPDATE memory SET payload = ...` to evolve a memory in place, the
  contracts version requires a supersession link (or a different id
  on the new record, with the old one marked superseded).

- **Ad-hoc schema flexibility.** The contracts are strict, on
  purpose. Every plane has a JSON Schema; every typed class refuses
  to instantiate invalid input. If your current system lets you
  store arbitrary fields per row (a common pattern for
  "fast iteration"), the contracts version will reject the
  arbitrary fields. The fix is to either drop the fields or
  promote them into the schema (which is a real change, not a
  renaming).

- **The reducer step is the hardest part to retrofit.** The
  contracts library *requires* a reducer decision to authorize every
  ledger entry, taste card, and state snapshot. If your existing
  system just has an LLM writing to memory, you now need to write
  the part that decides *which* LLM outputs become memory and on
  what evidence. See
  [`examples/reference_reducer.py`](https://github.com/eoniclife/agent-memory-contracts/blob/main/examples/reference_reducer.py)
  for a complete reducer with three worked scenarios (happy path,
  rejections, validator enforcement). It is ~1000 lines because
  the reducer is doing real work; the test of whether a reducer is
  correct is whether it surfaces the right rejections.


## What you gain

From the A- to A assessment of v0.6.0:

- **Same payload → same id, forever.** Two systems independently
  processing the same evidence can verify they agree by comparing
  ids. No assigned UUIDs, no "did this already exist?" guesses.

- **Bundle fingerprint is one hash compare.** Re-running the same
  pipeline on the same inputs produces the same digest. A
  downstream write layer can dedupe on the hash. A rebuild can
  check "did anything change?" with `bundle_fingerprint(records) ==
  old_digest`.

- **The reducer authorization pattern is enforced in the type
  system.** A `CandidateClaim` is physically a different type from
  a `FactLedgerEntry`; the only path from one to the other is
  through a reducer decision. The validator refuses to instantiate
  a ledger entry whose reducer doesn't authorize it.

- **Supersession is a graph, not a flag.** If B supersedes A, both
  sides have to point at each other. The bundle validator checks
  reciprocity and temporal ordering at validation time. No
  one-way supersession chains drifting apart.

- **The CLI is a complete programmatic surface.** A Node, Go, or
  Rust pipeline can call
  `python -m agent_memory_contracts validate foo.json --schema taste_card --json`
  and get back a structured `{"ok": true, "errors": []}` (or a
  parseable error). The CLI uses the same public Python API as the
  library, so anything you can do from Python you can do from the
  shell.

- **Falsifiable history.** "Did this rebuild change anything?" is
  a single hash compare. "What changed in the last cycle?" is a
  single function call (`bundle_diff`). "Do these two systems
  agree?" is a per-record id compare. Every question about the
  state of memory has a deterministic answer.


## Worked example

[`docs/migration_example.py`](./migration_example.py) is a 477-line
script that takes a synthetic 3-row SQLite store through to a
validated contracts JSONL fileset. It runs end-to-end with no
network, no LLM call, no randomness:

```bash
PYTHONPATH=src python3 docs/migration_example.py
```

Output (abbreviated):

```
[1] Read 3 rows from the synthetic SQLite store
    - row  1  kind=preference   created_at=2026-05-01T09:30:00Z
    - row  2  kind=preference   created_at=2026-05-01T09:31:00Z
    - row  3  kind=decision     created_at=2026-05-01T09:45:00Z

[3] Pre-validation dedup: rows with identical payloads
    produce identical ids; the bundle validator rejects
    duplicates, so we collapse by id first (last-write-wins).
    sources:        - 0
    spans:          - 0
    candidates:     - 0
    reducers:       - 0
    ledgers:        - 1

[4] Validating the ledger bundle
    OK -- all cross-plane references resolve, reducer decisions
    authorize their entries

[5] Writing JSONL fileset to /tmp/migrated_contracts/
    - sources.jsonl               2 records
    - spans.jsonl                 2 records
    - candidates.jsonl            2 records
    - reducer_decisions.jsonl     3 records
    - ledger.jsonl                2 records

[6] Computing bundle fingerprint
    fingerprint = d7447e1ecb90b3e56263032fe51fe35f2daa411c65aed81ef0a9b16159e07a62

[7] Deduplication report: 3 SQLite row(s) -> 2 distinct ledger id(s)
    ledger pref_11d91d06cc8ef3ef9fb..  <-  SQLite rows [1, 2]
```

Three observations from the output:

- **The dedup story works.** Rows 1 and 2 have identical payloads,
  so they collapse to one ledger id (`pref_11d91d06...`). The
  script records the mapping in `migrated_from_rowid` metadata so
  the original SQLite rowids are still queryable.

- **The reducer decision is fabricated.** SQLite has no concept of
  a reducer, so the script generates a synthetic
  `MemoryReducerDecision` for each row with all five reducer checks
  marked `"pass"`. A real migration would either: (a) skip
  candidates that have no real reducer backing and leave them as
  candidates only, or (b) have a human sign off on each one. The
  example shows option (b) with the synthetic rationale.

- **The fingerprint is the same on every run.** Because the ids
  are content-derived and the dedup is deterministic, the bundle
  fingerprint of the migrated fileset is stable. Re-run the
  script, get the same hash. That's the falsifiability claim in
  one line.

The script is a worked example, not a production migration tool. A
real migration would handle: schema evolution across versions,
partial migrations, rollback, real reducer implementations, and
probably streaming writes (the example writes everything to memory
then dumps at the end). But the structural pattern — content-derived
ids, reducer authorization, bundle validation, JSONL fileset output —
is the same.


## Further reading

- [`README.md`](https://github.com/eoniclife/agent-memory-contracts/blob/main/README.md) — the entry point. Quickstart, "what's in the box", CLI examples.
- [`docs/architecture.md`](https://github.com/eoniclife/agent-memory-contracts/blob/main/docs/architecture.md) — the design document. Why the six-plane model, why the reducer is the only path from candidate to ledger, why supersession is a graph.
- [`examples/quickstart.py`](https://github.com/eoniclife/agent-memory-contracts/blob/main/examples/quickstart.py) — minimal end-to-end example. Source → span → candidate → reducer → ledger, then `validate_ledger_bundle`.
- [`examples/reference_reducer.py`](https://github.com/eoniclife/agent-memory-contracts/blob/main/examples/reference_reducer.py) — complete reference reducer with three worked scenarios (added in 0.6.0). The hard part of any contracts-based system, in runnable form.
- [`examples/extract_taste_cards.py`](https://github.com/eoniclife/agent-memory-contracts/blob/main/examples/extract_taste_cards.py) — synthetic transcript → multiple taste cards, with positive/negative example grounding. The taste plane worked end-to-end.
- [`benchmarks/`](https://github.com/eoniclife/agent-memory-contracts/blob/main/benchmarks/) — stdlib-only benchmark suite for `bundle_fingerprint`, `bundle_diff`, `merge_bundles` at 100/1k/10k/50k records (added in 0.6.0). Use it to characterize the performance envelope on your data.
- [`CHANGELOG.md`](https://github.com/eoniclife/agent-memory-contracts/blob/main/CHANGELOG.md) — version history. The v0.6.0 release is the "polish + examples" release; v0.5.0 added the `merge_bundles` primitive; v0.4.0 added the CLI.
