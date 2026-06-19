"""MCP server example.

This example demonstrates the MCP server integration. By
default it instantiates the server (which registers six
tools and the JSON Schema resources) and prints a summary of
the public surface; the actual server is started with
``--serve``.

To run the server in stdio mode (consumed by an MCP client
like Claude Desktop, Cursor, or Cline), register it in the
client's config:

```json
{
  "mcpServers": {
    "agent-memory-contracts": {
      "command": "python",
      "args": ["-m", "agent_memory_contracts.integrations.mcp"]
    }
  }
}
```

Or run it directly to see the JSON-RPC traffic on stdout:

```
$ python -m agent_memory_contracts.integrations.mcp
```

The server uses stdio for transport, so the JSON-RPC
messages appear on stdout. A real client consumes those
messages.

Set ``MCP_MAX_PRIVACY_CLASS`` to cap what the MCP server
will expose even if a client requests a broader scope.
"""

from __future__ import annotations

import sys

from agent_memory_contracts.integrations.mcp import (
    ContractsMCPServer,
    MCPConfig,
    run_server,
)


def main() -> None:
    """Instantiate the server and print a summary, or start it."""
    if "--serve" in sys.argv:
        # `python examples/mcp_server.py --serve` actually starts
        # the server (blocks on stdio). Use only with an MCP
        # client; do not run in the CI smoke loop.
        run_server()
        return

    # Default: instantiate and summarize the public surface.
    server = ContractsMCPServer(MCPConfig())
    tools = [
        "validate_records_against_schemas",
        "validate_bundle_integrity",
        "evaluate_access_scope",
        "validate_bundle",
        "compile_context",
        "check_access",
    ]
    schemas = [
        "candidate_claim", "candidate_decision", "candidate_preference",
        "candidate_task", "candidate_taste_signal", "context_pack",
        "context_pack_build_receipt", "context_pack_validation_report",
        "core_state_delta_proposal", "core_state_snapshot",
        "decision_ledger_entry", "episode_record", "evidence_span",
        "fact_ledger_entry", "memory_reducer_decision",
        "preference_ledger_entry", "project_state_delta_proposal",
        "project_state_snapshot", "source_record", "state_reducer_decision",
        "taste_card", "taste_delta_proposal", "taste_reducer_decision",
    ]
    print("MCP server registered:")
    print(f"  server name: {server.config.server_name}")
    print(f"  transport:   {server.config.transport}")
    print(f"  max privacy: {server.config.maximum_privacy_class}")
    print(f"  tools ({len(tools)}): {', '.join(tools)}")
    print(f"  resources: agent-memory-contracts://schemas (list of {len(schemas)})")
    print(f"  resources: agent-memory-contracts://schemas/{{name}} (one per schema)")
    print()
    print("To start the server in stdio mode, run:")
    print("  python examples/mcp_server.py --serve")
    print("  # or: python -m agent_memory_contracts.integrations.mcp")


if __name__ == "__main__":
    main()
