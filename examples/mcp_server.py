"""MCP server example.

This example starts an MCP server in stdio mode. The
server is registered with three tools (``validate_bundle``,
``compile_context``, ``check_access``) and a resource
tree (24 JSON Schemas under
``agent-memory-contracts://schemas``).

To use this server, register it with an MCP client (e.g.,
Claude Desktop's ``claude_desktop_config.json``):

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
messages appear on stdout. A real client (Claude Desktop,
Cursor, Cline, etc.) consumes those messages.
"""

from __future__ import annotations

from agent_memory_contracts.integrations.mcp import (
    ContractsMCPServer,
    MCPConfig,
    run_server,
)


def main() -> None:
    """Start the MCP server with default configuration."""
    # You can also create the server directly:
    # server = ContractsMCPServer(MCPConfig(transport="stdio"))
    # server.run()
    #
    # `run_server` is the same thing, with env-var support.
    run_server()


if __name__ == "__main__":
    main()
