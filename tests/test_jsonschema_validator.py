"""Tests for the optional jsonschema-backed validator.

These tests are skipped unless the ``jsonschema`` package is importable.
The library stays stdlib-only at runtime; ``jsonschema`` is an opt-in
extra for users who want JSON-Schema-level validation in addition to
the Python contract validation.
"""

from __future__ import annotations

import unittest
from dataclasses import asdict

from agent_memory_contracts import (
    SourceRecord,
    make_source_id,
    make_span_id,
)

# Probe the optional dep once at import time. Tests that need it
# are wrapped in @unittest.skipUnless. The jsonschema_validator
# module itself is always importable (graceful degradation is
# the design), so we import it unconditionally; only the
# `jsonschema` symbol is gated.
try:
    import jsonschema  # noqa: F401
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

from agent_memory_contracts import jsonschema_validator as jv


def _valid_source_dict() -> dict:
    source_id = make_source_id(
        "chatgpt_conversation", "https://example.com/x", "a" * 64
    )
    return {
        "id": source_id,
        "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": "Test source",
        "origin_uri": "https://example.com/x",
        "raw_ref": {"kind": "external_uri", "value": "https://example.com/x"},
        "content_hash_sha256": "a" * 64,
        "captured_at": "2026-05-30T12:00:00Z",
        "observed_at": "2026-05-30T12:00:00Z",
        "author_or_sender": "user",
        "participants": ["user"],
        "privacy_class": "internal",
        "custody_status": "external_pointer",
        "parser_version": "v1",
        "metadata": {},
    }


@unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
class LoadSchemaTests(unittest.TestCase):
    def test_known_schema_loads(self):
        schema = jv.load_schema("taste_card")
        self.assertEqual(schema["title"], "TasteCard")
        self.assertIn("properties", schema)
        self.assertIn("required", schema)

    def test_unknown_schema_raises_value_error(self):
        with self.assertRaises(jv.SchemaNotFoundError) as ctx:
            jv.load_schema("not_a_real_schema")
        self.assertIn("not_a_real_schema", str(ctx.exception))
        # The error message should list the valid names so users
        # can see what they could have meant.
        self.assertIn("taste_card", str(ctx.exception))

    def test_every_advertised_schema_loads(self):
        # The published schema registry must match the files
        # actually shipped in the package. Catches packaging bugs
        # where a new schema is added to the registry but the
        # file is forgotten, or vice versa.
        for name in jv.VALID_SCHEMA_NAMES:
            with self.subTest(name=name):
                schema = jv.load_schema(name)
                self.assertIn("title", schema)
                self.assertIn("type", schema)
                self.assertEqual(schema["type"], "object")

    def test_schema_count_matches_registry(self):
        # If this fails, either the registry or the package
        # has drifted; update both.
        self.assertEqual(
            len(jv.VALID_SCHEMA_NAMES), 23,
            "VALID_SCHEMA_NAMES must match the 23 shipped schemas"
        )


@unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
class ValidateInstanceTests(unittest.TestCase):
    def setUp(self):
        self.source = _valid_source_dict()

    def test_valid_instance_passes_silently(self):
        # Default: raise_on_error=True, returns empty list.
        errors = jv.validate_instance(self.source, "source_record")
        self.assertEqual(errors, [])

    def test_valid_instance_passes_with_raise_false(self):
        errors = jv.validate_instance(
            self.source, "source_record", raise_on_error=False
        )
        self.assertEqual(errors, [])

    def test_missing_required_field_rejected(self):
        bad = {k: v for k, v in self.source.items() if k != "title"}
        errors = jv.validate_instance(
            bad, "source_record", raise_on_error=False
        )
        self.assertTrue(
            any("title" in e for e in errors),
            f"expected title error, got: {errors}",
        )

    def test_bad_enum_value_rejected(self):
        bad = dict(self.source)
        bad["privacy_class"] = "made_up_class"
        errors = jv.validate_instance(
            bad, "source_record", raise_on_error=False
        )
        self.assertTrue(
            any("privacy_class" in e for e in errors),
            f"expected privacy_class error, got: {errors}",
        )

    def test_additional_properties_rejected(self):
        bad = dict(self.source, random_extra_field="surprise")
        errors = jv.validate_instance(
            bad, "source_record", raise_on_error=False
        )
        self.assertTrue(
            any("random_extra_field" in e for e in errors),
            f"expected additionalProperties error, got: {errors}",
        )

    def test_raise_on_error_true_raises(self):
        bad = {"not": "a source record at all"}
        with self.assertRaises(jsonschema.ValidationError):
            jv.validate_instance(bad, "source_record", raise_on_error=True)

    def test_roundtrip_from_python_dataclass(self):
        # Build a real SourceRecord via the Python contract,
        # asdict it, and verify the JSON Schema accepts it.
        # This is the polyglot interop guarantee: a Python
        # service that produces records will produce records
        # that a TypeScript/Rust/Go consumer accepts.
        source = SourceRecord.from_dict(self.source)
        errors = jv.validate_instance(
            asdict(source), "source_record", raise_on_error=False
        )
        self.assertEqual(
            errors, [],
            f"dataclass -> JSON Schema roundtrip failed: {errors}",
        )

    def test_error_path_points_to_offending_field(self):
        # An error in a nested field should produce a path
        # that includes the field, not just "<root>".
        bad = dict(self.source)
        bad["raw_ref"] = {"kind": "not_a_real_kind", "value": "x"}
        errors = jv.validate_instance(
            bad, "source_record", raise_on_error=False
        )
        self.assertTrue(errors, "expected at least one error")
        # At least one error should mention raw_ref (the nested
        # object path) or "kind" (the offending field).
        combined = " | ".join(errors)
        self.assertTrue(
            "raw_ref" in combined or "kind" in combined,
            f"expected path/breadcrumb, got: {errors}",
        )

    def test_unknown_schema_raises(self):
        with self.assertRaises(jv.SchemaNotFoundError):
            jv.validate_instance(self.source, "no_such_schema")


@unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
class ValidateBundleTests(unittest.TestCase):
    def test_all_valid_bundle_returns_empty(self):
        records = [("source_record", _valid_source_dict())]
        errors = jv.validate_bundle(records, raise_on_error=False)
        self.assertEqual(errors, {})

    def test_mixed_bundle_reports_per_schema_errors(self):
        good = {"id": "src_aaaa", "schema_version": "1.0.0"}
        errors = jv.validate_bundle(
            [("source_record", good)],
            raise_on_error=False,
        )
        self.assertIn("source_record", errors)
        self.assertGreater(
            len(errors["source_record"]), 0,
            "expected validation errors for the minimal dict",
        )

    def test_raise_on_error_true_raises(self):
        with self.assertRaises(jsonschema.ValidationError):
            jv.validate_bundle(
                [("source_record", {"junk": "data"})],
                raise_on_error=True,
            )

    def test_empty_bundle_is_a_noop(self):
        errors = jv.validate_bundle([], raise_on_error=False)
        self.assertEqual(errors, {})


@unittest.skipUnless(HAS_JSONSCHEMA, "jsonschema not installed")
class AvailabilityTests(unittest.TestCase):
    def test_is_available_true(self):
        # We're inside @skipUnless(HAS_JSONSCHEMA), so it must be True.
        self.assertTrue(jv.is_available())


class MissingDependencyTests(unittest.TestCase):
    """Verify the graceful-degradation path: when jsonschema is not
    installed, the module still imports and the public functions
    raise a clear error rather than crashing with ModuleNotFoundError
    deep in a stack frame.

    These tests do NOT need jsonschema to be importable; they validate
    the public-API contract.
    """

    def test_module_imports_without_jsonschema(self):
        # The import at the top of this test file already proved this.
        # We just need a sentinel: if we got here, the module
        # imported successfully regardless of jsonschema's presence.
        from agent_memory_contracts import jsonschema_validator  # noqa: F401
        self.assertTrue(hasattr(jsonschema_validator, "validate_instance"))

    def test_validate_instance_raises_actionable_import_error(self):
        # We can only assert the error class if jsonschema is missing.
        if HAS_JSONSCHEMA:
            self.skipTest("jsonschema is installed; cannot test the missing path")
        with self.assertRaises(ImportError) as ctx:
            jv.validate_instance({}, "source_record")
        msg = str(ctx.exception)
        self.assertIn("jsonschema", msg)
        self.assertIn("pip install", msg)


if __name__ == "__main__":
    unittest.main()
