"""Tests for the MCP server integration.

The integration is optional; tests are gated on whether
``fastmcp`` is importable. When it is not, the tests skip
with a clear message.
"""

from __future__ import annotations

import asyncio
import unittest

import pytest

pytest.importorskip("fastmcp")

from agent_memory_contracts import (  # noqa: E402
    ContextPackTask,
)
from agent_memory_contracts.integrations.mcp import (  # noqa: E402
    ContractsMCPServer,
    MCPConfig,
    _evaluate_access_scope,
    _load_schema_names,
    _scope_from_dict,
    _validate_bundle,
    _validate_bundle_integrity,
    _records_to_iter,
)


class TestMCPConfig(unittest.TestCase):
    """The config dataclass has the right defaults."""

    def test_defaults(self) -> None:
        cfg = MCPConfig()
        self.assertEqual(cfg.server_name, "agent-memory-contracts")
        self.assertEqual(cfg.transport, "stdio")
        self.assertEqual(cfg.port, 8765)
        self.assertEqual(cfg.maximum_privacy_class, "internal")
        self.assertTrue(cfg.fail_closed_unknown_privacy)

    def test_custom(self) -> None:
        cfg = MCPConfig(
            server_name="x",
            transport="http",
            port=9000,
            maximum_privacy_class="private",
            allowed_tools=frozenset({"validate_bundle_integrity"}),
            http_security_notice_acknowledged=True,
        )
        self.assertEqual(cfg.server_name, "x")
        self.assertEqual(cfg.transport, "http")
        self.assertEqual(cfg.port, 9000)
        self.assertEqual(cfg.maximum_privacy_class, "private")
        self.assertEqual(cfg.allowed_tools, frozenset({"validate_bundle_integrity"}))

    def test_invalid_maximum_privacy_class_raises(self) -> None:
        with self.assertRaises(ValueError):
            MCPConfig(maximum_privacy_class="classified")


class TestSchemaResources(unittest.TestCase):
    """The server exposes JSON Schemas as resources."""

    def test_load_schema_names(self) -> None:
        names = _load_schema_names()
        self.assertGreater(len(names), 0)
        # The library has 23 schemas at v1.0.0
        self.assertGreaterEqual(len(names), 23)

    def test_load_specific_schema(self) -> None:
        # All schema names have ".schema" suffix
        from agent_memory_contracts.integrations.mcp import _load_schema

        names = _load_schema_names()
        first = names[0]
        schema = _load_schema(first)
        self.assertIsNotNone(schema)
        self.assertIn("$schema", schema)

    def test_missing_schema(self) -> None:
        from agent_memory_contracts.integrations.mcp import _load_schema

        self.assertIsNone(_load_schema("nonexistent_schema"))


class TestValidateBundleHelper(unittest.TestCase):
    """The ``_validate_bundle`` helper validates a dict bundle."""

    def test_empty_bundle(self) -> None:
        errors = _validate_bundle({})
        # Empty bundle: no errors
        for plane, plane_errors in errors.items():
            self.assertEqual(
                plane_errors, [],
                f"plane {plane} had errors: {plane_errors}",
            )

    def test_invalid_source_record(self) -> None:
        bad_source = {
            "id": "src_1",
            # missing required fields
        }
        errors = _validate_bundle({"source_records": [bad_source]})
        self.assertGreater(len(errors["source_records"]), 0)

    def test_invalid_plane_type(self) -> None:
        errors = _validate_bundle({"source_records": "not a list"})
        self.assertIn("source_records", errors)
        self.assertGreater(len(errors["source_records"]), 0)


class TestValidateBundleIntegrityHelper(unittest.TestCase):
    """The integrity helper separates schema and graph validation."""

    def test_empty_bundle(self) -> None:
        report = _validate_bundle_integrity({})
        self.assertTrue(report["valid"])
        self.assertIn("schema", report)
        self.assertIn("integrity", report)
        self.assertEqual(report["integrity"]["ledger"]["errors"], [])

    def test_bad_ledger_reference_is_not_hidden_by_schema_tool_name(self) -> None:
        report = _validate_bundle_integrity(
            {
                "fact_ledger_entries": [
                    {
                        "id": "fact_x",
                        "schema_version": "1.1.0",
                        "ledger_type": "fact",
                        "status": "active",
                        "confidence": "high",
                        "scope": "global",
                        "source_record_ids": ["src_missing"],
                        "episode_record_ids": [],
                        "evidence_span_ids": [],
                        "candidate_ids": [],
                        "reducer_decision_id": "redmem_missing",
                        "observed_at": None,
                        "asserted_at": "2026-01-01T00:00:00Z",
                        "valid_from": "2026-01-01T00:00:00Z",
                        "valid_until": None,
                        "stale_after": None,
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                        "supersedes": [],
                        "superseded_by": [],
                        "metadata": {"human_asserted": True},
                        "freshness_score": None,
                    }
                ]
            }
        )
        self.assertFalse(report["valid"])
        self.assertGreater(len(report["integrity"]["ledger"]["errors"]), 0)


