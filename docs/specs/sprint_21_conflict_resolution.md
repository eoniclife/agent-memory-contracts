# Sprint 21 / v0.7.0 spec: conflict resolution + memory hygiene report

**Status:** awaiting your review. I'll start implementation after
you sign off. Open questions are listed at the bottom; please
answer them inline (or say "use your judgment").

**Branching decision:** staying on `main`. The current cadence
(`feat:` commits + `release:` commit + tag + GitHub Release) gives
you the same review surface as a PR-based flow with less ceremony.
If you want feature branches + PRs from this sprint onwards, say
so and I'll switch — but my read is that solo-founder + spec
review is the cheaper path to the same outcome.

---

## Problem

The library has `merge_bundles(*bundles, prefer=...)` (v0.5.0) which
*surfaces* content conflicts across input bundles, and a `conflicts`
field on the result. What it does NOT have:

1. A way to *resolve* a conflict and persist the resolution as an
   auditable record.
2. A way to *apply* a set of resolutions to a bundle, producing a
   new bundle where the chosen versions are canonical and the
   rejected versions are linked back to the resolution.
3. A way to *report* on the overall health of a memory system:
   supersession rate, conflict count, stale-record count, etc.

A company-brain product needs all three. Without them, conflicts
are sticky notes and "how is our memory doing?" is a SQL query that
takes an afternoon to write.

This sprint ships the three primitives above as library-level
public API, plus a CLI subcommand and a worked example.

---

## What's in this sprint

### New module: `src/agent_memory_contracts/conflict.py`

#### `ConflictResolution` (frozen dataclass)

Audit record for a single resolved conflict. Same shape conventions
as the rest of the library (frozen dataclass, content-derived id,
`schema_version: "1.0.0"`, `from_dict`/`to_dict`).

```python
@dataclass(frozen=True)
class ConflictResolution:
    id: str                          # confres_<sha256 hex>
    schema_version: str              # "1.0.0"
    conflict_id: str                 # the id of the original
                                     # conflict (typically the
                                     # content-derived id of the
                                     # first version, or a
                                     # derived id if the conflict
                                     # spans multiple records)
    chosen_version_index: int        # 0-based index into the
                                     # original conflict's variant
                                     # list. -1 means "merge",
                                     # -2 means "split".
    chosen_record: dict | None       # the chosen version (full
                                     # record), OR None for split
                                     # resolutions
    rejected_record_ids: list[str]   # the ids of the rejected
                                     # versions, for audit
    resolved_by: str                 # the author of the
                                     # resolution — human name,
                                     # agent name, or system id
    resolved_at: str                 # ISO 8601 UTC, e.g.
                                     # "2026-06-06T12:00:00Z"
    rationale: str                   # required, min 10 chars
    superseded_at: str | None        # if this resolution is
                                     # later superseded, the
                                     # timestamp; None if active
    metadata: dict                   # free-form
```

