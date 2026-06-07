# Sprint 26 / v1.0.2: MCP Server

**Status:** planned
**Target version:** `1.0.2` (minor; backwards-compatible, additive)
**Depends on:** v1.0.1 (commit `f35b090`)
**Spec author:** Mavis (best-judgment draft)
**Spec written:** 2026-06-07

## Why this sprint

Hermes competitive analysis (June 2026) identified three
integration gaps. Sprint 26 covers gap #4: **MCP server**.

The Model Context Protocol (MCP) is Anthropic's open standard
for connecting LLMs to tools and data. An MCP server exposes
"tools" (callable functions) and "resources" (addressable
data) over a JSON-RPC transport. Any MCP-compatible client
(Claude Desktop, Cursor, Cline, etc.) can discover and
invoke the server's tools.

This sprint ships an MCP server that exposes 3 of the
library's headline functions as MCP tools and a handful of
the library's JSON Schemas as MCP resources. The server
makes the library's integrity layer (validate, compile,
access control) callable from any MCP client.

## What this sprint is not

- **Not a tool/agent framework.** The server exposes
  library functions as MCP tools; it does not implement
  an agent loop, a reasoning chain, or an LLM
  orchestration layer.
- **Not a vector store.** MCP servers can be vector stores;
  the library is not a vector store. A v1.1.0+
  consideration is a vector store adapter.
- **Not a database backend.** The server is stateless; it
  accepts bundles in tool calls and returns results. The
  user supplies the bundle.
- **Not an OAuth/auth layer.** The server is open-loop. The
  user runs the server in their environment; access
  control happens in the bundle (via `check_access`).

## Architecture

```
┌──────────────────────┐
│ MCP client           │
│ (Claude Desktop,     │
│  Cursor, Cline, ...) │
└──────────┬───────────┘
           │ JSON-RPC over stdio (or HTTP)
           ▼
┌──────────────────────┐
│ agent-memory-        │  ◄── this sprint
│ contracts-mcp        │
│ (FastMCP server)     │
└──────────┬───────────┘
           │ calls into
           ▼
┌──────────────────────┐
│ agent_memory_contracts│
│ library (v1.0.1)     │
└──────────────────────┘
```

The server is a thin FastMCP wrapper. It is **stateless**:
each tool call is independent; no shared state between
calls.

## Public API

| Name | Module | Description |
| --- | --- | --- |
| `ContractsMCPServer` | `integrations.mcp` | The MCP server class |
| `run_server` | `integrations.mcp` | Entry point: `python -m agent_memory_contracts.integrations.mcp` |
| `MCPConfig` | `integrations.mcp` | Configuration: transport, port (HTTP mode) |

`integrations.mcp` is a new module. It imports
`fastmcp` (from the optional `[mcp]` extra) and exposes
the 3 names.

## Tools exposed (3)

1. **`validate_bundle(bundle: dict) -> dict`**
   Wraps the library's
   `agent_memory_contracts.bundles.validate_bundle_dict` (or
   equivalent). Returns a validation report.

2. **`compile_context(bundle: dict, task: dict, policy: dict) -> dict`**
   Wraps `compile_context_pack`. Returns a dict form of the
   `ContextPack`.

3. **`check_access(bundle: dict, scope: dict) -> dict`**
   Wraps `check_access` + `scope_bundle`. Returns a
   summary of decisions and the redacted bundle.

## Resources exposed (1 list + 24 schemas = 25)

1. **`agent-memory-contracts://schemas`**
   A directory resource: a list of available JSON
   Schemas (the 24 schema files at "1.0.0").

2. **`agent-memory-contracts://schemas/{name}`**
   For each schema name, a resource containing the
   schema's JSON content. 24 resources total.

## Dependencies

- `fastmcp>=2.0` (peer dependency, optional).
  The `mcp` extra in `pyproject.toml` adds it.
- The library's core deps (stdlib only).

The integration lives in
`src/agent_memory_contracts/integrations/mcp.py` and is
not imported by the core library. To use it, the user
runs `pip install agent-memory-contracts[mcp]`.

## Test plan

- `tests/test_integrations_mcp.py` with ~10 tests:
  - 3 tests for tool invocation (validate, compile,
    check_access) using a programmatic MCP client.
  - 2 tests for resource resolution (schema list,
    schema content).
  - 3 tests for error handling (invalid bundle,
    missing required field, unsupported schema).
  - 2 tests for the optional-dep gate (no fastmcp →
    `ImportError` on `from
    agent_memory_contracts.integrations.mcp import ...`,
    not on `import agent_memory_contracts`).
- Tests use a fake MCP client (or the
  `mcp.client.session` testing utilities) to invoke
  the server's tools and read its resources.

## Example

`examples/mcp_server.py` shows how to start the server
in stdio mode. The user adds the server to their MCP
client config (e.g., `claude_desktop_config.json`):

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

## Decisions applied to this sprint

### Small defaults

1. **Server name: `agent-memory-contracts`.** The MCP
   `Server.name` field; users see this in their client
   UI.
2. **Default transport: `stdio`.** The default MCP
   transport. HTTP/SSE is opt-in via
   `MCPConfig(transport="http", port=8080)`.
