# Sprint 23 / v0.9.0 spec: access control + bundle scope

**Status:** awaiting the post-v0.8.0 review the user asked for.
I'll start implementation after that sign-off, but I am writing
this spec now (per the "build and push v0.9.0" mandate) so the
sprint is queued.

**Branching decision:** staying on `main`.

---

## Problem

The library has 5 privacy classes defined in
`evidence_contracts.PRIVACY_CLASSES`:
`public < internal < private < sensitive < highly_sensitive`,
plus 5 custody statuses, 5 excerpt policies, and a
`source_record_ids` / `episode_record_ids` / `evidence_span_ids`
field on every claim that traces back to raw inputs. The
classification system is in place; the library has no
first-class primitive for "given a bundle, return the subset of
records I can safely share at scope X" or "given a record,
should it be allowed, redacted, or dropped at scope X."

A company-brain product needs this. The product's sharing
semantics are not just "send the bundle to a teammate"; they
are "send the bundle to a teammate whose clearance is internal,
so redact everything private-or-above and drop everything
sensitive-or-above." Without a library primitive for this, every
product re-implements the same `if privacy_class > max: drop`
loop, with subtle off-by-one bugs in different places.

This sprint ships the primitive.

---

## What's in this sprint

### New module: `src/agent_memory_contracts/access.py`

A single new module that defines the scope, the access decision,
and two free functions. Library conventions: frozen dataclasses,
stdlib only, `mypy --strict` clean.

#### `PRIVACY_CLASS_ORDER` (tuple of strings)

A constant defining the strict ordering of privacy classes from
least to most restricted:

```python
PRIVACY_CLASS_ORDER: tuple[str, ...] = (
    "public", "internal", "private", "sensitive", "highly_sensitive",
)
```

A record is "at or below" ``max_privacy_class`` iff its privacy
class appears in the tuple at or before the index of
``max_privacy_class``. The library's existing
`PRIVACY_CLASSES` set is the source of truth for valid values;
this tuple is the ordering.

#### `BundleScope` (frozen dataclass)

Describes the "view" of a bundle — what subset is allowed at
this scope. Three fields:

```python
@dataclass(frozen=True)
class BundleScope:
    max_privacy_class: str  # a value in PRIVACY_CLASS_ORDER
    allowed_record_types: frozenset[str] | None  # None = all types
    name: str  # human-readable label, e.g., "team", "customer", "public"
```

`max_privacy_class` is the gate. `allowed_record_types` is an
optional whitelist (e.g., "share facts but not decisions").
`name` is for the product UI ("Bundle shared at scope: team").

The default scope (`max_privacy_class="internal"`,
`allowed_record_types=None`, `name="team"`) is the most common
case: "share with my team, no decision records." A factory
function `team_scope()` returns the default. Factories
`public_scope()`, `customer_scope()`, `private_scope()` cover
the other common cases.

#### `AccessDecision` (frozen dataclass)

The per-record outcome of a scope check.

```python
@dataclass(frozen=True)
class AccessDecision:
    record_id: str
    action: Literal["allow", "redact", "drop"]
    reason: str  # human-readable explanation
```

In v0.9.0, the `redact` action is reserved but never returned
by the default `check_access` (we don't have field-level
redaction yet). The action is in the API so a future sprint can
add a redact step without breaking the public surface.

#### `check_access(record, scope) -> AccessDecision`

Per-record access check. Returns an `AccessDecision` with
`action="allow"` if the record is within the scope, `action="drop"`
if it is above `max_privacy_class` or has a disallowed record
type. The `reason` field is human-readable, e.g.,
"privacy_class=highly_sensitive > max=internal".

The function is forgiving about record shape: a record without
a `privacy_class` field is treated as `internal` (the library's
default). A record without a discriminator field that
`allowed_record_types` can match is allowed (record-type
filtering is opt-in, not opt-out).

#### `scope_bundle(bundle, scope) -> tuple[Bundle, list[AccessDecision]]`

Whole-bundle filter. Returns a new bundle (list of records)
containing only the allowed records, plus a list of access
decisions (one per record, in the same order as the input).
The function is the headline primitive for "share this bundle
at scope X."

The default action is `drop` for records outside the scope. The
caller can also ask for `redact` semantics in a future sprint
(v0.9.x) once field-level redaction is implemented.

#### `summarize_access(decisions) -> AccessSummary`

Helper that aggregates a list of access decisions into a
summary:

```python
@dataclass(frozen=True)
class AccessSummary:
    total: int
    allowed: int
    redacted: int
    dropped: int
    by_privacy_class: Mapping[str, int]  # count per privacy_class for records in the bundle
    by_action: Mapping[str, int]
```