The id is content-derived from the canonical JSON of all fields
(including `chosen_record`'s canonical JSON), prefixed with
`confres_`. This means: same conflict, same resolution, same id.

#### `resolve_conflict(conflict, chosen, *, resolved_by, rationale, resolved_at=None)`

Builds a `ConflictResolution`. Returns the new record.

`conflict` is a single entry from `BundleMerge.conflicts`:
`(id, [(bundle_index, record), ...])`.

`chosen` is one of:
- An `int` (0-based) — picks that version as the winner. `chosen_record`
  is a deep copy of the variant.
- The string `"merge"` — produces a synthetic merged record combining
  the variants (last-write-wins per field across the variants). The
  `chosen_record` is the synthetic merged dict. `chosen_version_index`
  is `-1`.
- The string `"split"` — produces a resolution with `chosen_record=None`
  and `rationale` explaining why neither version wins (e.g., "the two
  records refer to different things despite sharing an id; kept both
  as separate memories with distinct ids"). `chosen_version_index`
  is `-2`.

`resolved_by` is required (non-empty). `rationale` is required
(non-empty, min 10 chars). `resolved_at` defaults to the current
UTC time in ISO 8601.

**Raises:**
- `ValueError` if `chosen` is not int / `"merge"` / `"split"`
- `ValueError` if int and out of range for the variant list
- `ValueError` if `"merge"` and the variant list is empty
- `ValueError` if `resolved_by` is empty
- `ValueError` if `rationale` is empty or shorter than 10 chars
- `ValueError` if `resolved_at` is provided but not valid ISO 8601

#### `apply_resolutions(bundle, resolutions, *, now=None) -> list[dict]`

Applies a list of resolutions to a bundle. Returns the new bundle.

For each resolution:
- The chosen record (or the synthetic merged record) **replaces** the
  first matching variant in the bundle by id. If the chosen record
  has the same id as the original variant, it's a same-id replacement.
  If the chosen record has a different id (e.g., from `"merge"`
  producing a new id, or the original variants have synthetic ids),
  the chosen record is added to the bundle and the original variants
  get a `superseded_by` field pointing at the resolution's id.
- The **rejected** records stay in the bundle but get a new field
  `superseded_by_conflict_resolution: str` (the resolution id) and
  the existing `superseded_by: list[str]` field is updated to include
  the resolution id.
- A **chain field** is added to the chosen record:
  `resolved_conflict_ids: list[str]` (the conflicts this resolution
  closed). This is the audit trail in the bundle itself — you can
  answer "where did this record come from?" by walking
  `resolved_conflict_ids` → `ConflictResolution.id` →
  `rejected_record_ids` → the variants.

`now` defaults to the current UTC time; it's the timestamp written
into the updated `superseded_at` field on the rejected records.

Returns a new bundle list. The input is not mutated.

**Raises:**
- `ValueError` if any resolution's `conflict_id` is not findable
  among the bundle's records (i.e., the resolution refers to a
  conflict that has no matching records in the bundle)
- `ValueError` if any resolution's `chosen_record.id` (when not
  None) does not appear in the original conflict's variant ids
  AND the resolution is not a `"merge"` or `"split"`

#### `validate_resolutions(bundle, resolutions) -> list[str]`

Returns a list of error messages (empty list = all resolutions are
consistent with the bundle). Does NOT raise; the caller decides
what to do with errors. Useful for product UIs that show
validation issues inline.

Validates:
- Each `ConflictResolution.conflict_id` corresponds to a real
  conflict in the bundle (i.e., the variant records are present)
- Each `ConflictResolution.chosen_record.id` (when not None) is
  one of the variant ids in the conflict
- No two resolutions in the input list target the same
  `conflict_id` (a conflict is resolved at most once; if you want
  to override a resolution, the right primitive is to create a
  new resolution that supersedes the old one)
- Each `resolved_at` is valid ISO 8601

### New module: `src/agent_memory_contracts/hygiene.py`

#### `MemoryHygieneReport` (frozen dataclass)

Snapshot of a memory system's health over a time window.

```python
@dataclass(frozen=True)
class MemoryHygieneReport:
    id: str                          # hygiene_<sha256 hex>
    schema_version: str              # "1.0.0"
    bundle_fingerprint: str          # the SHA-256 of the bundle
                                     # this report was computed on
                                     # (links the report to the
                                     # exact bundle it describes)
    window_start: str                # ISO 8601 UTC
    window_end: str                  # ISO 8601 UTC
    computed_at: str                 # ISO 8601 UTC, the time the
                                     # report was computed
    total_records: int               # total record count in the
                                     # bundle
    records_by_plane: dict[str, int]  # plane name -> count
    records_by_type: dict[str, int]   # ledger_type / candidate_type
                                       # -> count (for planes that
                                       # have a type field)
    records_by_privacy: dict[str, int] # privacy_class -> count
    active_count: int               # records in their valid window
    stale_count: int                 # records past their
                                     # stale_after
    expired_count: int               # records past their
                                     # valid_until
    superseded_count: int            # records with a non-empty
                                     # superseded_by list
    conflicts_surfaced_count: int    # from a bundle_diff or
                                     # merge_bundles result if
                                     # provided
    conflicts_resolved_count: int    # from the audit trail if
                                     # available
    records_with_missing_evidence: int   # ledger / taste / state
                                          # entries with empty
                                          # evidence_span_ids
    records_with_orphan_evidence: int    # records whose
                                          # evidence_span_ids point
                                          # at spans not in the
                                          # bundle
    metadata: dict
```

