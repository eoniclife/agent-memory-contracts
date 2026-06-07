"""Tests for the schema migration framework.

Coverage targets (per docs/specs/sprint_24b_schema_migration.md):

1. MigrationStep invariants (frozen, valid version
   pair, callable required).
2. MigrationResult is frozen.
3. SchemaMigrator.register detects double-registration.
4. SchemaMigrator.find_path returns the chain in order
   (BFS); raises on no path.
5. SchemaMigrator.has_path returns True/False correctly.
6. SchemaMigrator.migrate_bundle applies the chain,
   sets schema_version correctly, returns the right
   MigrationResult.
7. Idempotency: second call is a no-op.
8. Mixed-version bundle: each record migrated
   independently.
9. Records already at target: passed through unchanged.
10. Dataclass input: conversion happens, output is a dict.
11. Backward target raises.
12. Migration step that raises propagates.
13. apply_migrations: lower-level API works directly.
14. default_migrator: returns empty registry.
15. migrate_bundle: top-level convenience works.
16. Public API exports.
"""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any

from agent_memory_contracts import (
    CURRENT_SCHEMA_VERSION,
    MigrationResult,
    MigrationStep,
    SchemaMigrator,
    apply_migrations,
    default_migrator,
    migrate_bundle,
)

from .fixtures import T_CAPTURED, T_DECIDED, build_source_and_span


# ---------------------------------------------------------------------------
# Sample migration step builders
# ---------------------------------------------------------------------------


def _add_parser_version(record: dict[str, Any]) -> dict[str, Any]:
    """A 1.0.0 -> 1.0.5 step: add a `parser_version` field."""
    r = dict(record)
    r["parser_version"] = "v2"
    return r


def _add_evidence_quality_score(record: dict[str, Any]) -> dict[str, Any]:
    """A 1.0.5 -> 1.1.0 step: add an `evidence_quality_score` field."""
    r = dict(record)
    r["evidence_quality_score"] = 1.0
    return r


def _rename_parsed_by(record: dict[str, Any]) -> dict[str, Any]:
    """A 1.1.0 -> 1.2.0 step: rename `parser_version` to `parsed_by`."""
    r = dict(record)
    if "parser_version" in r:
        r["parsed_by"] = r.pop("parser_version")
    return r


def _step_1_0_to_1_0_5() -> MigrationStep:
    return MigrationStep("1.0.0", "1.0.5", "add parser_version", _add_parser_version)


def _step_1_0_5_to_1_1_0() -> MigrationStep:
    return MigrationStep("1.0.5", "1.1.0", "add evidence_quality_score", _add_evidence_quality_score)


def _step_1_1_0_to_1_2_0() -> MigrationStep:
    return MigrationStep("1.1.0", "1.2.0", "rename parser_version -> parsed_by", _rename_parsed_by)


def _build_two_step_migrator() -> SchemaMigrator:
    m = SchemaMigrator()
    m.register(_step_1_0_to_1_0_5())
    m.register(_step_1_0_5_to_1_1_0())
    return m


def _build_three_step_migrator() -> SchemaMigrator:
    m = _build_two_step_migrator()
    m.register(_step_1_1_0_to_1_2_0())
    return m


def _build_source_dict(version: str = "1.0.0") -> dict[str, Any]:
    src, _ = build_source_and_span()
    d = dataclasses.asdict(src)
    d["schema_version"] = version
    return d


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigrationStepInvariants(unittest.TestCase):
    """MigrationStep validates its fields at construction."""

    def test_construction(self) -> None:
        step = _step_1_0_to_1_0_5()
        self.assertEqual(step.from_version, "1.0.0")
        self.assertEqual(step.to_version, "1.0.5")
        self.assertEqual(step.description, "add parser_version")
        self.assertTrue(callable(step.migrate_record))

    def test_frozen(self) -> None:
        step = _step_1_0_to_1_0_5()
        with self.assertRaises(Exception):
            step.from_version = "2.0.0"  # type: ignore[misc]

    def test_empty_from_version(self) -> None:
        with self.assertRaises(ValueError):
            MigrationStep("", "1.1.0", "x", lambda r: r)

    def test_empty_to_version(self) -> None:
        with self.assertRaises(ValueError):
            MigrationStep("1.0.0", "", "x", lambda r: r)

    def test_same_from_to_raises(self) -> None:
        with self.assertRaises(ValueError):
            MigrationStep("1.0.0", "1.0.0", "x", lambda r: r)

    def test_empty_description(self) -> None:
        with self.assertRaises(ValueError):
            MigrationStep("1.0.0", "1.1.0", "", lambda r: r)

    def test_non_callable_migrator(self) -> None:
        with self.assertRaises(ValueError):
            MigrationStep("1.0.0", "1.1.0", "x", "not callable")  # type: ignore[arg-type]