Useful for product dashboards ("you tried to share 100 records;
62 are allowed, 0 will be redacted, 38 will be dropped").

---

## What's NOT in this sprint

- **No field-level redaction.** A record is either allowed or
  dropped, never partially redacted. Redaction of specific
  fields (e.g., text excerpts while keeping the span's
  metadata) is a future sprint.
- **No user/team/role model.** `BundleScope` describes a
  *classification* (a level of allowed sensitivity), not a
  *principal* (a person or team). The product is responsible
  for mapping principals to scopes. The library gives the
  product the primitive to check and filter; the product
  applies it.
- **No signed envelopes / encryption / decryption.** Scope
  filtering is a data-classification primitive. Cryptographic
  protections are a different layer.
- **No audit log.** `AccessDecision` is returned by the
  function; the product persists it (or doesn't). The library
  does not log.
- **No new privacy classes or scope kinds.** The 5 classes
  in `PRIVACY_CLASSES` and the 3 scope factories (public,
  team, customer, private) are the surface. Adding a new
  class is a schema change, deferred.

---

## Public API placement

```python
# src/agent_memory_contracts/__init__.py — added in v0.9.0
from .access import (
    PRIVACY_CLASS_ORDER,
    AccessDecision,
    AccessSummary,
    BundleScope,
    check_access,
    public_scope,
    team_scope,
    customer_scope,
    private_scope,
    scope_bundle,
    summarize_access,
)
```

Public API commitment for the v0.9.0 line. Renaming any of these
names after v0.9.0 is a breaking change.

---

## Semantics

### Privacy class ordering

```
public < internal < private < sensitive < highly_sensitive
```

A record with `privacy_class="internal"` is at-or-below
`max_privacy_class="internal"` (allowed). A record with
`privacy_class="private"` is above `max_privacy_class="internal"`
(dropped).

A `max_privacy_class="public"` scope allows only `public`
records. A `max_privacy_class="highly_sensitive"` scope allows
all 5 classes (the most permissive scope).

### Default scope

The default `BundleScope(max_privacy_class="internal",
allowed_record_types=None, name="team")` is the "share with my
team" use case. It is the most common scope; making it
ergonomic is worth a factory.

### `check_access(record, scope)`

```python
src = SourceRecord(..., privacy_class="public", ...)
scope = team_scope()  # max_privacy_class = "internal"
decision = check_access(src, scope)
# decision.action == "allow"
# decision.reason == "privacy_class=public <= max=internal"
```

```python
sensitive = SourceRecord(..., privacy_class="highly_sensitive", ...)
decision = check_access(sensitive, scope)
# decision.action == "drop"
# decision.reason == "privacy_class=highly_sensitive > max=internal"
```

### `scope_bundle(bundle, scope)`

```python
bundle = [public_src, internal_src, private_fact, sensitive_src]
scope = team_scope()
filtered, decisions = scope_bundle(bundle, scope)
# filtered = [public_src, internal_src]
# decisions = [
#     AccessDecision(record_id=public_src.id, action="allow", ...),
#     AccessDecision(record_id=internal_src.id, action="allow", ...),
#     AccessDecision(record_id=private_fact.id, action="drop", ...),
#     AccessDecision(record_id=sensitive_src.id, action="drop", ...),
# ]
```

The function preserves the input order. The decisions list is
the same length as the input. The filtered list is a strict
subset (the allowed records, in the same relative order).

### `summarize_access(decisions)`

```python
summary = summarize_access(decisions)
# summary.total == 4
# summary.allowed == 2
# summary.dropped == 2
# summary.redacted == 0
# summary.by_action == {"allow": 2, "drop": 2}
# summary.by_privacy_class == {"public": 1, "internal": 1, "private": 1, "sensitive": 1}
```

---

## Failure modes and edge cases

1. **Empty bundle.** `scope_bundle([], scope)` returns `([], [])`.
   `summarize_access([])` returns a zeroed summary.
2. **Record with no `privacy_class` field.** Treated as
   `internal` (the library's default for un-classified data).
3. **Record with an unknown `privacy_class` value** (e.g.,
   `"classified"`). `check_access` raises `ValueError` with the
   offending class. The library's `PRIVACY_CLASSES` set is the
   source of truth; an unknown class is a contract violation.
4. **Scope with an unknown `max_privacy_class`.** Raises
   `ValueError` on construction (`__post_init__`).
5. **`allowed_record_types` is `None`.** Treated as "all
   record types allowed" (the common case).
6. **`allowed_record_types` is an empty `frozenset`.** Treated
   as "no record types allowed" — every record is dropped. The
   caller's mistake; the library surfaces it deterministically.
7. **Dict vs dataclass records.** Both are accepted. The
   classification uses `getattr(record, "privacy_class", "internal")`
   with fallback to dict-key access.
8. **Records that are not in the citation graph.** Scope
   filtering is independent of the citation graph. A
   `MemoryReducerDecision` (not in the citation graph) is
   still subject to scope filtering.
9. **Cycles in `allowed_record_types` references.** Not
   applicable; `allowed_record_types` is a `frozenset[str]`,
   not a graph.

---

## Test plan

### Synthetic fixtures

1. **All-five-classes bundle.** One record at each of
   `public`, `internal`, `private`, `sensitive`,
   `highly_sensitive`. Used for the cross-class sweep.
2. **Mixed-type bundle.** Sources, evidence spans, ledger
   entries. Tests `allowed_record_types` filtering.
3. **Dict-form bundle.** Tests the dict path of `check_access`.
4. **Empty bundle.** Tests the empty case.

### Test cases

For each fixture:

- `check_access(record, scope)` returns the expected
  `AccessDecision` for every record.
- `scope_bundle(bundle, scope)` returns the expected
  filtered list and decision list.
- `summarize_access(decisions)` returns the expected counts.
- `__init__.py` exports all v0.9.0 names.
- `mypy --strict` clean on the new module.

Plus the failure-mode tests.

Target: **25+ new tests** in `tests/test_access.py`. Total
target: **393+ tests** (368 + ~25).

### `examples/access.py`

A worked example covering:

- Build a bundle with records at 3 privacy levels.
- `scope_bundle` at the `team` scope: keep public + internal,
  drop private.
- `scope_bundle` at the `public` scope: keep public only.
- `scope_bundle` at the `customer` scope: keep public +
  internal + private.
- `summarize_access` of each.

---

## Bottom line

The access control primitive is a thin layer on top of the
existing privacy classes. It does not need new schemas, new ids,
or new dependencies. It needs ~150 LOC of scope logic, ~50 LOC
of the access decision, and ~25 tests. The deliverable is small
and the value is high: a company-brain product gets a single
library call to "share this bundle at scope X" with per-record
audit decisions for the product UI to display.

If you sign off as-is, I start work in ~30 minutes from your
"go." If you have overrides on the 9 small questions below,
list them inline. If you have a substantive redesign, let's
talk.

---

## Open questions for you

1. **Module name:** `access.py` (my default) vs `scope.py` vs
   `acl.py`. Default: `access.py` (matches the public API name
   `BundleScope` and the headline function `check_access`).
2. **`BundleScope` field name for the privacy gate:**
   `max_privacy_class` (my default) vs `max_classification` vs
   `allowed_classification`. Default: `max_privacy_class` (uses
   the library's existing terminology; less likely to clash with
   future fields).
3. **Default `max_privacy_class`:** `"internal"` (my default) vs
   `"public"` vs required. Default: `"internal"` (the "team"
   use case is the most common; making it ergonomic is worth
   the default).
4. **`AccessDecision` `action` enum:** `"allow" | "redact" |
   "drop"` (my default) vs `"allow" | "drop"` only. Default: 3
   values (the redact action is reserved for v0.9.x when
   field-level redaction is implemented; the public surface
   stays stable across that change).
5. **`summarize_access` free function:** yes (my default) vs
   only as a method on the list. Default: free function for
   ergonomics.
6. **Should `scope_bundle` return the decisions list or a
   single `AccessSummary`?** Default: both — the decisions list
   (so the product can render per-record decisions in the UI)
   and the summary (so dashboards can show counts). Two
   return values, in a tuple.
7. **`AccessSummary` field names:** `total`, `allowed`,
   `redacted`, `dropped` (my default) vs `count`/`n`/etc.
   Default: spelled-out names (the dataclass is for humans to
   read in product code).
8. **Should `check_access` raise on unknown `privacy_class` or
   treat it as a sentinel?** Default: raise (a contract
   violation is a bug; silent fallback hides the bug).
9. **Should there be a CLI subcommand for access?**
   Default: defer to v0.9.1. The three primitives
   (`check_access`, `scope_bundle`, `summarize_access`) are
   the deliverable.

If you have no overrides, I'll go with all defaults.

---

## What I'd want feedback on

The 9 open questions above are the small ones. The bigger
questions, where I'd most value your pushback:

- **Is "drop, never redact" the right v0.9.0 default?** A
  product that wants to share a `FactLedgerEntry`'s subject
  and predicate while hiding its `fact_text` (a sensitive
  full-text quote) cannot do that in v0.9.0 — the entire
  record is dropped. The alternative is field-level
  redaction: a `RedactionPolicy` that says "for fact_text
  above `internal`, replace with `None`." That doubles the
  sprint size. My read is "v0.9.0 ships the primitive
  without redaction; v0.9.x adds redaction once we have
  evidence the product needs it." If you disagree and want
  redaction in v0.9.0, let's talk about which fields.
- **Is the privacy-class ordering the right shape?** The
  library's `PRIVACY_CLASSES` set has 5 values; the
  ordering is a strict linear order. Some products want a
  partial order (e.g., `public` is less restrictive than
  `internal` AND `sensitive`; `sensitive` is more restrictive
  than `internal` but not necessarily related to `private`).
  My read is "the linear order is enough for v0.9.0; a
  partial order is a v0.9.x conversation." If you want a
  partial order now, let's talk.
- **Is `summarize_access` needed at all?** The product can
  trivially aggregate the decisions list with a counter
  pattern. A library helper saves the product from writing
  the same 4-line loop in 5 places. My read is "ship the
  helper, it costs 30 LOC and saves duplication." If you
  disagree, override question 5.

---

## Implementation order

After sign-off, the work happens in 6 commits on `main`:

1. `docs(specs): sprint 23 / v0.9.0 — access control + bundle
   scope` (this doc, on `docs/specs/sprint_23_access_control.md`).
2. `feat: add BundleScope, AccessDecision, PRIVACY_CLASS_ORDER
   in agent_memory_contracts.access`.
3. `feat: add check_access, scope_bundle, summarize_access, and
   the four scope factories`.
4. `test+example: 25+ tests for access control;
   examples/access.py`.
5. `release: agent-memory-contracts v0.9.0` (version bump,
   CHANGELOG, tag, push, GitHub Release — but per your
   "not pypi just yet" call, no Release until you say so).

The work is solo (one main primitive, ~150 LOC of code + ~50
LOC of test). Estimated: 1 day solo, faster than v0.8.0
because the design is smaller.

After implementation:
- `pytest -q` reports **393+ tests** (368 + ~25), 0 failures.
- `mypy --strict src/agent_memory_contracts` clean.
- All 6 examples (5 existing + `access.py`) run as smoke
  tests in CI.

---

## Decisions applied to this sprint

Applied 2026-06-06 per the user's "go with best judgment"
mandate. Recorded here so the spec stays the source of truth for
"why was this built this way" review.

### 9 small decisions (all defaults)

1. **Module name:** `access.py`.
2. **`BundleScope` field name:** `max_privacy_class`.
3. **Default `max_privacy_class`:** `"internal"` (the "team"
   use case is the most common; making it ergonomic is worth
   the default).
4. **`AccessDecision` `action` enum:** `"allow" | "redact" |
   "drop"` (the redact action is reserved for v0.9.x; the
   public surface stays stable across that change).
5. **`summarize_access` is a free function** (not just a
   method on the list).
6. **`scope_bundle` returns `(filtered_bundle, decisions_list)`:**
   a 2-tuple, in that order.
7. **`AccessSummary` field names:** `total`, `allowed`,
   `redacted`, `dropped` (spelled out, not abbreviated).
8. **`check_access` raises on unknown `privacy_class`:** a
   contract violation is a bug; silent fallback hides the bug.
9. **No `access` CLI subcommand:** defer to v0.9.1.

### 3 bigger-question decisions (all defaults)

- **"Drop, never redact" is the v0.9.0 default.** The
  primitive is whole-record: a record is allowed or dropped,
  not partially redacted. Field-level redaction is a v0.9.x
  conversation once we have evidence the product needs it.
  The `action="redact"` enum value is reserved in the public
  surface so the future addition does not break call sites.
- **Linear privacy-class ordering is the v0.9.0 shape.** The
  5 classes in `PRIVACY_CLASSES` are ordered strictly:
  `public < internal < private < sensitive <
  highly_sensitive`. A partial order is a v0.9.x
  conversation.
- **`summarize_access` is shipped as a helper.** The 30-LOC
  helper saves duplication across product dashboards.

### Minor implementation choices

- **Default `privacy_class` for records without the field:**
  `"internal"` (the library's working default for
  un-classified data; matches the schema's default).
- **Dict records:** accepted in `check_access` and
  `scope_bundle` via the same shape-based dispatch used in
  v0.8.0's citation graph.
- **Order preservation in `scope_bundle`:** the filtered
  bundle preserves the input order; the decisions list
  matches the input order 1:1.
- **`AccessDecision.reason` is human-readable English.** No
  structured codes; the reason is for product UIs and audit
  logs, not for programmatic branching. Programmatic branching
  uses `decision.action == "allow"`.
