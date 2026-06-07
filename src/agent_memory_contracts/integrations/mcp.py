"""MCP server integration: expose library functions as MCP tools.

This module is **optional**. It is not imported by the core library.
To use it, install the optional ``[mcp]`` extra:

    pip install agent-memory-contracts[mcp]

The integration exposes three public names:

- :class:`ContractsMCPServer` — the MCP server class. Wraps
  FastMCP and registers the library's headline functions
  as tools and JSON Schemas as resources.
- :func:`run_server` — entry point: ``python -m
  agent_memory_contracts.integrations.mcp``.
- :class:`MCPConfig` — configuration: server name, transport,
  port (HTTP mode).

Tools exposed (3):
- ``validate_bundle(bundle)`` — validate a bundle against
  the library's JSON Schemas.
- ``compile_context(bundle, task, policy)`` — compile a
  ContextPack for a task.
- ``check_access(bundle, scope)`` — check access for each
  record in the bundle.

Resources exposed:
- ``agent-memory-contracts://schemas`` — list of available
  JSON Schemas.
- ``agent-memory-contracts://schemas/{name}`` — read a
  JSON Schema by name (24 resources total).

The server is stateless. Each tool call is independent; no
shared state between calls.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

# The integration is gated on fastmcp. If it is not
# installed, importing ContractsMCPServer raises ImportError
# on instantiation. The rest of the module (MCPConfig) is
# importable without fastmcp.
_FASTMCP_AVAILABLE = False
_FASTMCP_CLASS: Any = None
try:
    from fastmcp import FastMCP as _FastMCPClass

    _FASTMCP_AVAILABLE = True
    _FASTMCP_CLASS = _FastMCPClass
except Exception:  # ImportError or other import-time errors
    pass


# Re-export under the conventional name for the rest of the
# module to use.
FastMCP = _FASTMCP_CLASS

# Library imports — always available.
from agent_memory_contracts import (
    PRIVACY_CLASS_ORDER,
    BundleScope,
    CompilationPolicy,
    ContextPack,
    ContextPackTask,
    compile_context_pack,
    scope_bundle,
    summarize_access,
)


TransportStr = Literal["stdio", "http"]


@dataclass(frozen=True)
class MCPConfig:
    """Configuration for :class:`ContractsMCPServer`.

    Attributes:
        server_name: The MCP server name (visible in client
            UIs). Defaults to ``"agent-memory-contracts"``.
        transport: ``"stdio"`` (default) or ``"http"``.
        host: HTTP host (HTTP mode only). Defaults to
            ``"127.0.0.1"``.
        port: HTTP port (HTTP mode only). Defaults to
            ``8765``.
    """

    server_name: str = "agent-memory-contracts"
    transport: TransportStr = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765


# ---------------------------------------------------------------------------
# Helper: load JSON Schemas from the library
# ---------------------------------------------------------------------------

_SCHEMAS_DIR = Path(__file__).resolve().parent.parent / "schemas"


def _load_schema_names() -> list[str]:
    """Return the names of all JSON Schemas in the library.

    The schema name is the file stem with the ``.schema``
    suffix stripped, e.g. ``candidate_claim`` for
    ``candidate_claim.schema.json``. This matches what
    :func:`validate_instance` expects.
    """
    if not _SCHEMAS_DIR.exists():
        return []
    names: list[str] = []
    for p in _SCHEMAS_DIR.glob("*.schema.json"):
        # Strip the ".schema" suffix to match validate_instance
        stem = p.stem
        if stem.endswith(".schema"):
            stem = stem[: -len(".schema")]
        names.append(stem)
    return sorted(names)


def _load_schema(name: str) -> dict[str, Any] | None:
    """Load a JSON Schema by name. Returns None if not found."""
    path = _SCHEMAS_DIR / f"{name}.schema.json"
    if not path.exists():
        return None
    result: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return result


# ---------------------------------------------------------------------------
# Helper: bundle normalization
# ---------------------------------------------------------------------------


# Map: bundle key -> validate_instance schema name.
# (Many planes share a schema; we use the first one.)
_PLANE_TO_SCHEMA: dict[str, str] = {
    "source_records": "source_record",
    "episode_records": "episode_record",
    "evidence_spans": "evidence_span",
    "candidate_claims": "candidate_claim",
    "candidate_decisions": "candidate_decision",
    "candidate_preferences": "candidate_preference",
    "candidate_tasks": "candidate_task",
    "candidate_taste_signals": "candidate_taste_signal",
    "fact_ledger_entries": "fact_ledger_entry",
    "preference_ledger_entries": "preference_ledger_entry",
    "decision_ledger_entries": "decision_ledger_entry",
    "memory_reducer_decisions": "memory_reducer_decision",
    "taste_cards": "taste_card",
    "taste_reducer_decisions": "taste_reducer_decision",
    "project_state_snapshots": "project_state_snapshot",
    "core_state_snapshots": "core_state_snapshot",
    "state_reducer_decisions": "state_reducer_decision",
    "context_packs": "context_pack",
    "context_pack_build_receipts": "context_pack_build_receipt",
    "context_pack_validation_reports": "context_pack_validation_report",
}


def _validate_bundle(bundle: dict[str, Any]) -> dict[str, list[str]]:
    """Validate every record in the bundle against its schema.

    Returns a dict mapping plane name to a list of error
    strings (empty list = no errors for that plane).
    """
    # Import here to avoid pulling jsonschema_validator
    # into the namespace when the MCP extras are not
    # installed.
    from agent_memory_contracts.jsonschema_validator import (
        validate_instance,
    )

    errors: dict[str, list[str]] = {}
    for plane, schema_name in _PLANE_TO_SCHEMA.items():
        records = bundle.get(plane, [])
        if not isinstance(records, list):
            errors[plane] = [
                f"plane {plane!r} is not a list (got {type(records).__name__})"
            ]
            continue
        plane_errors: list[str] = []
        for i, record in enumerate(records):
            record_errors = validate_instance(
                record, schema_name, raise_on_error=False
            )
            for err in record_errors:
                plane_errors.append(f"[{i}] {err}")
        errors[plane] = plane_errors
    return errors


def _records_to_iter(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a bundle dict into a single list of records."""
    records: list[dict[str, Any]] = []
    for plane in _PLANE_TO_SCHEMA:
        records.extend(bundle.get(plane, []))
    return records