class TestMigrationResultIsFrozen(unittest.TestCase):
    """MigrationResult is a frozen dataclass."""

    def test_frozen(self) -> None:
        result = MigrationResult(bundle=[], target_version="1.0.0")
        with self.assertRaises(Exception):
            result.target_version = "2.0.0"  # type: ignore[misc]


class TestSchemaMigratorRegistration(unittest.TestCase):
    """register detects double-registration."""

    def test_register_succeeds(self) -> None:
        m = SchemaMigrator()
        m.register(_step_1_0_to_1_0_5())
        self.assertEqual(len(m.registered_steps()), 1)

    def test_register_detects_double_registration(self) -> None:
        m = SchemaMigrator()
        m.register(_step_1_0_to_1_0_5())
        with self.assertRaises(ValueError):
            m.register(_step_1_0_to_1_0_5())  # same (from, to)

    def test_registered_steps_preserves_order(self) -> None:
        m = SchemaMigrator()
        m.register(_step_1_0_to_1_0_5())
        m.register(_step_1_0_5_to_1_1_0())
        steps = m.registered_steps()
        self.assertEqual(steps[0].from_version, "1.0.0")
        self.assertEqual(steps[1].from_version, "1.0.5")


class TestSchemaMigratorFindPath(unittest.TestCase):
    """find_path returns the chain in order; raises on no path."""

    def test_same_version_empty_path(self) -> None:
        m = _build_two_step_migrator()
        self.assertEqual(m.find_path("1.0.0", "1.0.0"), [])

    def test_direct_step(self) -> None:
        m = _build_two_step_migrator()
        path = m.find_path("1.0.0", "1.0.5")
        self.assertEqual(len(path), 1)
        self.assertEqual(path[0].from_version, "1.0.0")
        self.assertEqual(path[0].to_version, "1.0.5")

    def test_two_step_chain(self) -> None:
        m = _build_two_step_migrator()
        path = m.find_path("1.0.0", "1.1.0")
        self.assertEqual(len(path), 2)
        self.assertEqual(path[0].from_version, "1.0.0")
        self.assertEqual(path[1].from_version, "1.0.5")

    def test_three_step_chain(self) -> None:
        m = _build_three_step_migrator()
        path = m.find_path("1.0.0", "1.2.0")
        self.assertEqual(len(path), 3)

    def test_no_path_raises(self) -> None:
        m = SchemaMigrator()
        with self.assertRaises(ValueError):
            m.find_path("1.0.0", "1.1.0")

    def test_empty_version_raises(self) -> None:
        m = SchemaMigrator()
        with self.assertRaises(ValueError):
            m.find_path("", "1.0.0")
        with self.assertRaises(ValueError):
            m.find_path("1.0.0", "")

    def test_has_path(self) -> None:
        m = _build_two_step_migrator()
        self.assertTrue(m.has_path("1.0.0", "1.0.0"))
        self.assertTrue(m.has_path("1.0.0", "1.1.0"))
        self.assertFalse(m.has_path("1.0.0", "1.2.0"))
        self.assertFalse(m.has_path("2.0.0", "2.0.1"))


