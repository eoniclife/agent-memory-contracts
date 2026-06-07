# Sprint 27 / v1.1.0: Decay Primitives

**Status:** planned
**Target version:** `1.1.0` (minor; backwards-compatible, additive)
**Depends on:** v1.0.2 (commit `1dea695`)
**Spec author:** Mavis (best-judgment draft)
**Spec written:** 2026-06-07

## Why this sprint

The library currently has a sharp `exclude_stale` boolean on
`CompilationPolicy` (from v1.0.0a3) that excludes records
whose `stale_after` is in the past. That's a binary
include/exclude — useful, but coarse.

A more honest model is **decay**: a record's freshness
decreases continuously over time and/or with events, and
the compiler uses the freshness score (0.0–1.0) to weight
records in the context_pack. Records with freshness 0.0
are dropped; records with freshness 1.0 are kept;
records in between are selected by the existing
`selection_strategy` ("recent" / "diverse" / "frequent")
weighted by freshness.

This is the natural next step. It also registers the
**first concrete migration** in `default_migrator()`:
v1.1.0 adds an optional `freshness_score` field to ledger
entries and taste cards, and the migration step backfills
the field for existing records.

## What this sprint is not

- **Not a re-implementation of TTL.** Records do not
  "expire" on a hard schedule. Decay is continuous, and
  the compiler uses the score.
- **Not a re-implementation of the v0.8.0
  supersession chain.** Supersession is a discrete
  state change (this fact replaces that fact); decay is
  a continuous signal.
- **Not a vector store / semantic similarity.** Decay is
  a temporal / event-based signal; semantic similarity
  is a v1.1.0+ consideration.
- **Not an automatic re-decay job.** The library is
  not a service. Decay is computed on read (at
  `compile_context_pack` time), not on a schedule.

## Architecture

```
┌──────────────────────┐
│ compile_context_pack │
│ (v1.0.0a3)           │
└──────────┬───────────┘
           │ computes freshness_score
           ▼
┌──────────────────────┐
│ DecayPolicy + apply  │  ◄── this sprint
│ decay                │
└──────────┬───────────┘
           │ uses
           ▼
┌──────────────────────┐
│ MigrationStep        │  ◄── first registered
│ (v1.0.0a2)           │      migration: backfills
│                      │      freshness_score
└──────────────────────┘
```

`DecayPolicy` is a configuration object. `apply_decay` is
the headline function. The compiler calls `apply_decay`
during selection to weight records.

## Public API

| Name | Module | Description |
| --- | --- | --- |
| `DecayPolicy` | `decay` | Configuration: half-life, event weights |
| `DecayScore` | `decay` | The freshness score: 0.0 (stale) to 1.0 (fresh) |
| `apply_decay` | `decay` | Compute the score for a record at a time |
| `default_decay_policy` | `decay` | The default policy |

`decay` is a new module. It does not modify any existing
records; it only reads them.

## Schema migration (the v1.1.0 schema bump)

This sprint bumps `SCHEMA_VERSION` from `"1.0.0"` to
`"1.1.0"` and registers the first concrete migration in
`default_migrator()`:

- **Migration step:** `add_freshness_score_field`
- **What it does:** For every `FactLedgerEntry`,
  `PreferenceLedgerEntry`, `DecisionLedgerEntry`,
  `TasteCard`, `ProjectStateSnapshot`, and
  `CoreStateSnapshot`, add an optional
  `freshness_score: float | None` field. Default to `None`
  (i.e., "freshness not yet computed"). The compiler
  computes the score on read.
- **Backwards compatibility:** Records without the
  field are treated as if the field is `None`. The
  compiler falls back to the v1.0.0 binary
  `exclude_stale` logic when freshness is not available.

This is the first time the library actually uses the
migration framework shipped in v1.0.0a2.

## Dependencies

- None. The decay module is stdlib-only.

## Test plan

- `tests/test_decay.py` with ~15 tests:
  - 3 tests for `DecayPolicy` construction and validation.
  - 4 tests for `apply_decay` with time-based decay (linear
    and exponential).
  - 3 tests for `apply_decay` with event-based decay
    (count of new evidence, count of supersedes).
  - 2 tests for the migration step
    (`add_freshness_score_field`).
  - 3 tests for the integration with `compile_context_pack`:
    a v1.0.0 bundle with no freshness field still compiles;
    a v1.1.0 bundle with freshness scores compiles
    weighted by score; a record with `freshness_score=0.0`
    is dropped.
  - 1 test for the audit script
    (`scripts/audit_public_api.py`) — the new public
    names are listed in `STABILITY.md`.

## Example

`examples/decay.py` shows a 30-line example: a bundle of
facts with `asserted_at` timestamps, compiled with a
`DecayPolicy(half_life_days=30)`. The compiler returns
the facts weighted by freshness — recent facts are
selected first; old facts are dropped or down-weighted.

## Decisions applied to this sprint

### Small defaults

1. **Default `DecayPolicy(half_life_days=90)`.** Three
   months is a reasonable default for personal /
   company-brain use. 30 days is too aggressive; 1 year
   is too lax.
