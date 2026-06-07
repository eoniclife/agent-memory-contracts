"""Schema migration framework.

The library's JSON Schemas are at ``schema_version: "1.0.0"``
and have been stable across v0.1.0 → v1.0.0-alpha.1. But
"stable" doesn't mean "frozen forever." When v1.1.0 ships
a new field on ``SourceRecord``, or when a record type is
renamed, or when a privacy class is reclassified — existing
bundles in user storage will silently become invalid at the
schema layer.

This module ships the **migration framework**: a way to
register schema-version-to-schema-version migration steps
and apply them to a bundle deterministically. The
framework is the safety net for the v1.0.0 stability
commitment.

The framework is **forward-only**: a bundle at
``schema_version="1.0.0"`` can be migrated to
``"1.1.0"``; the reverse is not supported. The library
never silently downgrades user data.

The framework is **idempotent**: calling
:meth:`SchemaMigrator.migrate_bundle` twice on the same
bundle with the same ``target_version`` is a no-op the
second time. Records already at the target version are
passed through unchanged.

The framework is **dict-typed at the boundary**: every
record is converted to a dict before the migration is
applied, and the migration callable operates on dicts.
The product can re-hydrate to dataclasses if it wants
to.

The framework is **fail-fast**: a migration that raises
propagates the exception out of ``migrate_bundle``; no
partial results. The product is expected to wrap the
call in a try/except.

The framework is **stdlib-only**: no third-party
dependencies. No JSON Schema validator (the framework
applies migrations; validation is the product's
responsibility).

The framework is **library-style**: frozen dataclasses,
the mutable registry is a class with explicit
``register`` and ``find_path`` methods, mypy --strict
clean.

.. versionadded:: 1.0.0-alpha.2
"""

from __future__ import annotations

import dataclasses
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: The current library-wide schema version. Every record
#: in the library carries a ``schema_version`` field;
#: this is the version that field is expected to equal
#: for newly-created records.
CURRENT_SCHEMA_VERSION: str = "1.0.0"

#: Sentinel used when a record has no ``schema_version``
#: field. Treated as :data:`CURRENT_SCHEMA_VERSION` for
#: migration purposes (the library has always been at
#: "1.0.0" since the initial release; old records that
#: predate the field are assumed to be at the original
#: version).
DEFAULT_RECORD_VERSION: str = CURRENT_SCHEMA_VERSION

#: Sentinel for the "no schema_version" record case in
#: error messages.
MISSING_VERSION: str = "<missing>"


# ---------------------------------------------------------------------------
# Migration step type
# ---------------------------------------------------------------------------


RecordMigrator = Callable[[dict[str, Any]], dict[str, Any]]
"""The signature for a per-record migration function.

Takes a record dict, returns a record dict. The framework
guarantees the input is a dict (dataclass records are
converted via :func:`dataclasses.asdict` before the
migrator is called). The migrator is expected to mutate
the dict in place and return it, or to return a new dict.
The framework does not care which.
"""


@dataclass(frozen=True)
class MigrationStep:
    """A single migration from one schema version to another.

    Attributes:
        from_version: the schema version the record is at
            before this step applies. Must match the
            record's ``schema_version`` field.
        to_version: the schema version the record is at
            after this step applies. The framework sets the
            record's ``schema_version`` to this value
            after the step runs.
        description: human-readable description of what the
            step does. Surfaced in error messages and in
            the ``MigrationResult.steps_applied`` field.
        migrate_record: the per-record migration function.
            Takes a record dict, returns a record dict.
    """

    from_version: str
    to_version: str
    description: str
    migrate_record: RecordMigrator

    def __post_init__(self) -> None:
        if not self.from_version:
            raise ValueError("MigrationStep.from_version is required")
        if not self.to_version:
            raise ValueError("MigrationStep.to_version is required")
        if self.from_version == self.to_version:
            raise ValueError(
                f"MigrationStep.from_version and to_version are equal: {self.from_version!r}"
            )
        if not self.description:
            raise ValueError("MigrationStep.description is required")
        if not callable(self.migrate_record):
            raise ValueError("MigrationStep.migrate_record must be callable")