Id derivation: `sha256` of the canonical JSON of all input fields
(including the bundle fingerprint, window, computed_at), prefixed
with `hygiene_`. So the same bundle + same window + same
`computed_at` produces the same report id; re-running the report
gives a new id (because `computed_at` shifts) but otherwise
identical fields.

#### `compute_hygiene_report(bundle, *, window_start=None, window_end=None, now=None, conflicts=None) -> MemoryHygieneReport`

Compute a report for a bundle over a time window.

`window_start` and `window_end` default to the bundle's full time
range (the earliest and latest ISO timestamps found in the records,
or to the current UTC time if the bundle has no timestamps). `now`
defaults to the current UTC time and is used to determine
"active / stale / expired" relative to the bundle's temporal
fields (`valid_from`, `valid_until`, `stale_after`).

`conflicts` is an optional dict that augments the report's
conflict counts. Shape:
```python
{
    "surfaced": int,   # from bundle_diff or merge_bundles
    "resolved": int,   # from the audit trail (count of
                       # ConflictResolution records in the
                       # resolved window)
}
```

The function reads the bundle, computes the counts, and returns a
`MemoryHygieneReport`. It does NOT mutate the bundle.

**Raises:**
- `ValueError` if `window_start` or `window_end` is provided but
  not valid ISO 8601
- `ValueError` if `window_start > window_end`
- `TypeError` if any record in the bundle is not a dict

#### `hygiene_report_to_markdown(report) -> str`

Format a `MemoryHygieneReport` as a Markdown table. Used by the CLI
subcommand. The output is a single Markdown document with:
- A one-line summary (total records, window, fingerprint)
- A "By plane" table
- A "By type" table (omitted if no typed planes are present)
- A "By privacy" table
- A "Temporal" table (active, stale, expired, superseded)
- A "Conflicts" line (if `conflicts_surfaced_count > 0`)
- An "Evidence integrity" line (missing + orphan counts)
- A footer with `bundle_fingerprint` and `computed_at`

The function is pure (no side effects, no I/O). It returns a string
the caller can print, write to disk, or embed in another document.

### Public API placement

All of the following are exported from `agent_memory_contracts`
(top-level, in `__init__.py`):

- `ConflictResolution` (class)
- `resolve_conflict` (function)
- `apply_resolutions` (function)
- `validate_resolutions` (function)
- `MemoryHygieneReport` (class)
- `compute_hygiene_report` (function)
- `hygiene_report_to_markdown` (function)

The reasoning: these are the building blocks of any company-brain
product. Making them importable as `from agent_memory_contracts
import resolve_conflict` keeps the product's call sites short. If
a user wants to avoid the import surface, they can still import
from the submodule (`from agent_memory_contracts.conflict
import resolve_conflict`).

### CLI subcommand: `hygiene`

```
python -m agent_memory_contracts hygiene <path>
                                       [--from ISO]
                                       [--to ISO]
                                       [--conflicts-diff <a.json> <b.json>]
                                       [--conflicts-merge <a.json> <b.json> [<c.json> ...]]
                                       [--json]
```

- `<path>` is a JSON or JSONL bundle of records (same loading as
  `validate` / `fingerprint` / `diff`).
- `--from` and `--to` set the time window. Both optional; default
  to the bundle's full time range.
- `--conflicts-diff` runs `bundle_diff` on two additional bundles
  and uses the result's `changed` + `added` + `removed` counts to
  populate `conflicts_surfaced_count`. The two bundles are loaded
  and diffed, but not persisted.