class TestApplyMigrations(unittest.TestCase):
    """apply_migrations: lower-level API works directly."""

    def test_two_step_chain(self) -> None:
        records = [
            {"id": "src_1", "schema_version": "1.0.0", "title": "first"},
            {"id": "src_2", "schema_version": "1.0.0", "title": "second"},
        ]
        result = apply_migrations(
            records, [_step_1_0_to_1_0_5(), _step_1_0_5_to_1_1_0()], target_version="1.1.0"
        )
        self.assertEqual(result.records_migrated, 2)
        self.assertEqual(result.records_unchanged, 0)
        self.assertEqual(result.steps_applied, (("1.0.0", "1.0.5"), ("1.0.5", "1.1.0")))
        for r in result.bundle:
            self.assertEqual(r["schema_version"], "1.1.0")
            self.assertEqual(r["parser_version"], "v2")
            self.assertEqual(r["evidence_quality_score"], 1.0)

    def test_records_already_at_target(self) -> None:
        records = [{"id": "x", "schema_version": "1.1.0", "title": "t"}]
        result = apply_migrations(records, [], target_version="1.1.0")
        self.assertEqual(result.records_migrated, 0)
        self.assertEqual(result.records_unchanged, 1)
        self.assertEqual(result.bundle[0]["schema_version"], "1.1.0")

    def test_mixed_version_bundle(self) -> None:
        records = [
            {"id": "a", "schema_version": "1.0.0", "title": "old"},
            {"id": "b", "schema_version": "1.0.5", "title": "middle"},
            {"id": "c", "schema_version": "1.1.0", "title": "new"},
        ]
        result = apply_migrations(
            records, [_step_1_0_to_1_0_5(), _step_1_0_5_to_1_1_0()], target_version="1.1.0"
        )
        self.assertEqual(result.records_migrated, 2)  # a and b; c is unchanged
        self.assertEqual(result.records_unchanged, 1)
        for r in result.bundle:
            self.assertEqual(r["schema_version"], "1.1.0")

    def test_missing_schema_version_defaults(self) -> None:
        # Records without a schema_version are treated as 1.0.0.
        records = [{"id": "a", "title": "t"}]
        result = apply_migrations(
            records, [_step_1_0_to_1_0_5(), _step_1_0_5_to_1_1_0()], target_version="1.1.0"
        )
        self.assertEqual(result.bundle[0]["schema_version"], "1.1.0")

    def test_unknown_version_raises(self) -> None:
        records = [{"id": "a", "schema_version": "2.0.0", "title": "future"}]
        with self.assertRaises(ValueError):
            apply_migrations(records, [_step_1_0_to_1_0_5()], target_version="1.1.0")

    def test_migration_step_raising_propagates(self) -> None:
        def bad_step(record: dict[str, Any]) -> dict[str, Any]:
            raise RuntimeError("nope")

        records = [{"id": "a", "schema_version": "1.0.0", "title": "t"}]
        step = MigrationStep("1.0.0", "1.1.0", "broken", bad_step)
        with self.assertRaises(ValueError) as ctx:
            apply_migrations(records, [step], target_version="1.1.0")
        self.assertIn("migration step", str(ctx.exception))
        self.assertIn("nope", str(ctx.exception))

    def test_empty_bundle(self) -> None:
        result = apply_migrations(
            [], [_step_1_0_to_1_0_5(), _step_1_0_5_to_1_1_0()], target_version="1.1.0"
        )
        self.assertEqual(result.bundle, [])
        self.assertEqual(result.records_migrated, 0)
        self.assertEqual(result.records_unchanged, 0)
        self.assertEqual(result.steps_applied, ())


class TestMigrateBundle(unittest.TestCase):
    """SchemaMigrator.migrate_bundle: top-level integration."""

    def test_two_step_via_migrator(self) -> None:
        m = _build_two_step_migrator()
        bundle = [_build_source_dict("1.0.0") for _ in range(3)]
        result = m.migrate_bundle(bundle, target_version="1.1.0")
        self.assertEqual(len(result.bundle), 3)
        self.assertEqual(result.records_migrated, 3)
        for r in result.bundle:
            self.assertEqual(r["schema_version"], "1.1.0")

    def test_no_path_raises(self) -> None:
        m = SchemaMigrator()
        with self.assertRaises(ValueError):
            m.migrate_bundle([], target_version="1.1.0")

    def test_idempotent(self) -> None:
        m = _build_two_step_migrator()
        bundle = [_build_source_dict("1.0.0")]
        result1 = m.migrate_bundle(bundle, target_version="1.1.0")
        # Second call: every record is already at 1.1.0; no-op.
        result2 = m.migrate_bundle(result1.bundle, target_version="1.1.0")
        self.assertEqual(result2.records_migrated, 0)
        self.assertEqual(result2.records_unchanged, 1)
        self.assertEqual(result2.steps_applied, ())

    def test_three_step_chain(self) -> None:
        m = _build_three_step_migrator()
        bundle = [_build_source_dict("1.0.0")]
        result = m.migrate_bundle(bundle, target_version="1.2.0")
        self.assertEqual(result.bundle[0]["schema_version"], "1.2.0")
        # The third step renames parser_version to parsed_by.
        self.assertNotIn("parser_version", result.bundle[0])
        self.assertEqual(result.bundle[0]["parsed_by"], "v2")
        self.assertEqual(result.bundle[0]["evidence_quality_score"], 1.0)

    def test_empty_target_version_raises(self) -> None:
        m = SchemaMigrator()
        with self.assertRaises(ValueError):
            m.migrate_bundle([], target_version="")


