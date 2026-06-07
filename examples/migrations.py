"""Worked example for the schema migration framework.

Run from the repository root::

    PYTHONPATH=src python examples/migrations.py

Demonstrates the migration framework: registering steps,
applying chains, idempotency, and mixed-version bundles.

The library ships with no concrete migrations (the
schemas are stable at "1.0.0" as of v1.0.0-alpha.2). The
example defines three sample migrations to demonstrate
the framework; in production, the v1.1.0 sprint will
register the real migrations as part of the schema bump.

.. versionadded:: 1.0.0-alpha.2
"""

from __future__ import annotations

import sys
from typing import Any

from agent_memory_contracts import (
    CURRENT_SCHEMA_VERSION,
    MigrationStep,
    SchemaMigrator,
    default_migrator,
)


def _add_parser_version(record: dict[str, Any]) -> dict[str, Any]:
    r = dict(record)
    r["parser_version"] = "v2"
    return r


def _add_evidence_quality_score(record: dict[str, Any]) -> dict[str, Any]:
    r = dict(record)
    r["evidence_quality_score"] = 1.0
    return r


def _rename_parsed_by(record: dict[str, Any]) -> dict[str, Any]:
    r = dict(record)
    if "parser_version" in r:
        r["parsed_by"] = r.pop("parser_version")
    return r


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def demo_default_migrator() -> None:
    section("1. default_migrator() - empty registry")
    m = default_migrator()
    print(f"registered steps: {len(m.registered_steps())}")
    print(f"current schema version: {CURRENT_SCHEMA_VERSION}")


def demo_two_step_chain() -> None:
    section("2. Two-step chain (1.0.0 -> 1.0.5 -> 1.1.0)")
    m = SchemaMigrator()
    m.register(MigrationStep("1.0.0", "1.0.5", "add parser_version", _add_parser_version))
    m.register(MigrationStep("1.0.5", "1.1.0", "add evidence_quality_score", _add_evidence_quality_score))

    bundle = [
        {"id": "src_1", "schema_version": "1.0.0", "title": "first"},
        {"id": "src_2", "schema_version": "1.0.0", "title": "second"},
    ]
    result = m.migrate_bundle(bundle, target_version="1.1.0")
    print(f"records_migrated:  {result.records_migrated}")
    print(f"records_unchanged: {result.records_unchanged}")
    print(f"steps_applied:     {result.steps_applied}")
    print("first record after migration:")
    for k, v in result.bundle[0].items():
        print(f"  {k}: {v!r}")


def demo_three_step_chain() -> None:
    section("3. Three-step chain with field rename (1.0.0 -> 1.2.0)")
    m = SchemaMigrator()
    m.register(MigrationStep("1.0.0", "1.0.5", "add parser_version", _add_parser_version))
    m.register(MigrationStep("1.0.5", "1.1.0", "add evidence_quality_score", _add_evidence_quality_score))
    m.register(MigrationStep("1.1.0", "1.2.0", "rename parser_version -> parsed_by", _rename_parsed_by))

    bundle = [{"id": "src_x", "schema_version": "1.0.0", "title": "test"}]
    result = m.migrate_bundle(bundle, target_version="1.2.0")
    print(f"final schema_version: {result.bundle[0]['schema_version']!r}")
    print(f"parser_version present: {'parser_version' in result.bundle[0]}")
    print(f"parsed_by: {result.bundle[0].get('parsed_by')!r}")
    print(f"evidence_quality_score: {result.bundle[0].get('evidence_quality_score')!r}")


def demo_path_finding() -> None:
    section("4. Path-finding (find_path)")
    m = SchemaMigrator()
    m.register(MigrationStep("1.0.0", "1.0.5", "step 1", _add_parser_version))
    m.register(MigrationStep("1.0.5", "1.1.0", "step 2", _add_evidence_quality_score))

    path = m.find_path("1.0.0", "1.1.0")
    print(f"path 1.0.0 -> 1.1.0: {len(path)} step(s)")
    for step in path:
        print(f"  {step.from_version} -> {step.to_version}: {step.description}")

    try:
        m.find_path("1.0.0", "2.0.0")
    except ValueError as e:
        print(f"\nno path 1.0.0 -> 2.0.0: {e}")


def demo_idempotency() -> None:
    section("5. Idempotency")
    m = SchemaMigrator()
    m.register(MigrationStep("1.0.0", "1.0.5", "step 1", _add_parser_version))
    m.register(MigrationStep("1.0.5", "1.1.0", "step 2", _add_evidence_quality_score))

    bundle = [{"id": "src_x", "schema_version": "1.0.0", "title": "test"}]
    result1 = m.migrate_bundle(bundle, target_version="1.1.0")
    print(f"first call:  records_migrated={result1.records_migrated}, "
          f"records_unchanged={result1.records_unchanged}")
    result2 = m.migrate_bundle(result1.bundle, target_version="1.1.0")
    print(f"second call: records_migrated={result2.records_migrated}, "
          f"records_unchanged={result2.records_unchanged}")
    print(f"second-call steps_applied: {result2.steps_applied}")


def demo_mixed_version() -> None:
    section("6. Mixed-version bundle")
    m = SchemaMigrator()
    m.register(MigrationStep("1.0.0", "1.0.5", "step 1", _add_parser_version))
    m.register(MigrationStep("1.0.5", "1.1.0", "step 2", _add_evidence_quality_score))

    bundle = [
        {"id": "a", "schema_version": "1.0.0", "title": "old"},
        {"id": "b", "schema_version": "1.0.5", "title": "middle"},
        {"id": "c", "schema_version": "1.1.0", "title": "new"},
    ]
    result = m.migrate_bundle(bundle, target_version="1.1.0")
    print(f"records_migrated:  {result.records_migrated} (a and b)")
    print(f"records_unchanged: {result.records_unchanged} (c)")
    for r in result.bundle:
        print(f"  {r['id']}: schema_version={r['schema_version']!r}")


def main(argv: list[str] | None = None) -> int:
    demo_default_migrator()
    demo_two_step_chain()
    demo_three_step_chain()
    demo_path_finding()
    demo_idempotency()
    demo_mixed_version()
    print()
    print("=" * 70)
    print("Schema migration example complete.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
