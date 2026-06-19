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

Tools exposed:
- ``validate_records_against_schemas(bundle)`` — validate
  records against the library's JSON Schemas.
- ``validate_bundle_integrity(bundle)`` — run schema
  validation plus the library's cross-record integrity
  validators where applicable.
- ``evaluate_access_scope(bundle, scope)`` — evaluate access
  against the client-requested scope capped by the server's
  configured maximum privacy class.
- ``validate_bundle(bundle)`` — compatibility alias for
  schema validation.
- ``compile_context(bundle, task, policy)`` — compile a
  ContextPack for a task.
- ``check_access(bundle, scope)`` — shape-compatible alias for
  access-scope evaluation with server-side caps enforced.

Resources exposed:
- ``agent-memory-contracts://schemas`` — list of available
  JSON Schemas.
- ``agent-memory-contracts://schemas/{name}`` — read a
  JSON Schema by name.

The server is stateless. Each tool call is independent; no
shared state between calls.
"""

from __future__ import annotations

import json
import sys
import warnings
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
    AccessDecision,
    PRIVACY_CLASS_ORDER,
    BundleScope,
    CompilationPolicy,
    ContextPack,
    ContextPackTask,
    check_access,
    compile_context_pack,
    validate_candidate_bundle,
    validate_contextpack_bundle,
    validate_ledger_bundle,
    validate_state_bundle,
    validate_taste_bundle,
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
        maximum_privacy_class: server-side cap for MCP access
            tools. A client can request a stricter scope, but
            not a looser one. Defaults to ``"internal"``.
        allowed_tools: optional allow-list of MCP tool names.
            ``None`` registers all tools.
        fail_closed_unknown_privacy: if ``True``, unknown
            client-requested or record privacy classes fail
            closed instead of being silently coerced.
        http_security_notice_acknowledged: set to ``True`` when
            deliberately serving HTTP after adding deployment
            auth/hardening outside this stateless example.
    """

    server_name: str = "agent-memory-contracts"
    transport: TransportStr = "stdio"
    host: str = "127.0.0.1"
    port: int = 8765
    maximum_privacy_class: str = "internal"
    allowed_tools: frozenset[str] | None = None
    fail_closed_unknown_privacy: bool = True
    http_security_notice_acknowledged: bool = False

    def __post_init__(self) -> None:
        _validate_privacy_class(self.maximum_privacy_class)
        if self.allowed_tools is not None:
            object.__setattr__(self, "allowed_tools", frozenset(self.allowed_tools))


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


def _validate_privacy_class(privacy_class: str) -> None:
    if privacy_class not in PRIVACY_CLASS_ORDER:
        raise ValueError(
            f"unknown privacy_class: {privacy_class!r}; "
            f"expected one of {PRIVACY_CLASS_ORDER}"
        )


def _effective_privacy_class(requested: str, maximum: str) -> str:
    _validate_privacy_class(requested)
    _validate_privacy_class(maximum)
    requested_index = PRIVACY_CLASS_ORDER.index(requested)
    maximum_index = PRIVACY_CLASS_ORDER.index(maximum)
    return PRIVACY_CLASS_ORDER[min(requested_index, maximum_index)]


def _tool_enabled(config: MCPConfig, tool_name: str) -> bool:
    return config.allowed_tools is None or tool_name in config.allowed_tools


def _plane_records(bundle: dict[str, Any], plane: str) -> list[dict[str, Any]]:
    records = bundle.get(plane, [])
    if not isinstance(records, list):
        return []
    return [dict(record) for record in records if isinstance(record, dict)]