# ---------------------------------------------------------------------------
# Migration result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MigrationResult:
    """The outcome of applying a chain of migrations.

    Attributes:
        bundle: the migrated bundle (a list of record
            dicts, in input order).
        target_version: the version that the bundle was
            migrated to.
        steps_applied: the (from, to) pairs of every step
            that the framework actually applied. A step
            that was registered but not needed (because
            no record was at its ``from_version``) does
            *not* appear here.
        records_migrated: the number of records that were
            touched by at least one migration step.
        records_unchanged: the number of records that were
            already at ``target_version`` and were passed
            through unchanged.
        errors: per-record error messages. Currently
            always empty (the framework is fail-fast and
            raises on the first error). The field is
            reserved for a future sprint that switches to
            per-record-error-collection.
    """

    bundle: list[Any]
    target_version: str
    steps_applied: tuple[tuple[str, str], ...] = ()
    records_migrated: int = 0
    records_unchanged: int = 0
    errors: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# SchemaMigrator (mutable registry)
# ---------------------------------------------------------------------------


class SchemaMigrator:
    """A mutable registry of schema-version-to-schema-version
    migration steps.

    The product constructs one, registers steps via
    :meth:`register`, and calls :meth:`migrate_bundle` to
    apply the chain.

    A ``SchemaMigrator`` is **forward-only**: there is no
    way to "downgrade" a bundle to an earlier schema
    version. The library's stability promise is
    "forward-migrations ship with the change," not
    "downgrades are supported."

    The migrator is **not thread-safe**. The product is
    expected to construct one per migration job, or to
    guard concurrent access with its own lock. The
    framework does not provide thread-safety because
    migrations are typically a batch operation that runs
    at a controlled time (e.g., on bundle load) rather
    than a high-frequency operation.
    """

    def __init__(self) -> None:
        self._steps: dict[tuple[str, str], MigrationStep] = {}

    def register(self, step: MigrationStep) -> None:
        """Register a migration step.

        Raises:
            ValueError: if a step with the same
                ``(from_version, to_version)`` pair is
                already registered. Double-registration
                is almost always a bug (the second
                registration would silently shadow the
                first on ``find_path``).
        """
        key = (step.from_version, step.to_version)
        if key in self._steps:
            raise ValueError(
                f"migration step already registered: {step.from_version!r} -> {step.to_version!r}"
            )
        self._steps[key] = step

    def registered_steps(self) -> tuple[MigrationStep, ...]:
        """Return the registered steps in registration order.

        Useful for debugging and for the product to
        introspect what migrations are available.
        """
        return tuple(self._steps.values())

    def has_path(self, from_version: str, to_version: str) -> bool:
        """Return whether a migration path exists from
        ``from_version`` to ``to_version``.

        ``from_version == to_version`` returns ``True``
        (the empty path is a no-op).
        """
        if from_version == to_version:
            return True
        try:
            self.find_path(from_version, to_version)
            return True
        except ValueError:
            return False

    def find_path(
        self, from_version: str, to_version: str
    ) -> list[MigrationStep]:
        """Return a chain of ``MigrationStep`` from
        ``from_version`` to ``to_version``, or raise.

        The algorithm is BFS over the registered
        ``(from, to)`` edges, ordered by the registration
        order on tie.

        Args:
            from_version: the source schema version.
            to_version: the target schema version.

        Returns:
            A list of ``MigrationStep`` objects, in
            application order. Empty list if
            ``from_version == to_version``.

        Raises:
            ValueError: if no migration path exists from
                ``from_version`` to ``to_version``, or if
                either version is empty.
        """
        if not from_version or not to_version:
            raise ValueError(
                f"from_version and to_version are required: "
                f"from={from_version!r}, to={to_version!r}"
            )
        if from_version == to_version:
            return []
        # BFS.
        # queue entries: (current_version, path_so_far)
        queue: deque[tuple[str, list[MigrationStep]]] = deque(
            [(from_version, [])]
        )
        visited: set[str] = {from_version}
        # Iterate edges in registration order for determinism.
        for edge_key, step in self._steps.items():
            src = edge_key[0]
            if src not in visited:
                continue
            # BFS step: from src, follow to step.to_version.
            # (We only expand the head of the queue to keep
            # memory bounded; full BFS is below.)
        # Full BFS:
        while queue:
            current, path = queue.popleft()
            for (src, dst), step in self._steps.items():
                if src != current:
                    continue
                if dst in visited:
                    continue
                new_path = path + [step]
                if dst == to_version:
                    return new_path
                visited.add(dst)
                queue.append((dst, new_path))
        raise ValueError(
            f"no migration path from {from_version!r} to {to_version!r}"
        )

    def migrate_bundle(
        self,
        bundle: Iterable[Any],
        *,
        target_version: str,
    ) -> MigrationResult:
        """Apply the migration chain to ``bundle`` so that
        every record ends up at ``target_version``.

        Args:
            bundle: an iterable of records (dataclasses,
                dicts, or Mappings).
            target_version: the schema version to migrate
                to.

        Returns:
            A :class:`MigrationResult` with the migrated
            bundle, the steps that were applied, and
            per-record counts.

        Raises:
            ValueError: if no migration path exists for
                any record in the bundle from its current
                version to ``target_version``. The
                exception is raised on the first record
                that has no path; partial results are not
                returned.
        """
        if not target_version:
            raise ValueError("target_version is required")
        records = list(bundle)
        path = self.find_path(_infer_start_version(records), target_version)
        return apply_migrations(records, path, target_version=target_version)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def default_migrator() -> SchemaMigrator:
    """Return a fresh ``SchemaMigrator`` with the built-in
    v1.0.0 -> v1.1.0 migration registered.

    The library's schemas are at ``1.1.0`` as of v1.1.0;
    the v1.0.0 -> v1.1.0 step is the first concrete
    migration. It adds an optional ``freshness_score``
    field to ledger entries, taste cards, and state
    snapshots; bumps ``schema_version``; the field is
    computed on read by the decay module.
    """
    # Lazy import to avoid a circular dependency between
    # migrations and decay at module load time.
    from agent_memory_contracts.decay import v1_0_0_to_v1_1_0_step

    migrator = SchemaMigrator()
    migrator.register(v1_0_0_to_v1_1_0_step())
    return migrator