- `--conflicts-merge` runs `merge_bundles` on N additional bundles
  with `prefer="raise"` semantics, catches the resulting
  `ValueError` if any, and uses the conflict count from the
  result. (This is the standard "is there a conflict I'd be
  hiding if I picked 'last'?" check.)
- `--json` emits a structured JSON envelope instead of the Markdown
  report.

The CLI is consistent with the other subcommands: exit 0 on
success, 1 on validation error, 2 on usage error. With `--json`,
errors are emitted as JSON to stderr.

### Worked examples

The new example `examples/conflict_resolution.py` (~150-200
lines, runnable) demonstrates three scenarios:

**Scenario A: pick-one resolution.** Two engineering teams both
wrote a "use SQLite for state" preference in the same week. One
came from a Slack thread; the other from a design doc with
explicit benchmark data. The team lead picks the design doc
version with rationale "design doc has empirical data; Slack
thread is a passing comment." The example builds a
`ConflictResolution`, applies it to a bundle, and prints the
result: the design doc version is canonical, the Slack thread
version is marked `superseded_by_conflict_resolution=<res id>`.

**Scenario B: merge resolution.** Three teams wrote a "preferred
deployment cadence" preference with different content (daily,
weekly, on-demand). The team lead can't pick a single winner;
picks `"merge"`. The example shows the synthetic merged record
with `last-write-wins` per field. The audit chain is visible:
the merged record has `resolved_conflict_ids=[res id]`, the
three rejected variants point at the resolution, and the
resolution's `chosen_record` is the synthetic merge.

**Scenario C: split resolution.** A reducer mistake produced two
records with the same id that actually refer to different things
(the schemas are valid for both, but the semantic intent is
different). The team lead picks `"split"` with rationale "the two
records share an id but encode different facts; the correct fix
is to deprecate the id and create two new memories with distinct
ids, deferring to v0.8.0." The example shows the bundle state
after: both original records still present, both marked
`superseded_by_conflict_resolution`, the resolution record
itself has `chosen_record=None`.

**Scenario D: weekly hygiene report.** A bundle of 200 records
spanning 6 months. The example runs `compute_hygiene_report`,
prints the Markdown, and shows the one-line "12 supersessions,
3 conflicts surfaced, 2 resolved, 1 still open, 47 stale, 189
active" summary.

**Scenario E: hygiene report with custom window + diff-augmented
conflicts.** A 500-record bundle for Q2 2026. The example runs
`compute_hygiene_report(bundle, window_start="2026-04-01",
window_end="2026-06-30", conflicts={"surfaced": 7, "resolved":
5})` and prints the Markdown.

### Failure cases (explicit, with the exact error messages)

These are the unhappy paths the verifier will check. The wording
is part of the spec — change it here if you want a different
message.

1. `resolve_conflict(conflict_with_2_variants, chosen=99)` →
   `ValueError("chosen index 99 out of range for conflict with 2
   variants")`
2. `resolve_conflict(conflict, chosen="pick_one")` →
   `ValueError("chosen must be an int (0-based index), 'merge', or
   'split'; got 'pick_one'")`
3. `resolve_conflict(conflict, chosen=0, rationale="fix")` →
   `ValueError("rationale must be at least 10 characters; got 3")`
4. `resolve_conflict(conflict, chosen=0, resolved_by="")` →
   `ValueError("resolved_by is required and must be non-empty")`
5. `resolve_conflict(conflict, chosen="merge", conflict=(id,
   []))` (no variants) → `ValueError("cannot merge a conflict
   with 0 variants")`
6. `resolve_conflict(conflict, chosen=0, resolved_at="not-iso")`
   → `ValueError("resolved_at must be ISO 8601; got 'not-iso'")`
7. `apply_resolutions(bundle, [resolution])` where the
   resolution's `conflict_id` matches no record in the bundle →
   `ValueError("resolution refers to conflict '<id>' but no
   matching records found in bundle")`
8. `apply_resolutions(bundle, [resolution])` where the
   resolution's `chosen_record.id` (when not None) is not in the
   original conflict's variant ids → `ValueError("resolution
   chosen_record id '<id>' is not among the conflict's variant
   ids")`
9. `apply_resolutions(bundle, [res1, res2])` where two
   resolutions target the same `conflict_id` →
   `ValueError("two resolutions target the same conflict_id
   '<id>'; resolve each conflict at most once")`
10. `compute_hygiene_report(bundle, window_start="not-iso")` →
    `ValueError("window_start must be ISO 8601; got 'not-iso'")`
11. `compute_hygiene_report(bundle, window_start="2026-07-01",
    window_end="2026-06-01")` → `ValueError("window_start
    '2026-07-01' is after window_end '2026-06-01'")`