def _candidate_records(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for plane in (
        "candidate_claims",
        "candidate_decisions",
        "candidate_preferences",
        "candidate_tasks",
        "candidate_taste_signals",
    ):
        records.extend(_plane_records(bundle, plane))
    return records


def _ledger_entries(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for plane in (
        "fact_ledger_entries",
        "preference_ledger_entries",
        "decision_ledger_entries",
    ):
        records.extend(_plane_records(bundle, plane))
    return records


def _run_integrity_validator(name: str, call: Any) -> dict[str, Any]:
    try:
        call()
    except Exception as exc:
        return {"name": name, "errors": [str(exc)]}
    return {"name": name, "errors": []}


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


def _validate_bundle_integrity(bundle: dict[str, Any]) -> dict[str, Any]:
    """Run schema and cross-record integrity validators on a bundle."""
    source_records = _plane_records(bundle, "source_records")
    episode_records = _plane_records(bundle, "episode_records")
    evidence_spans = _plane_records(bundle, "evidence_spans")
    candidate_records = _candidate_records(bundle)
    ledger_entries = _ledger_entries(bundle)
    memory_reducer_decisions = _plane_records(bundle, "memory_reducer_decisions")
    taste_reducer_decisions = _plane_records(bundle, "taste_reducer_decisions")
    taste_cards = _plane_records(bundle, "taste_cards")
    state_reducer_decisions = _plane_records(bundle, "state_reducer_decisions")
    project_states = _plane_records(bundle, "project_state_snapshots")
    core_states = _plane_records(bundle, "core_state_snapshots")
    context_packs = _plane_records(bundle, "context_packs")
    build_receipts = _plane_records(bundle, "context_pack_build_receipts")
    validation_reports = _plane_records(bundle, "context_pack_validation_reports")

    schema = _validate_bundle(bundle)
    integrity = {
        "candidate": _run_integrity_validator(
            "candidate",
            lambda: validate_candidate_bundle(
                source_records,
                episode_records,
                evidence_spans,
                candidate_records,
            ),
        ),
        "ledger": _run_integrity_validator(
            "ledger",
            lambda: validate_ledger_bundle(
                source_records,
                episode_records,
                evidence_spans,
                candidate_records,
                memory_reducer_decisions,
                ledger_entries,
            ),
        ),
        "taste": _run_integrity_validator(
            "taste",
            lambda: validate_taste_bundle(
                source_records,
                episode_records,
                evidence_spans,
                candidate_records,
                taste_reducer_decisions,
                taste_cards,
            ),
        ),
        "state": _run_integrity_validator(
            "state",
            lambda: validate_state_bundle(
                source_records,
                episode_records,
                evidence_spans,
                candidate_records,
                ledger_entries,
                taste_cards,
                state_reducer_decisions,
                project_states,
                core_states,
            ),
        ),
        "context_pack": _run_integrity_validator(
            "context_pack",
            lambda: validate_contextpack_bundle(
                source_records,
                episode_records,
                evidence_spans,
                candidate_records,
                ledger_entries,
                taste_cards,
                project_states,
                core_states,
                context_packs,
                build_receipts,
                validation_reports,
            ),
        ),
    }
    valid = (
        all(not errors for errors in schema.values())
        and all(not section["errors"] for section in integrity.values())
    )
    return {
        "valid": valid,
        "schema": schema,
        "integrity": integrity,
    }


def _records_to_iter(bundle: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a bundle dict into a single list of records."""
    records: list[dict[str, Any]] = []
    for plane in _PLANE_TO_SCHEMA:
        plane_records = bundle.get(plane, [])
        if not isinstance(plane_records, list):
            raise ValueError(
                f"plane {plane!r} is not a list "
                f"(got {type(plane_records).__name__})"
            )
        for record in plane_records:
            if not isinstance(record, dict):
                raise ValueError(
                    f"plane {plane!r} contains non-object record "
                    f"(got {type(record).__name__})"
                )
            records.append(record)
    return records


def _context_pack_to_dict(cp: ContextPack) -> dict[str, Any]:
    """Serialize a ContextPack to a JSON-friendly dict."""
    import dataclasses
    return dataclasses.asdict(cp)


def _scope_from_dict(
    scope_dict: dict[str, Any],
    config: MCPConfig,
) -> BundleScope:
    """Build a BundleScope from a dict (MCP-friendly input)."""
    name = scope_dict.get("name", "team")
    privacy = scope_dict.get("max_privacy_class", "internal")
    if privacy not in PRIVACY_CLASS_ORDER:
        if config.fail_closed_unknown_privacy:
            raise ValueError(
                f"unknown requested max_privacy_class: {privacy!r}; "
                f"expected one of {PRIVACY_CLASS_ORDER}"
            )
        privacy = "internal"
    effective_privacy = _effective_privacy_class(
        str(privacy),
        config.maximum_privacy_class,
    )
    allowed_record_types_raw = scope_dict.get("allowed_record_types")
    allowed_record_types = None
    if allowed_record_types_raw is not None:
        if not isinstance(allowed_record_types_raw, list):
            raise ValueError("allowed_record_types must be a list when provided")
        else:
            allowed_record_types = frozenset(
                str(item) for item in allowed_record_types_raw
            )
    return BundleScope(
        max_privacy_class=effective_privacy,
        allowed_record_types=allowed_record_types,
        name=name,
    )


def _record_id_for_mcp(record: Any) -> str:
    if isinstance(record, dict):
        return str(record.get("id", "<missing>"))
    return str(getattr(record, "id", "<missing>"))


def _decision_to_dict(decision: AccessDecision) -> dict[str, str]:
    return {
        "record_id": decision.record_id,
        "action": decision.action,
        "reason": decision.reason,
    }


def _evaluate_access_scope(
    bundle: dict[str, Any],
    scope: dict[str, Any],
    config: MCPConfig,
) -> dict[str, Any]:
    scope_obj = _scope_from_dict(scope, config)
    records = _records_to_iter(bundle)
    if not config.fail_closed_unknown_privacy:
        allowed, decisions = scope_bundle(records, scope_obj)
    else:
        allowed = []
        decisions = []
        for record in records:
            try:
                decision = check_access(record, scope_obj)
            except ValueError as exc:
                decision = AccessDecision(
                    record_id=_record_id_for_mcp(record),
                    action="drop",
                    reason=f"fail_closed_unknown_privacy: {exc}",
                )
            decisions.append(decision)
            if decision.action == "allow":
                allowed.append(record)
    summary = summarize_access(decisions)
    return {
        "allowed_records": allowed,
        "decisions": [_decision_to_dict(decision) for decision in decisions],
        "effective_scope": {
            "name": scope_obj.name,
            "max_privacy_class": scope_obj.max_privacy_class,
            "allowed_record_types": (
                sorted(scope_obj.allowed_record_types)
                if scope_obj.allowed_record_types is not None
                else None
            ),
            "server_maximum_privacy_class": config.maximum_privacy_class,
        },
        "summary": {
            "total": summary.total,
            "allowed": summary.allowed,
            "redacted": summary.redacted,
            "dropped": summary.dropped,
        },
    }


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
            if _tool_enabled(self.config, "validate_records_against_schemas"):
                @self._mcp.tool(    # type: ignore[untyped-decorator]
                    name="validate_records_against_schemas",
                    description=(
                        "Validate records against the library's JSON Schemas. "
                        "This is per-record schema validation, not full "
                        "cross-record bundle integrity validation."
                    ),
                )
                def validate_records_against_schemas_tool(
                    bundle: dict[str, Any],
                ) -> dict[str, list[str]]:
                    return _validate_bundle(bundle)

            if _tool_enabled(self.config, "validate_bundle_integrity"):
                @self._mcp.tool(    # type: ignore[untyped-decorator]
                    name="validate_bundle_integrity",
                    description=(
                        "Run schema validation plus cross-record integrity "
                        "validators for candidates, ledgers, taste, state, "
                        "and ContextPacks where records are present."
                    ),
                )
                def validate_bundle_integrity_tool(
                    bundle: dict[str, Any],
                ) -> dict[str, Any]:
                    return _validate_bundle_integrity(bundle)

            if _tool_enabled(self.config, "validate_bundle"):
                @self._mcp.tool(    # type: ignore[untyped-decorator]
                    name="validate_bundle",
                    description=(
                        "Compatibility alias for validate_records_against_schemas. "
                        "Returns per-plane JSON Schema errors only."
                    ),
                )
                def validate_bundle_tool(bundle: dict[str, Any]) -> dict[str, list[str]]:
                    return _validate_bundle(bundle)

            if _tool_enabled(self.config, "compile_context"):
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

            if _tool_enabled(self.config, "evaluate_access_scope"):
                @self._mcp.tool(    # type: ignore[untyped-decorator]
                    name="evaluate_access_scope",
                    description=(
                        "Evaluate access for each record using the requested "
                        "scope capped by the server's maximum_privacy_class. "
                        "Returns allowed records, decisions, effective scope, "
                        "and a summary."
                    ),
                )
                def evaluate_access_scope_tool(
                    bundle: dict[str, Any],
                    scope: dict[str, Any],
                ) -> dict[str, Any]:
                    return _evaluate_access_scope(bundle, scope, self.config)

            if _tool_enabled(self.config, "check_access"):
                @self._mcp.tool(    # type: ignore[untyped-decorator]
                    name="check_access",
                    description=(
                        "Shape-compatible alias for evaluate_access_scope. "
                        "Server maximum_privacy_class is enforced."
                    ),
                )
                def check_access_tool(
                    bundle: dict[str, Any],
                    scope: dict[str, Any],
                ) -> dict[str, Any]:
                    return _evaluate_access_scope(bundle, scope, self.config)

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
                if not self.config.http_security_notice_acknowledged:
                    warnings.warn(
                        "HTTP transport is a stateless demo surface. Add "
                        "authentication, deployment hardening, and server-side "
                        "scope policy before exposing it beyond localhost.",
                        stacklevel=2,
                    )
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

    Reads ``MCP_TRANSPORT``, ``MCP_HOST``, ``MCP_PORT``,
    ``MCP_MAX_PRIVACY_CLASS``,
    ``MCP_FAIL_CLOSED_UNKNOWN_PRIVACY``, and
    ``MCP_HTTP_SECURITY_NOTICE_ACKNOWLEDGED`` from environment
    variables if set; otherwise uses :class:`MCPConfig`
    defaults.
    """
    import os

    transport_env = os.environ.get("MCP_TRANSPORT", "stdio")
    transport: TransportStr = (
        "http" if transport_env == "http" else "stdio"
    )
    fail_closed_env = os.environ.get("MCP_FAIL_CLOSED_UNKNOWN_PRIVACY", "1")
    http_ack_env = os.environ.get("MCP_HTTP_SECURITY_NOTICE_ACKNOWLEDGED", "0")
    config = MCPConfig(
        transport=transport,
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8765")),
        maximum_privacy_class=os.environ.get("MCP_MAX_PRIVACY_CLASS", "internal"),
        fail_closed_unknown_privacy=fail_closed_env.lower()
        not in {"0", "false", "no"},
        http_security_notice_acknowledged=http_ack_env.lower()
        in {"1", "true", "yes"},
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
