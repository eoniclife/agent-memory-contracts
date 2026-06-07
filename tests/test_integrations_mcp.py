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
    _load_schema_names,
    _validate_bundle,
    _records_to_iter,
)


class TestMCPConfig(unittest.TestCase):
    """The config dataclass has the right defaults."""

    def test_defaults(self) -> None:
        cfg = MCPConfig()
        self.assertEqual(cfg.server_name, "agent-memory-contracts")
        self.assertEqual(cfg.transport, "stdio")
        self.assertEqual(cfg.port, 8765)

    def test_custom(self) -> None:
        cfg = MCPConfig(server_name="x", transport="http", port=9000)
        self.assertEqual(cfg.server_name, "x")
        self.assertEqual(cfg.transport, "http")
        self.assertEqual(cfg.port, 9000)


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
