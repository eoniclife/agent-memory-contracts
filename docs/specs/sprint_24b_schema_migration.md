# Sprint 24b / v1.0.0-alpha.2 spec: schema migration framework

**Status:** awaiting your review (the same "go" pattern as
24a). The 9 small + 3 bigger questions are at the bottom;
defaults applied per the "best judgment" mandate.

**Branching decision:** staying on `main`.

---

## Problem

The library's JSON Schemas are at `schema_version: "1.0.0"`
and have been stable across v0.1.0 → v1.0.0-alpha.1. But
"stable" doesn't mean "frozen forever." When v1.1.0 ships a
new field on `SourceRecord` (say, a `parsed_by` field), or
when a record type is renamed, or when a privacy class is
reclassified — existing bundles in user storage will
silently become invalid at the schema layer.

The library needs a **migration framework**: a way to
register schema-version-to-schema-version migration steps
and apply them to a bundle deterministically. The
framework is the safety net for the v1.0.0 stability
commitment — the promise is "we won't break your data
silently, and when we do change the schema, we'll ship a
migration with the change."

What the library does **not** do:
- Define specific schema migrations for v1.1.0 (those
  don't exist yet — the framework is the scaffolding).
- Auto-detect which migrations to run from a bundle's
  contents (the caller knows the source version and the
  target version; the framework applies the path between
  them).
- Persist migration state. The framework is a function:
  `migrate_bundle(bundle, target_version) -> Bundle`. The
  product persists the result.

What the library **does** do:
- Define `MigrationStep` (frozen dataclass) and
  `SchemaMigrator` (mutable registry).
- Ship a `default_migrator()` with no built-in migrations
  registered (but the API is in place for when v1.1.0
  ships its first migration).
- Define `migrate_bundle(bundle, *, target_version)` and
  the lower-level `apply_migrations(bundle, steps)`.
- Support chained migrations (1.0.0 → 1.0.5 → 1.1.0)
  with idempotency.
- Detect version mismatches and raise clear errors
  ("no migration path from X to Y").
- Be library-style: stdlib only, frozen dataclasses,
  mypy --strict clean.

---

## What's in this sprint

### New module: `src/agent_memory_contracts/migrations.py`

A single new module. The deliverable is two frozen
dataclasses, one mutable registry, and three free
functions.

#### `MigrationStep` (frozen dataclass)

A single migration from one schema version to another.

```python
@dataclass(frozen=True)
class MigrationStep:
    from_version: str          # "1.0.0"
    to_version: str            # "1.0.5" or "1.1.0"
    description: str           # human-readable
    migrate_record: Callable[[dict], dict]
```

The `migrate_record` callable takes a single record dict
and returns the migrated record dict. The framework
applies the callable to every record in the bundle whose
`schema_version` matches `from_version`.

The callable is `Callable[[dict], dict]` (not
`Callable[[Any], Any]`) so the contract is explicit: the
function takes a record *as a dict* and returns a record
*as a dict*. Records that arrive as dataclasses are
converted via `dataclasses.asdict` before the migration
is applied; records that arrive as dicts are passed
through unchanged.

#### `MigrationResult` (frozen dataclass)

The outcome of applying a migration.

```python
@dataclass(frozen=True)
class MigrationResult:
    bundle: list[Any]                  # the migrated bundle
    target_version: str                # the version we migrated to
    steps_applied: tuple[tuple[str, str], ...]  # (from, to) pairs
    records_migrated: int              # count of records touched
    records_unchanged: int             # count of records already at target
    errors: tuple[str, ...]            # per-record error messages (empty on success)
```

#### `SchemaMigrator` (class)

A mutable registry of migration steps. The product
constructs one, registers steps, and calls
`migrate_bundle(bundle, target_version="1.1.0")`.

```python
class SchemaMigrator:
    def __init__(self) -> None:
        self._steps: dict[tuple[str, str], MigrationStep] = {}

    def register(self, step: MigrationStep) -> None: ...
    def has_path(self, from_version: str, to_version: str) -> bool: ...
    def find_path(self, from_version: str, to_version: str) -> list[MigrationStep]: ...
    def migrate_bundle(
        self,
        bundle: Iterable[Any],
        *,
        target_version: str,
    ) -> MigrationResult: ...
```

`find_path` is a BFS over the registered (from, to) edges
that returns a list of `MigrationStep` from `from_version`
to `target_version`, or raises `ValueError` if no path
exists. `migrate_bundle` uses `find_path` to compute the
chain, then applies each step in order.

`register` raises `ValueError` if a step with the same
`(from_version, to_version)` pair is already registered
(detects double-registration).

#### `migrate_bundle(bundle, *, target_version, migrator=None) -> MigrationResult`

The headline function. Convenience wrapper that uses a
default migrator if none is passed.

```python
def migrate_bundle(
    bundle: Iterable[Any],
    *,
    target_version: str,
    migrator: SchemaMigrator | None = None,
) -> MigrationResult: ...
```

If `migrator is None`, uses `default_migrator()` (which
ships with no built-in migrations; calls to `migrate_bundle`
with a `target_version` other than `"1.0.0"` will raise
`ValueError("no migration path from 1.0.0 to <target>")`
until v1.1.0 ships its first migration).

#### `default_migrator() -> SchemaMigrator`

Returns a `SchemaMigrator` with no built-in migrations
registered. The product builds on this by calling
`register` for its own custom migrations.

#### `apply_migrations(bundle, steps) -> MigrationResult`

Lower-level function. Applies a specific list of
`MigrationStep` objects to a bundle, in order. Used by
`SchemaMigrator.migrate_bundle` internally.

---

## What's NOT in this sprint

- **No specific migrations for v1.1.0.** The library
  ships with the framework, not with concrete migrations
  (none are needed yet — the schemas are stable). When
  v1.1.0 ships, the migrations module will be updated
  with the new step(s) as part of that sprint.
- **No auto-detection of source version.** The caller
  passes `target_version`; the framework assumes the
  bundle is at the version returned by
  `find_path(start, target).steps[0].from_version` where
  `start` is the earliest step. A future sprint could
  add an `auto_detect=True` mode that reads each record's
  `schema_version` field.
- **No validation of migrated records.** A migration
  might produce an invalid record (e.g., a field is
  removed without updating dependents). The framework
  applies the migration; validation is the product's
  responsibility. The framework surfaces per-record
  errors in the `MigrationResult.errors` field.
- **No persistence of migration state.** The product
  persists the migrated bundle. The framework does not
  track "which bundles have been migrated" — that's a
  product concern.
- **No CLI subcommand.** Primitives are library-only.
- **No schema changes.** The 23 JSON Schemas stay at
  `"1.0.0"`. The framework is a safety net for the day
  they change.

---

## Public API placement

```python
# src/agent_memory_contracts/__init__.py — added in v1.0.0-alpha.2
from .migrations import (
    MigrationStep,
    MigrationResult,
    SchemaMigrator,
    apply_migrations,
    default_migrator,
    migrate_bundle,
)
```

This is a public API commitment for the v1.0.0 line.

---

## Semantics

### Migration step

A `MigrationStep(from_version="1.0.0", to_version="1.1.0",
description="...", migrate_record=lambda r: r)` is a
declarative description of "how to turn a record at 1.0.0
into a record at 1.1.0." The framework applies the
migrate_record callable to every record in the bundle
whose `schema_version` matches `from_version`.

The `migrate_record` callable is `Callable[[dict], dict]`.
Records that arrive as dataclasses are converted to
dicts via `dataclasses.asdict` before the migration;
records that arrive as dicts are passed through. The
result is always a dict; the framework does not
re-hydrate to dataclasses (the product can do that if
it wants to).

### Path finding

`SchemaMigrator.find_path(from_version, to_version)`
returns a list of `MigrationStep` objects that, applied
in order, take a record from `from_version` to
`to_version`. The algorithm is BFS over the registered
edges:

- Direct edge `(from_version, to_version)`: a single
  step.
- Indirect path `from_version → A → B → to_version`:
  a chain of steps.
- No path: `ValueError("no migration path from X to Y")`.

The framework does not validate that the chain is
"correct" (i.e., that the steps compose cleanly). The
caller is responsible for registering well-formed
chains.

### Migration application

`SchemaMigrator.migrate_bundle(bundle, *, target_version)`:

1. Find the path via `find_path`. (If no path, raise
   `ValueError`.)
2. Initialize `records_migrated=0`, `records_unchanged=0`,
   `errors=()` on the result.
3. For each record in the bundle:
   - Convert to dict (if dataclass).
   - Read `schema_version` (default `"1.0.0"` if
     missing).
   - If already at `target_version`: append to the
     migrated bundle unchanged; increment
     `records_unchanged`.
   - Else: find the first step in the path whose
     `from_version` matches the record's version. If
     no such step: raise `ValueError("no migration path
     for record at version X")`. Apply the step's
     `migrate_record`; set the record's `schema_version`
     to the step's `to_version`; look up the next step
     in the path that takes from that version, and
     apply; repeat until the record is at
     `target_version`. Append to the migrated bundle;
     increment `records_migrated`.
4. Return `MigrationResult(bundle=migrated_bundle, ...)`.

### Idempotency

If `migrate_bundle` is called twice on the same bundle
with the same `target_version`, the second call is a
no-op: every record is already at the target version.
This is the property that makes the framework safe to
retry.

If `migrate_bundle` is called with a `target_version`
that's earlier in the chain than the bundle's current
state (e.g., a bundle at "1.1.0" is migrated to "1.0.0"),
the framework raises `ValueError("no migration path
from 1.1.0 to 1.0.0")` — backward migrations are not
supported. The framework only knows forward edges.

### Default migrator

`default_migrator()` returns a fresh `SchemaMigrator`
with no built-in migrations. The product registers
custom migrations as needed. The library does not
auto-register anything, because there are no schema
changes yet.

A future v1.1.0 sprint will update the default migrator
to include the v1.0.0 → v1.1.0 migration(s) as part of
the schema bump.

---

## Failure modes and edge cases

1. **Empty bundle.** `migrate_bundle([], target_version="1.1.0")`
   returns a `MigrationResult` with `records_migrated=0`,
   `records_unchanged=0`, and the original empty list.
2. **No migration path.** `migrate_bundle(bundle, target_version="1.1.0")`
   with no registered 1.0.0 → 1.1.0 step raises
   `ValueError("no migration path from 1.0.0 to 1.1.0")`.
3. **Double registration.** Registering two steps with
   the same `(from, to)` pair raises `ValueError`.
4. **Mixed-version bundle.** A bundle with records at
   different versions (some 1.0.0, some 1.1.0) is
   supported: each record is migrated from its current
   version to the target. The `MigrationResult` reports
   the per-record counts.
5. **Dataclass input.** Records that are dataclasses
   are converted to dicts via `dataclasses.asdict` before
   the migration; the result is a dict. The product
   re-hydrates if it wants dataclass output.
6. **Migration that produces an invalid record.** The
   framework applies the migration; the result is
   returned. Validation is the product's responsibility.
7. **Migration step raises.** The exception propagates
   out of `migrate_bundle`; partial results are not
   returned. The product is expected to wrap the call
   in a try/except and handle the partial state.
8. **Backward target.** `target_version` earlier in
   the chain than the bundle's current state raises
   `ValueError`. (See "Idempotency" above.)

---

## Test plan

### Synthetic fixtures

1. **Two-step chain.** Register
   `1.0.0 → 1.0.5 → 1.1.0`. A bundle at 1.0.0 should
   migrate to 1.1.0 in two steps.
2. **Direct step.** Register
   `1.0.0 → 1.1.0` (no intermediate). A bundle at 1.0.0
   should migrate in one step.
3. **No path.** No steps registered. A migration
   attempt should raise `ValueError`.
4. **Mixed-version bundle.** Records at 1.0.0, 1.0.5,
   and 1.1.0 in the same bundle. Each is migrated
   (or left unchanged) appropriately.
5. **Dataclass input.** Records arrive as
   `SourceRecord.from_dict(...)` instances; the
   migration converts to dict, applies, returns dict.
6. **Idempotent re-run.** Calling `migrate_bundle`
   twice on the same bundle with the same
   `target_version` is a no-op the second time.

### Test cases

- `default_migrator()` returns a fresh `SchemaMigrator`
  with no registered steps.
- `register(step)` adds the step; double-registration
  raises.
- `find_path(1.0.0, 1.1.0)` returns the chain of steps
  in order.
- `find_path` with no path raises `ValueError`.
- `migrate_bundle` with a path applies each step in
  order, sets `schema_version` correctly, and returns
  the right `MigrationResult`.
- `migrate_bundle` on a record already at
  `target_version` is a no-op (records_unchanged
  increments).
- Mixed-version bundle: each record migrated
  independently.
- Dataclass input: conversion happens, output is a
  dict.
- Idempotency: second call is a no-op.
- `__init__.py` exports all v1.0.0-alpha.2 names.
- `mypy --strict` clean on the new module.

Target: **20+ new tests** in `tests/test_migrations.py`.
Total target: **459+ tests** (439 + ~20).

### `examples/migrations.py`

A worked example showing:
- A 2-step chain (1.0.0 → 1.0.5 → 1.1.0) with a
  realistic migration (e.g., adding a default field).
- A bundle at 1.0.0 being migrated to 1.1.0 in two
  steps.
- The `MigrationResult` with the per-step breakdown.

---

## Bottom line

The migration framework is a thin layer on top of the
existing record types. It does not need new schemas, new
ids, or a runtime migration engine. It needs ~150 LOC of
path-finding + step application, ~50 LOC of the result
dataclass, and ~20 tests. The deliverable is small and
the value is high: it's the v1.0.0 commitment — the
promise that future schema changes ship with a migration.

After this sprint, the library has:
- The integrity layer (citations, access, embedding).
- The compile layer (`ContextPack` schemas, the next
  sprint's compiler).
- The migration framework (this sprint).
- The end-to-end demo (the sprint after that).
- The stability commitment (the sprint after that).

That's v1.0.0.

---

## Decisions applied to this sprint

Applied 2026-06-07 per the user's "best judgment" mandate
and the post-24a re-pacing decision (commit `e99dc63`).

### 9 small decisions (all defaults)

1. **Module name:** `migrations.py`.
2. **Dataclass name:** `MigrationStep` (and
   `MigrationResult`).
3. **Registry class name:** `SchemaMigrator`.
4. **`migrate_record` signature:** `Callable[[dict],
   dict]` (records are converted to dicts before
   migration).
5. **Path-finding algorithm:** BFS over the registered
   `(from, to)` edges. Returns the chain in order.
6. **Backward migrations:** not supported (raises
   `ValueError` if `target_version` is earlier in the
   chain).
7. **Default migrator:** empty registry (no built-in
   migrations). v1.1.0 will add the first one.
8. **Mixed-version bundles:** supported (each record
   migrated independently).
9. **No `migrations` CLI subcommand.** Primitives are
   library-only.

### 3 bigger-question decisions (all defaults)

- **Path-finding is BFS, not the only valid choice.**
  A future sprint could replace BFS with a shortest-
  path algorithm if the migration graph grows large.
  BFS is correct and simple for v1.0.0-alpha.2.
- **Migrations are forward-only.** Backward migrations
  are out of scope. The library's stability promise is
  "we'll ship forward migrations when the schema
  changes," not "we'll support downgrades."
- **The framework is a no-op for v1.0.0 bundles.** The
  default migrator has no registered steps, so calling
  `migrate_bundle` with a target other than `"1.0.0"`
  raises. This is correct: there are no schema changes
  yet. The framework is the safety net, not the cause.

### Minor implementation choices

- **Dataclass-to-dict conversion via
  `dataclasses.asdict`.** Standard library; no custom
  walker.
- **Records already at `target_version` are passed
  through unchanged** (not re-migrated). This makes
  the framework idempotent.
- **`MigrationResult.errors` is reserved for future
  use.** The current implementation does not produce
  per-record errors; it raises on the first error
  (fail-fast). The `errors` field is in the public
  surface so a future sprint can switch to
  per-record-error-collection without breaking the API.
- **The `migrate_record` callable is typed as
  `Callable[[dict], dict]`** (not `Callable[[Any],
  Any]`). Records are dicts at the migration boundary;
  the product can re-hydrate to dataclasses if it
  wants to.
- **The default migrator returns a fresh
  `SchemaMigrator` each call.** No global state; no
  surprises.