def apply_migrations(
    records: Iterable[Any],
    steps: list[MigrationStep],
    *,
    target_version: str,
) -> MigrationResult:
    """Apply a specific list of ``MigrationStep`` objects
    to a bundle, in order.

    This is the lower-level primitive; ``SchemaMigrator.migrate_bundle``
    uses it internally after path-finding.

    Args:
        records: an iterable of records.
        steps: a list of ``MigrationStep`` in application
            order. Each step is applied to records whose
            ``schema_version`` matches the step's
            ``from_version``.
        target_version: the schema version that the
            records should be at after all steps have been
            applied. Used to determine whether a record
            is "already at target" (no migration needed)
            vs. "needs migration."

    Returns:
        A :class:`MigrationResult` with the migrated
        bundle, the steps that were applied (deduplicated;
        only the steps that actually touched at least one
        record), and per-record counts.

    Raises:
        ValueError: if a record is at a version that
            none of the registered steps take as
            ``from_version``, or if a step's
            ``migrate_record`` callable raises.
    """
    records_list = list(records)
    result_records: list[Any] = []
    records_migrated = 0
    records_unchanged = 0
    steps_used: list[tuple[str, str]] = []

    for record in records_list:
        record_dict = _to_dict(record)
        current_version = str(record_dict.get("schema_version", DEFAULT_RECORD_VERSION))

        if current_version == target_version:
            result_records.append(record_dict)
            records_unchanged += 1
            continue

        # Walk the steps until we reach target_version.
        # The `steps` list is the chain; for each step
        # whose from_version matches the current version,
        # apply.
        path_index = _index_path(steps, current_version)
        if path_index is None:
            raise ValueError(
                f"no migration path for record at version {current_version!r} "
                f"to target {target_version!r}"
            )
        applied_any = False
        cursor = current_version
        while cursor != target_version:
            if path_index >= len(steps):
                raise ValueError(
                    f"migration path exhausted at version {cursor!r}; "
                    f"target was {target_version!r}"
                )
            step = steps[path_index]
            if step.from_version != cursor:
                raise ValueError(
                    f"migration path mismatch: expected step from {cursor!r}, "
                    f"got step from {step.from_version!r}"
                )
            try:
                record_dict = step.migrate_record(record_dict)
            except Exception as exc:
                raise ValueError(
                    f"migration step {step.from_version!r} -> {step.to_version!r} "
                    f"failed on record id={record_dict.get('id', MISSING_VERSION)!r}: {exc}"
                ) from exc
            record_dict["schema_version"] = step.to_version
            steps_used.append((step.from_version, step.to_version))
            applied_any = True
            cursor = step.to_version
            path_index += 1
        if applied_any:
            records_migrated += 1
        result_records.append(record_dict)

    # Deduplicate steps_used while preserving first-seen order.
    seen: set[tuple[str, str]] = set()
    deduped: list[tuple[str, str]] = []
    for s in steps_used:
        if s not in seen:
            seen.add(s)
            deduped.append(s)

    return MigrationResult(
        bundle=result_records,
        target_version=target_version,
        steps_applied=tuple(deduped),
        records_migrated=records_migrated,
        records_unchanged=records_unchanged,
    )