class TestAccessScopeHelper(unittest.TestCase):
    """MCP access evaluation applies server-side safety policy."""

    def test_server_maximum_privacy_caps_client_request(self) -> None:
        result = _evaluate_access_scope(
            {
                "source_records": [
                    {"id": "public", "privacy_class": "public"},
                    {"id": "private", "privacy_class": "private"},
                ]
            },
            {"name": "owner", "max_privacy_class": "highly_sensitive"},
            MCPConfig(maximum_privacy_class="internal"),
        )
        self.assertEqual(result["effective_scope"]["max_privacy_class"], "internal")
        self.assertEqual(len(result["allowed_records"]), 1)
        self.assertEqual(result["allowed_records"][0]["id"], "public")

    def test_unknown_requested_privacy_fails_closed_by_default(self) -> None:
        with self.assertRaises(ValueError):
            _scope_from_dict(
                {"max_privacy_class": "classified"},
                MCPConfig(),
            )

    def test_unknown_requested_privacy_can_use_compatibility_coercion(self) -> None:
        scope = _scope_from_dict(
            {"max_privacy_class": "classified"},
            MCPConfig(fail_closed_unknown_privacy=False),
        )
        self.assertEqual(scope.max_privacy_class, "internal")

    def test_unknown_record_privacy_is_dropped_when_fail_closed(self) -> None:
        result = _evaluate_access_scope(
            {"source_records": [{"id": "x", "privacy_class": "classified"}]},
            {"max_privacy_class": "highly_sensitive"},
            MCPConfig(maximum_privacy_class="highly_sensitive"),
        )
        self.assertEqual(result["summary"]["dropped"], 1)
        self.assertIn("fail_closed_unknown_privacy", result["decisions"][0]["reason"])

    def test_malformed_allowed_record_types_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            _scope_from_dict(
                {"allowed_record_types": "source_record"},
                MCPConfig(),
            )

    def test_malformed_allowed_record_types_raises_in_compatibility_mode(self) -> None:
        with self.assertRaises(ValueError):
            _scope_from_dict(
                {"allowed_record_types": "source_record"},
                MCPConfig(fail_closed_unknown_privacy=False),
            )

    def test_malformed_plane_does_not_become_allowed_records(self) -> None:
        with self.assertRaises(ValueError):
            _evaluate_access_scope(
                {"source_records": "abc"},
                {"max_privacy_class": "highly_sensitive"},
                MCPConfig(maximum_privacy_class="highly_sensitive"),
            )


class TestRecordsToIterHelper(unittest.TestCase):
    """The ``_records_to_iter`` helper flattens a bundle dict."""

    def test_empty_bundle(self) -> None:
        self.assertEqual(_records_to_iter({}), [])

    def test_multiple_planes(self) -> None:
        bundle = {
            "source_records": [{"id": "a"}],
            "episode_records": [{"id": "b"}],
        }
        records = _records_to_iter(bundle)
        self.assertEqual(len(records), 2)

    def test_non_list_plane_raises(self) -> None:
        with self.assertRaises(ValueError):
            _records_to_iter({"source_records": "not a list"})

    def test_non_object_record_raises(self) -> None:
        with self.assertRaises(ValueError):
            _records_to_iter({"source_records": ["not an object"]})


class TestServerToolInvocation(unittest.TestCase):
    """The server's tools can be invoked programmatically."""

    def setUp(self) -> None:
        self.server = ContractsMCPServer(MCPConfig())

    def _invoke(self, name: str, **kwargs: object) -> object:
        """Invoke a tool by name and return the result."""
        mcp = self.server._mcp

        async def _call() -> object:
            # FastMCP exposes ``call_tool`` for programmatic invocation.
            result = await mcp.call_tool(name, kwargs)
            # result is (content, structured) or similar; the
            # structured value is what we want.
            if hasattr(result, "structured_content"):
                return result.structured_content
            if hasattr(result, "data"):
                return result.data
            return result

        return asyncio.run(_call())

    def test_validate_bundle_tool(self) -> None:
        result = self._invoke("validate_bundle", bundle={})
        # Empty bundle validates cleanly
        self.assertIsInstance(result, dict)

    def test_validate_records_against_schemas_tool(self) -> None:
        result = self._invoke("validate_records_against_schemas", bundle={})
        self.assertIsInstance(result, dict)

    def test_validate_bundle_integrity_tool(self) -> None:
        result = self._invoke("validate_bundle_integrity", bundle={})
        self.assertIsInstance(result, dict)
        self.assertIn("schema", result)
        self.assertIn("integrity", result)

    def test_validate_bundle_with_bad_record(self) -> None:
        result = self._invoke(
            "validate_bundle",
            bundle={"source_records": [{"id": "src_1"}]},
        )
        # Bad record surfaces an error
        self.assertIsInstance(result, dict)
        # At least the source_records plane should have errors
        self.assertGreater(len(result.get("source_records", [])), 0)

    def test_check_access_tool(self) -> None:
        result = self._invoke(
            "check_access",
            bundle={"source_records": [{"id": "src_1", "privacy_class": "public"}]},
            scope={"name": "public"},
        )
        self.assertIsInstance(result, dict)
        self.assertIn("allowed_records", result)
        self.assertIn("summary", result)

    def test_evaluate_access_scope_tool(self) -> None:
        result = self._invoke(
            "evaluate_access_scope",
            bundle={"source_records": [{"id": "src_1", "privacy_class": "public"}]},
            scope={"name": "public"},
        )
        self.assertIsInstance(result, dict)
        self.assertIn("effective_scope", result)


class TestServerResources(unittest.TestCase):
    """The server exposes the JSON Schemas as resources."""

    def setUp(self) -> None:
        self.server = ContractsMCPServer(MCPConfig())

    def test_schemas_resource(self) -> None:
        mcp = self.server._mcp
        import json

        # Read the list-schemas resource.
        result = asyncio.run(mcp.read_resource("agent-memory-contracts://schemas"))
        # FastMCP returns a list of ResourceContent objects; extract
        # the first one's content text.
        contents = getattr(result, "contents", None) or result
        if isinstance(contents, list):
            text = contents[0].content
        else:
            text = str(contents)
        names = json.loads(text)
        self.assertIsInstance(names, list)
        self.assertGreater(len(names), 0)


if __name__ == "__main__":
    unittest.main()