2. **Default decay curve: exponential.** Linear decay is
   too abrupt (a fact goes from "fresh" to "stale" in a
   single half-life). Exponential decay is smoother.
3. **Default event weights: `supersession=0.5`,
   `new_evidence=0.1`.** A supersession (v0.8.0+) means
   the record is replaced; a new evidence is incremental.
4. **`freshness_score` is computed at read time.** Not
   persisted; not cached. The score depends on
   `as_of` and the current bundle, both of which are
   read-time inputs.
5. **Records without `asserted_at` get freshness 1.0**
   (i.e., "always fresh"). This is a defensive default
   for malformed records; the migration step in v1.1.0
   does NOT synthesize an `asserted_at` for old records.
6. **`freshness_score` is optional in the schema.** A
   record with the field set to `None` is treated as
   "not yet computed"; the compiler falls back to the
   v1.0.0 logic.
7. **`compile_context_pack` defaults to no decay.** The
   v1.0.0 behavior is preserved by default; users opt
   in by passing `decay_policy=DecayPolicy(...)`.
8. **Decay is monotonic.** Once a record is at 0.0, it
   stays there until the bundle is changed. Decay does
   not "regenerate" freshness.
9. **Decay is per-record-type.** A `FactLedgerEntry` and
   a `TasteCard` can have different half-lives; the
   policy is global but the type affects the read.

### Bigger defaults

10. **`SCHEMA_VERSION` bumps from `"1.0.0"` to `"1.1.0"`.**
    This is the first schema bump since the v1.0.0
    stability commitment. The migration framework
    (v1.0.0a2) is the safety net; v1.0.0 records are
    still readable.

11. **The migration step is a no-op for the field
    itself; it only adds the optional key.** Existing
    records do not get a computed `freshness_score`;
    the field is added with a `None` value. The compiler
    handles the `None` case gracefully. This avoids the
    cost of a one-time migration over potentially
    millions of records.

12. **Decay is additive to the existing
    `selection_strategy`.** The existing
    `recent` / `diverse` / `frequent` strategies
    continue to work; decay is an additional weight
    multiplied into the score. This is the safest
    possible integration: v1.0.0 bundles are
    unaffected; v1.1.0 bundles with decay enabled
    use the new weight.

## Implementation outline

```python
# src/agent_memory_contracts/decay.py

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class DecayPolicy:
    half_life_days: float = 90.0
    supersession_weight: float = 0.5
    new_evidence_weight: float = 0.1
    curve: str = "exponential"  # "exponential" or "linear"


@dataclass(frozen=True)
class DecayScore:
    score: float  # 0.0 to 1.0
    time_component: float
    event_component: float
    half_life_days: float


def apply_decay(
    record: dict[str, Any],
    policy: DecayPolicy,
    as_of: str,
) -> DecayScore:
    """Compute the freshness score for a record at a time.

    Returns a DecayScore with the overall score and the
    time + event components separately for inspection.
    """
    ...


def default_decay_policy() -> DecayPolicy:
    return DecayPolicy()
```

The exact code is in the implementation step. The spec
is the shape, not the body.

## Out of scope for v1.1.0

- Vector store integration (separate sprint).
- LLM-based decay scoring (e.g., "this fact is
  semantically obsolete because of new context") is a
  v1.2.0+ consideration.
- Per-record decay policies (the policy is global
  per-compile, not per-record). v1.2.0+ consideration.
- Persistent freshness scores. The score is
  computed on read; persisting it would couple
  decay to the storage layer.
- Decay-aware merge. When merging two bundles
  with different freshness scores, the v0.5.0
  merge logic does not consider decay. v1.2.0+
  consideration.

## Definition of done

- [ ] `src/agent_memory_contracts/decay.py` implemented
      per the outline.
- [ ] `SCHEMA_VERSION` bumped from `"1.0.0"` to `"1.1.0"`
      in the relevant places (the constant + JSON
      Schemas).
- [ ] Migration step `add_freshness_score_field`
      registered in `default_migrator()`.
- [ ] 23 JSON Schemas updated to include the
      `freshness_score` field (optional, default `null`).
- [ ] `tests/test_decay.py` with ~15 tests, including
      a v1.0.0 → v1.1.0 migration test.
- [ ] `examples/decay.py` runs end-to-end.
- [ ] `docs/STABILITY.md` updated with the 4 new public
      names + the v1.1.0 schema note.
- [ ] `CHANGELOG.md` updated with the v1.1.0 section.
- [ ] `docs/specs/DECISIONS.md` updated with the v1.1.0
      entry.
- [ ] All 534 existing tests still pass; ~15 new tests
      pass.
- [ ] `mypy --strict` clean.
- [ ] `scripts/audit_public_api.py` passes.
- [ ] Commit, push.

## Bottom line

This sprint ships the temporal/event decay primitive the
library has needed since v0.8.0. It's also the first real
test of the schema migration framework: the field is
optional, the migration is a no-op for the field, and
v1.0.0 records continue to work unchanged. The
`SCHEMA_VERSION` bump is the proof that the v1.0.0
stability commitment is honest, not theoretical.