def migrate_bundle(
    bundle: Iterable[Any],
    *,
    target_version: str,
    migrator: SchemaMigrator | None = None,
) -> MigrationResult:
    """Migrate a bundle to ``target_version`` using the
    registry.

    Convenience wrapper. If ``migrator`` is ``None``, uses
    :func:`default_migrator` (which has no built-in
    migrations as of v1.0.0-alpha.2).

    Args:
        bundle: an iterable of records.
        target_version: the schema version to migrate to.
        migrator: an optional ``SchemaMigrator``. ``None``
            means "use the default empty registry."

    Returns:
        A :class:`MigrationResult` with the migrated
        bundle, the steps that were applied, and
        per-record counts.

    Raises:
        ValueError: if no migration path exists from
            any record's current version to
            ``target_version``.
    """
    if migrator is None:
        migrator = default_migrator()
    return migrator.migrate_bundle(bundle, target_version=target_version)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_dict(record: Any) -> dict[str, Any]:
    """Convert a record to a dict for migration.

    Dataclass records are converted via
    :func:`dataclasses.asdict`. Mappings are copied to a
    plain dict. Other types are coerced into a dict via
    :class:`dict` (which works for any Mapping).
    """
    if dataclasses.is_dataclass(record) and not isinstance(record, type):
        return dict(dataclasses.asdict(record))
    return dict(record)


def _infer_start_version(records: list[Any]) -> str:
    """Infer the start version of a bundle for path-finding.

    Returns the version of the first record that has a
    ``schema_version`` field. If all records lack the
    field, returns :data:`DEFAULT_RECORD_VERSION`.
    """
    for record in records:
        d = _to_dict(record)
        v = d.get("schema_version")
        if isinstance(v, str) and v:
            return v
    return DEFAULT_RECORD_VERSION


def _index_path(
    steps: list[MigrationStep], from_version: str
) -> int | None:
    """Return the index in ``steps`` whose
    ``from_version`` matches ``from_version``, or ``None``.

    The first such step is returned; on a chained path
    like ``[1.0.0→1.0.5, 1.0.5→1.1.0]``, the
    ``_index_path`` for ``1.0.0`` returns ``0`` and for
    ``1.0.5`` returns ``1``.
    """
    for i, step in enumerate(steps):
        if step.from_version == from_version:
            return i
    return None