class TestDataclassInput(unittest.TestCase):
    """Dataclass records are converted to dicts at the boundary."""

    def test_dataclass_input(self) -> None:
        m = _build_two_step_migrator()
        from agent_memory_contracts import SourceRecord
        # The build_source_and_span fixture returns a real
        # SourceRecord dataclass.
        src, _ = build_source_and_span()
        # The dataclass has no schema_version field; the
        # framework treats it as 1.0.0 (the default).
        result = m.migrate_bundle([src], target_version="1.1.0")
        self.assertEqual(len(result.bundle), 1)
        # Output is a dict.
        self.assertIsInstance(result.bundle[0], dict)
        self.assertEqual(result.bundle[0]["schema_version"], "1.1.0")


class TestDefaultMigrator(unittest.TestCase):
    """default_migrator returns an empty registry."""

    def test_empty_registry(self) -> None:
        m = default_migrator()
        self.assertEqual(m.registered_steps(), ())

    def test_migrate_via_default_no_path_raises(self) -> None:
        bundle = [_build_source_dict("1.0.0")]
        with self.assertRaises(ValueError):
            migrate_bundle(bundle, target_version="2.0.0")

    def test_migrate_via_default_to_current_is_noop(self) -> None:
        # target_version == current library version: no
        # migration needed; all records unchanged.
        bundle = [_build_source_dict("1.0.0"), _build_source_dict("1.0.0")]
        result = migrate_bundle(bundle, target_version="1.0.0")
        self.assertEqual(result.records_migrated, 0)
        self.assertEqual(result.records_unchanged, 2)
        self.assertEqual(result.steps_applied, ())


class TestMigrateBundleConvenience(unittest.TestCase):
    """migrate_bundle (top-level) works with a custom migrator."""

    def test_with_explicit_migrator(self) -> None:
        m = _build_two_step_migrator()
        bundle = [_build_source_dict("1.0.0")]
        result = migrate_bundle(bundle, target_version="1.1.0", migrator=m)
        self.assertEqual(result.bundle[0]["schema_version"], "1.1.0")

    def test_with_default_migrator(self) -> None:
        bundle = [_build_source_dict("1.0.0")]
        # target_version=1.0.0 (current) is a no-op.
        result = migrate_bundle(bundle, target_version="1.0.0")
        self.assertEqual(result.records_migrated, 0)
        self.assertEqual(result.records_unchanged, 1)


class TestCurrentSchemaVersion(unittest.TestCase):
    """CURRENT_SCHEMA_VERSION is exposed and equals "1.0.0"."""

    def test_current_version(self) -> None:
        self.assertEqual(CURRENT_SCHEMA_VERSION, "1.0.0")


class TestPublicApi(unittest.TestCase):
    """All v1.0.0-alpha.2 names are exported."""

    def test_v100a2_exports_present(self) -> None:
        import agent_memory_contracts as a
        for name in (
            "CURRENT_SCHEMA_VERSION",
            "MigrationStep",
            "MigrationResult",
            "SchemaMigrator",
            "apply_migrations",
            "default_migrator",
            "migrate_bundle",
        ):
            self.assertTrue(hasattr(a, name), f"missing export: {name}")


if __name__ == "__main__":
    unittest.main()