12. `compute_hygiene_report([1, 2, 3])` (non-dict records) →
    `TypeError("each record must be a dict; got int at index 0")`
13. `compute_hygiene_report([])` → returns a report with all
    counts = 0 and the empty-bundle fingerprint, no error
14. `apply_resolutions(bundle, [resolution])` where the
    resolution is a "split" (chosen_record is None) → no error;
    split resolutions don't add a new record to the bundle, they
    just mark the original variants as superseded

### What this sprint does NOT include

- **Conflict surfacing for ledger-supersession chains.** When B
  supersedes A and A has a conflict, surfacing that requires a
  different primitive (graph traversal). That's v0.8.0.
- **Reducer-side conflict integration.** The reducer's job is to
  AVOID conflicts by gating on its own checks; the conflict
  primitive is for post-hoc triage, not pre-hoc prevention.
- **A "memory council" UI for conflict resolution.** That's a
  product feature, not a library feature.
- **Persisting `ConflictResolution` records to a database.** The
  library is storage-agnostic. The product handles persistence.
- **Time-windowed queries on `ConflictResolution` records**
  ("show me all resolutions in Q2 2026"). That's a future query
  primitive.
- **Auto-resolution policies** (e.g., "auto-resolve conflicts
  where the highest-confidence variant wins"). That's a product
  feature.
- **Conflict resolution for bundle-supersession chain edges** (the
  cross-plane conflict where a ledger entry's evidence points at
  two sources that disagree about a taste card's validity). That's
  v0.8.0's citation graph work.

### Test scope

**New test files:**
- `tests/test_conflict.py` — ~20 tests covering:
  - Basic `resolve_conflict` with int chosen
  - `resolve_conflict` with `"merge"`
  - `resolve_conflict` with `"split"`
  - Every failure case (1-9 in the list above)
  - `apply_resolutions` for each of the three scenarios
  - `apply_resolutions` for the empty-bundle edge case
  - `validate_resolutions` happy path
  - `validate_resolutions` returning error messages
  - Round-trip: dataclass → dict → dataclass
  - Content-derived id stability: same conflict + same resolution
    → same id; re-run with different `resolved_at` → different id
- `tests/test_hygiene.py` — ~12 tests covering:
  - Basic `compute_hygiene_report` on a small fixture bundle
  - Custom window
  - Empty bundle
  - Malformed timestamps
  - Records with missing/orphan evidence
  - Records across all six planes
  - Markdown formatting
  - `bundle_fingerprint` matches the input
- `tests/test_cli_hygiene.py` — ~5 tests covering:
  - CLI subcommand on a real bundle
  - `--json` mode
  - `--from` / `--to` flags
  - Missing file
  - Malformed bundle

**New example:**
- `examples/conflict_resolution.py` — ~150-200 lines, runnable,
  five scenarios (A through E above).

**New CLI subcommand:** `hygiene`.

**Updated files:**
- `src/agent_memory_contracts/__init__.py`: add the new exports.
- `src/agent_memory_contracts/__main__.py`: add the new subcommand
  and import the new symbols.
- `README.md`: add a "Conflict resolution" section and a "Memory
  hygiene" section to "What's in the box." Update the test-count
  badge.
- `CHANGELOG.md`: 0.7.0 entry.
- `pyproject.toml`: bump `version = "0.7.0"`.

**Sprint size estimate:** 1-2 days. The primitives are well-scoped;
the example is concrete; the tests are tractable. Solo execution,
no team mode.

### Order of work

1. Spec doc committed (this file, already in `docs/specs/`).
2. After your sign-off, in this order:
   a. `conflict.py` (primitives, no tests yet)
   b. `tests/test_conflict.py` (verify primitives)
   c. `hygiene.py` (primitives, no tests yet)
   d. `tests/test_hygiene.py` (verify primitives)
   e. `examples/conflict_resolution.py` (worked example)
   f. CLI subcommand `hygiene` + `tests/test_cli_hygiene.py`
   g. `__init__.py` and `__main__.py` exports/imports
   h. README + CHANGELOG
   i. `pyproject.toml` version bump
   j. Run full test suite (`pytest -q`)
   k. `mypy --strict src/agent_memory_contracts`
   l. Run all examples as a smoke test (already in CI)
   m. `release:` commit
   n. Push to `origin/main`
   o. Tag `v0.7.0`
   p. Create GitHub Release
3. Hand back to you for the v0.7.0 review.

### Open questions for you

1. **Branching:** staying on main (my read) vs feature branches
   from this sprint onwards. Default: stay on main. Override
   here if you want otherwise.
2. **Naming:** is `ConflictResolution` the right name, or do you
   prefer `MemoryConflictResolution` / `ConflictDecision` /
   something else? Default: `ConflictResolution` (short, library-
   standard). The submodule is `conflict.py` either way.
3. **`MemoryHygieneReport` vs alternatives:** `MemoryHealthReport`?
   `MemorySnapshot`? `MemoryAuditReport`? Default:
   `MemoryHygieneReport` (the "hygiene" word is intentional —
   the product's UX calls this "memory hygiene" in the weekly
   ritual; matching the naming keeps the call sites clean).
4. **Id prefix for `ConflictResolution`:** `confres_` (my
   default) vs `res_` vs `conflictres_`. Default: `confres_`.
5. **Id prefix for `MemoryHygieneReport`:** `hygiene_` (my
   default) vs `report_`. Default: `hygiene_`.
6. **The 10-character minimum on `rationale`:** is 10 enough?
   Or do you want 20, 30? Default: 10 (short but enough to
   filter out "ok" / "fix" / single-word rationales).
7. **Should `apply_resolutions` mutate the input or return a new
   bundle?** Default: returns a new bundle (no mutation), so
   the caller can choose whether to commit the change. The
   alternative is to mutate in place and return `None`. The
   dataclass convention is "no mutation," and products can
   rebind to the new list trivially.
8. **Should the CLI's `--json` mode for `hygiene` be a structured
   object or just the `MemoryHygieneReport` dict?** Default:
   the dict (consistent with the other subcommands' `--json`
   shapes — direct, not wrapped). Override here if you want
   a wrapper.
9. **Should the example's `examples/conflict_resolution.py`
   fixture data be reused in tests?** Default: no, tests have
   their own fixtures (synthetic, smaller, focused). The example
   is for human readers.

If you have no overrides, I'll go with all defaults. If you have
1-2 minor overrides, just list them inline. If you have a
substantive redesign, let's talk before I start.

---

## What I'd want feedback on

The 9 open questions above are the small ones. The bigger
questions, where I'd most value your pushback:

- **Is the `apply_resolutions` semantics right?** I'm choosing
  the chosen record by replacing the first matching variant; the
  rejected variants stay in the bundle with
  `superseded_by_conflict_resolution` pointing at the resolution.
  An alternative is to remove the rejected variants entirely.
  Keeping them preserves the audit chain in the bundle itself;
  removing them shrinks the bundle. I have a preference for
  "keep" because audit chains should be self-contained. If you
  want "remove," the rejection still gets a record but the
  variants don't stay in the bundle.
- **Is the `"merge"` synthetic record shape right?** I'm using
  last-write-wins per field across the variants. Alternatives:
  union of fields (preserving all), schema-aware merge (only
  merge fields the schema says are safe to merge), or fail on
  `"merge"` and require the caller to construct the merged
  record explicitly. The last is the most conservative and
  probably the most defensible. If you want the strict mode,
  we drop `"merge"` from `chosen` and require the caller to
  pass the merged record directly.
- **Is `MemoryHygieneReport` the right shape for what the product
  needs?** I've included 13 fields. If the product's UX needs
  different cuts (e.g., per-tenant breakdown, per-confidence
  histogram), say so now and I'll add them in this sprint
  rather than wait for the product to ship and discover the gap.

---

## Bottom line

I have a clear sprint. The size is right (1-2 days solo), the
spec is concrete enough to write tests against, the failure
modes are enumerated, the public API placement is decided, and
the boundaries ("what this sprint does NOT include") are stated.

If you sign off as-is, I start work in ~30 minutes from your
"go." If you have overrides on the 9 small questions, list them
inline. If you have a substantive redesign, let's talk.