def _context_pack_to_dict(cp: ContextPack) -> dict[str, Any]:
    """Serialize a ContextPack to a JSON-friendly dict."""
    import dataclasses
    return dataclasses.asdict(cp)


def _scope_from_dict(scope_dict: dict[str, Any]) -> BundleScope:
    """Build a BundleScope from a dict (MCP-friendly input)."""
    name = scope_dict.get("name", "team")
    privacy = scope_dict.get("max_privacy_class", "internal")
    if privacy not in PRIVACY_CLASS_ORDER:
        privacy = "internal"
    return BundleScope(
        max_privacy_class=privacy,
        allowed_record_types=None,
        name=name,
    )


# ---------------------------------------------------------------------------
# Public: ContractsMCPServer
# ---------------------------------------------------------------------------


if _FASTMCP_AVAILABLE:

    class ContractsMCPServer:
        """An MCP server exposing agent-memory-contracts primitives.

        The server is a thin wrapper around FastMCP. It
        registers three tools (``validate_bundle``,
        ``compile_context``, ``check_access``) and a
        resource tree (``agent-memory-contracts://schemas``
        and 24 ``agent-memory-contracts://schemas/{name}``
        resources).

        Example:
            ```python
            from agent_memory_contracts.integrations.mcp import (
                ContractsMCPServer,
                MCPConfig,
            )

            server = ContractsMCPServer(MCPConfig())
            server.run()
            ```
        """

        def __init__(self, config: MCPConfig | None = None) -> None:
            """Create the server and register tools/resources."""
            if config is None:
                config = MCPConfig()
            self.config = config
            self._mcp = FastMCP(config.server_name)
            self._register_tools()
            self._register_resources()

        def _register_tools(self) -> None:
            @self._mcp.tool(    # type: ignore[untyped-decorator]
                name="validate_bundle",
                description=(
                    "Validate a bundle's records against the library's "
                    "JSON Schemas. Returns a dict mapping plane name "
                    "to a list of error strings (empty list = valid)."
                ),
            )
            def validate_bundle_tool(bundle: dict[str, Any]) -> dict[str, list[str]]:
                return _validate_bundle(bundle)

            @self._mcp.tool(    # type: ignore[untyped-decorator]
                name="compile_context",
                description=(
                    "Compile a ContextPack for the given task and "
                    "policy. Returns a dict form of the ContextPack."
                ),
            )
            def compile_context_tool(
                bundle: dict[str, Any],
                task: dict[str, Any],
                policy: dict[str, Any] | None = None,
            ) -> dict[str, Any]:
                # Construct dataclass instances from dicts.
                # CompilationPolicy has no from_dict; build it
                # from a flat dict by passing **kwargs.
                if policy is None:
                    policy_obj = CompilationPolicy()
                else:
                    # The MCP-facing schema is the same as the
                    # dataclass's fields, so we can splat.
                    policy_obj = CompilationPolicy(**policy)
                task_obj = ContextPackTask(**task)
                records = _records_to_iter(bundle)
                result = compile_context_pack(
                    bundle=records, task=task_obj, policy=policy_obj
                )
                return {
                    "context_pack": _context_pack_to_dict(result.context_pack),
                    "selected_record_ids": list(result.selected_record_ids),
                    "excluded_record_ids": list(result.excluded_record_ids),
                }

            @self._mcp.tool(    # type: ignore[untyped-decorator]
                name="check_access",
                description=(
                    "Check access for each record in the bundle, "
                    "given a scope (e.g. team_scope, public_scope). "
                    "Returns the scoped bundle and a summary."
                ),
            )
            def check_access_tool(
                bundle: dict[str, Any],
                scope: dict[str, Any],
            ) -> dict[str, Any]:
                scope_obj = _scope_from_dict(scope)
                records = _records_to_iter(bundle)
                allowed, decisions = scope_bundle(records, scope_obj)
                summary = summarize_access(decisions)
                return {
                    "allowed_records": allowed,
                    "summary": {
                        "total": summary.total,
                        "allowed": summary.allowed,
                        "dropped": summary.dropped,
                    },
                }

        def _register_resources(self) -> None:
            @self._mcp.resource(    # type: ignore[untyped-decorator]
                "agent-memory-contracts://schemas",
                name="schemas",
                description="List of available JSON Schemas in the library.",
                mime_type="application/json",
            )
            def list_schemas() -> str:
                return json.dumps(_load_schema_names())

            @self._mcp.resource(    # type: ignore[untyped-decorator]
                "agent-memory-contracts://schemas/{name}",
                name="schema",
                description="A JSON Schema from the library.",
                mime_type="application/json",
            )
            def read_schema(name: str) -> str:
                schema = _load_schema(name)
                if schema is None:
                    raise ValueError(f"schema not found: {name}")
                return json.dumps(schema)

        def run(self) -> None:
            """Run the server (blocks)."""
            if self.config.transport == "http":
                self._mcp.run(transport="http", host=self.config.host, port=self.config.port)
            else:
                self._mcp.run(transport="stdio")

else:

    class ContractsMCPServer:  # type: ignore[no-redef]
        """Stub: fastmcp is not installed.

        Install it with::

            pip install agent-memory-contracts[mcp]
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "ContractsMCPServer requires fastmcp. "
                "Install it with: pip install agent-memory-contracts[mcp]"
            )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_server() -> None:
    """Entry point for ``python -m agent_memory_contracts.integrations.mcp``.

    Reads ``MCP_TRANSPORT`` and ``MCP_PORT`` from environment
    variables if set; otherwise uses
    :class:`MCPConfig` defaults (stdio, port 8765).
    """
    import os

    transport_env = os.environ.get("MCP_TRANSPORT", "stdio")
    transport: TransportStr = (
        "http" if transport_env == "http" else "stdio"
    )
    config = MCPConfig(
        transport=transport,
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8765")),
    )
    server = ContractsMCPServer(config)
    server.run()


if __name__ == "__main__":
    # Allow `python -m agent_memory_contracts.integrations.mcp` invocation
    run_server()


__all__ = [
    "ContractsMCPServer",
    "MCPConfig",
    "run_server",
]