3. **Tool descriptions are short.** ~1-2 sentences. Long
   descriptions are put in the docstring; the MCP
   descriptor uses a one-liner.
4. **Resource URIs use the `agent-memory-contracts://`
   scheme.** The `://` is the MCP convention for custom
   resource schemes.
5. **The server is stateless.** No caching, no
   per-session state. v1.1.0+ consideration is a
   stateful mode that holds bundles in memory.
6. **Tool input validation uses the library's own
   validators.** The server does not duplicate
   validation logic; it delegates to the library.
7. **Tool errors return JSON-RPC error responses.** The
   server does not silently swallow exceptions.
8. **The server does not log to stdout by default.**
   stdout is the JSON-RPC channel; logging goes to
   stderr.
9. **Schema resources are read-only.** The server
   exposes the JSON Schemas as resources but does not
   allow modification. Schemas are content-addressed
   in the library; modification is a v1.1.0+ concern.

### Bigger defaults

10. **The integration uses `fastmcp`, not the raw MCP
    SDK.** FastMCP is the de facto framework for
    building MCP servers in Python; it's by the same
    team (Prefect) and the official Anthropic SDK
    itself uses it under the hood for many examples.
    Using FastMCP gives us a 200-LOC server instead of
    a 2000-LOC one.

11. **The server exposes 3 tools, not the full library
    surface.** 3 tools = `validate_bundle`,
    `compile_context`, `check_access`. Other functions
    (fingerprint, diff, merge, hygiene) are
    client-side operations; they don't need an MCP
    round-trip. A user with full bundle access can run
    them locally.

12. **The server does not implement a "store" tool.**
    MCP has a `Store` resource type for read/write
    persistent state. The library is not a store; the
    server is stateless. A store-based adapter is a
    v1.1.0+ consideration (and would couple to
    LangGraph's `Store` API).

## Implementation outline

```python
# src/agent_memory_contracts/integrations/mcp.py

from __future__ import annotations

from typing import Any

try:
    from fastmcp import FastMCP
    _FASTMCP_AVAILABLE = True
except ImportError:
    _FASTMCP_AVAILABLE = False

from agent_memory_contracts import (
    bundle_fingerprint,
    check_access,
    compile_context_pack,
    # ...
)


if _FASTMCP_AVAILABLE:
    server = FastMCP("agent-memory-contracts")

    @server.tool()
    def validate_bundle(bundle: dict) -> dict:
        """Validate a bundle against the library's JSON Schemas."""
        ...

    @server.tool()
    def compile_context(bundle: dict, task: dict, policy: dict) -> dict:
        """Compile a ContextPack for the given task."""
        ...

    @server.tool()
    def check_access(bundle: dict, scope: dict) -> dict:
        """Check access for each record in the bundle."""
        ...

    @server.resource("agent-memory-contracts://schemas")
    def list_schemas() -> list[str]:
        """List available JSON Schemas."""
        ...

    @server.resource("agent-memory-contracts://schemas/{name}")
    def read_schema(name: str) -> str:
        """Read a JSON Schema by name."""
        ...


def run_server() -> None:
    """Entry point: `python -m agent_memory_contracts.integrations.mcp`."""
    if not _FASTMCP_AVAILABLE:
        raise ImportError("fastmcp is required...")
    server.run()
```

The exact code is in the implementation step. The spec is
the shape, not the body.

## Out of scope for v1.0.2

- HTTP transport (only stdio in v1.0.2; HTTP/SSE is a
  v1.x+ consideration).
- Authentication (the server is open-loop; auth is
  upstream).
- State (the server is stateless; per-session state is
  a v1.1.0+ consideration).
- Streaming (MCP supports streaming responses; the
  library's functions are sync; streaming is a v1.1.0+
  consideration).
- Logging customization (logs go to stderr with the
  default FastMCP settings; custom log routing is a
  v1.1.0+ consideration).

## Definition of done

- [ ] `src/agent_memory_contracts/integrations/mcp.py`
      implemented per the outline.
- [ ] `pyproject.toml` updated: `[mcp]` extra adds
      `fastmcp>=2.0`; `[all]` extra includes it.
- [ ] `tests/test_integrations_mcp.py` with ~10 tests,
      gated on `pytest.importorskip("fastmcp")`.
- [ ] `examples/mcp_server.py` runs end-to-end.
- [ ] `docs/STABILITY.md` updated with the 3 new public
      names.
- [ ] `CHANGELOG.md` updated with the v1.0.2 section.
- [ ] `docs/specs/DECISIONS.md` updated with the v1.0.2
      entry.
- [ ] All 520 existing tests still pass; ~10 new tests
      pass.
- [ ] `mypy --strict` clean on the new module (or
      skipped if fastmcp is not installed).
- [ ] `scripts/audit_public_api.py` passes.
- [ ] Commit, push.

## Bottom line

This sprint ships the proof that the library is MCP-native.
Any MCP client (Claude Desktop, Cursor, Cline, etc.) can
discover the library's integrity primitives as tools and
its JSON Schemas as resources. It is the smallest integration
that exposes the v1.0.0 stability commitment to the MCP
ecosystem.
