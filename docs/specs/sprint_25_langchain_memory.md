# Sprint 25 / v1.0.1: LangChain Memory Backend Integration

**Status:** planned
**Target version:** `1.0.1` (minor; backwards-compatible, additive)
**Depends on:** v1.0.0 final (commit `408606b`)
**Spec author:** Mavis (best-judgment draft)
**Spec written:** 2026-06-07

## Why this sprint

Hermes competitive analysis (June 2026) identified three integration
gaps. Sprint 25 covers gap #3: **LangChain memory backend**.

LangChain's `BaseMemory` is the conventional interface for memory
in LangChain chains. The library has 4 planes (evidence, candidate,
ledger, taste), a `ContextPack` compiler, and a citation graph, but
no adapter that lets a LangChain chain use it as the memory store.

This is a 200-300 LOC adapter that:

1. Wraps the library's bundle + `compile_context_pack` as a
   `BaseMemory` subclass.
2. Implements the 3 required methods (`memory_variables`,
   `load_memory_variables`, `save_context`, `clear`).
3. Maps LangChain's input/output keys onto the library's
   record types and ledger entries.

It is the smallest integration that exposes the library's
integrity layer (citation graph, source coverage) to a LangChain
chain. It is the proof that "use it in your chain" is a 1-line
swap, not a 500-line rewrite.

## What this sprint is not

- **Not a wrapper around the LangChain `Memory` class hierarchy.**
  We do not implement `BaseChatMemory`, `ConversationBufferMemory`,
  `ConversationSummaryMemory`, or any of the conversation-shaped
  classes. Those are conversation abstractions; the library is
  a fact/decision/preference/project ledger.
- **Not a vector store.** The library is `BaseMemory`-shaped, not
  `VectorStore`-shaped. Vector store integration (gap #1 in Hermes
  analysis) is a separate v1.1.0+ consideration.
- **Not a tool / agent integration.** The library is not a tool;
  it is a memory store. Tool/agent integration is a separate
  concern.
- **Not a replacement for a real database.** The default backend
  is an in-memory list of bundles. Persistence is a v1.1.0+
  consideration.
- **Not a LLM call abstraction.** The library does not call LLMs;
  the integration does not introduce LLM calls.

## Architecture

```
┌─────────────────────┐
│ LangChain Chain     │
│ (LCEL or classic)   │
└──────────┬──────────┘
           │ load_memory_variables
           │ save_context
           │ clear
           ▼
┌─────────────────────┐
│ ContractsMemory     │  ◄── this sprint
│ (BaseMemory)        │
└──────────┬──────────┘
           │ read/write
           ▼
┌─────────────────────┐
│ Bundle + ContextPack│  ◄── v1.0.0 final
└─────────────────────┘
```

`ContractsMemory` is the only public name added. It is a
`BaseMemory` subclass that:

- Holds a `MemoryStore` (an in-memory list of bundles, indexed
  by `session_id`).
- On `load_memory_variables`, compiles a `ContextPack` for the
  active session using `compile_context_pack` from v1.0.0 final,
  scoped to the privacy class of the chain.
- On `save_context`, extracts the new turn's input/output as a
  `FactLedgerEntry` (default) or a `PreferenceLedgerEntry` (if
  the input looks like a preference), records a
  `MemoryReducerDecision`, and appends to the bundle.
- On `clear`, removes the session.

## Public API

| Name | Module | Description |
| --- | --- | --- |
| `ContractsMemory` | `integrations.langchain` | A `BaseMemory` subclass wrapping the library |
| `MemoryStore` | `integrations.langchain` | A bundle store, indexed by session id |
| `ContractsMemoryConfig` | `integrations.langchain` | Configuration: privacy class, scope, max_bundles |

`integrations.langchain` is a new module. It imports
`langchain.memory.BaseMemory` (from `langchain-classic`) and
re-exports the 3 names.

## Dependencies

- `langchain-classic>=0.1` (peer dependency, optional).
  The `langchain` extra in `pyproject.toml` adds it.
- No new runtime deps for the core library.

The integration lives in `src/agent_memory_contracts/integrations/`
and is not imported by the core library. To use it, the user
runs `pip install agent-memory-contracts[langchain]`.

## Test plan

- `tests/test_integrations_langchain.py` with ~15 tests:
  - 3 tests for the `MemoryStore` (put, get, evict).
  - 5 tests for `ContractsMemory` (load, save, clear, session
    isolation, scope enforcement).
  - 4 tests for the round-trip (write facts, read them back via
    `load_memory_variables`).
  - 3 tests for the optional-dep gate (no langchain → `ImportError`
    on `from agent_memory_contracts.integrations.langchain import ...`,
    not on `import agent_memory_contracts`).
- The optional-dep gate follows the pattern from
  `tests/test_jsonschema_validator.py`: tests skip with
  `pytest.importorskip("langchain.memory")` so the suite
  remains green when the extra is not installed.

## Example

`examples/langchain_memory.py` shows a 30-line `ConversationChain`
backed by `ContractsMemory`. The user can `from
agent_memory_contracts.integrations.langchain import
ContractsMemory` and use it as a drop-in `memory=` arg.

## Decisions applied to this sprint

### Small defaults

1. **Default privacy class: `internal`.** Most LangChain chains
   are for internal tooling. `customer` and `private` are
   opt-in via `ContractsMemoryConfig(privacy_class="private")`.
2. **Default scope: `team_scope`.** Mirrors the v0.9.0 default.
   `public_scope` and `private_scope` are opt-in.
3. **`save_context` extracts facts, not preferences, by default.**
   LangChain input/output is conversational, not preferency.
   `extract_as_preference=True` is the opt-in for chains that
   want to record preferences.
4. **`MemoryStore` is in-memory only.** No file backend, no DB.
   v1.1.0+ will add a `PersistentMemoryStore` if a user asks.
5. **`MemoryStore` evicts the oldest bundle when
   `max_bundles` is exceeded.** Default: `max_bundles=100`.
   This is a soft limit, not a hard cap.
6. **No `ConversationSummaryMemory` integration.** The library
   is a fact ledger, not a conversation log. The "summary"
   pattern does not map cleanly.
7. **No `ConversationBufferWindowMemory` integration.** Same
   reason. A buffer window is a "last N messages" abstraction;
   the library is "all trusted facts" with citation graphs.
8. **`load_memory_variables` returns a single `ContextPack`-shaped
   dict**, not a list of message strings. The chain's prompt
   template references `memory["context_pack"]` and formats it
   via `context_pack_to_dict()`.
9. **`ContractsMemory.memory_variables == ["context_pack"]`.**
   Exactly one variable. The dict it returns is keyed by
   `context_pack` and contains the `ContextPack` as a dict.

### Bigger defaults

10. **The integration is a `langchain-classic` peer, not a
    `langchain-core` peer.** `BaseMemory` lives in
    `langchain-classic` in modern LangChain (0.2+). The
    `langchain` extra installs `langchain-classic`. We do not
    depend on the legacy `langchain<0.1` package.

11. **`save_context` does not call the LLM.** It writes the
    raw input/output as a `FactLedgerEntry` candidate; the
    reducer decision is the audit record. LLM-based extraction
    (e.g., "this turn expresses a preference") is a v1.1.0+
    feature, gated on the user installing their model of choice.

12. **The integration does not ship a `Memory` class that
    implements `BaseChatMemory`.** `BaseChatMemory` is for
    chat-model chains and adds message-list semantics. The
    library is ledger-shaped, not chat-shaped. A
    `BaseChatMemory` adapter would either be a thin shim
    that re-implements the same logic, or it would distort
    the library's API to look like `messages`. Neither is
    worth the surface area in v1.0.1.

## Implementation outline

```python
# src/agent_memory_contracts/integrations/langchain.py

from __future__ import annotations

import sys
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Iterator, TYPE_CHECKING

if TYPE_CHECKING:
    from langchain.memory import BaseMemory

from agent_memory_contracts import (
    Bundle,
    ContextPack,
    PrivacyClass,
    compile_context_pack,
    check_access,
    team_scope,
    public_scope,
    customer_scope,
    private_scope,
    make_ledger_entry_id,
    make_reducer_decision_id,
    # ... etc
)

_LANGCHAIN_AVAILABLE = "langchain.memory" in sys.modules
try:
    from langchain.memory import BaseMemory  # type: ignore
    _LANGCHAIN_INSTALLED = True
except ImportError:
    _LANGCHAIN_INSTALLED = False


@dataclass(frozen=True)
class ContractsMemoryConfig:
    privacy_class: PrivacyClass = "internal"
    scope_factory: str = "team"  # public / team / customer / private
    max_bundles: int = 100
    extract_as_preference: bool = False


class MemoryStore:
    """In-memory bundle store, indexed by session id."""
    def __init__(self, max_bundles: int = 100) -> None:
        self._bundles: dict[str, deque[Bundle]] = {}
        self._max = max_bundles

    def put(self, session_id: str, bundle: Bundle) -> None: ...
    def get(self, session_id: str) -> Bundle | None: ...
    def evict_oldest(self, session_id: str) -> None: ...


if _LANGCHAIN_INSTALLED:
    class ContractsMemory(BaseMemory):
        """A BaseMemory subclass backed by an agent-memory-contracts bundle."""
        # memory_variables: list[str] = ["context_pack"]
        # load_memory_variables: returns {"context_pack": {...}}
        # save_context: extracts FactLedgerEntry, records MemoryReducerDecision
        # clear: removes session
        ...
```

The exact code is in the implementation step. The spec is the
shape, not the body.

## Out of scope for v1.0.1

- Vector store integration (Hermes gap #1).
- Tool / agent integration (Hermes gap #2; "agent that uses the
  library as a tool" is a different shape).
- LLM-based extraction in `save_context` (gated on user model
  choice; v1.1.0+).
- Persistent `MemoryStore` (file / DB backend; v1.1.0+).
- LangSmith tracing integration (LangChain's tracing is a
  separate concern; out of scope for the memory adapter).
- Async `BaseMemory` (`aload_memory_variables`, `asave_context`).
  The v1.0.0 library is sync; async is a v1.1.0+ question.

## Definition of done

- [ ] `src/agent_memory_contracts/integrations/langchain.py`
      implemented per the outline.
- [ ] `pyproject.toml` updated: `[langchain]` extra adds
      `langchain-classic>=0.1`; `[all]` extra includes it.
- [ ] `tests/test_integrations_langchain.py` with ~15 tests,
      gated on `pytest.importorskip("langchain.memory")`.
- [ ] `examples/langchain_memory.py` runs end-to-end with a
      mock LLM (or the OpenAI API key from env if available).
- [ ] `docs/STABILITY.md` updated with the 3 new public names.
- [ ] `CHANGELOG.md` updated with the v1.0.1 section.
- [ ] `docs/specs/DECISIONS.md` updated with the v1.0.1 entry.
- [ ] All 501 existing tests still pass; ~15 new tests pass.
- [ ] `mypy --strict` clean on the new module (or skipped if
      langchain is not installed).
- [ ] `scripts/audit_public_api.py` passes.
- [ ] Commit, push.

## Bottom line

This sprint ships a 1-line LangChain integration: replace
`ConversationBufferMemory()` with `ContractsMemory()` and the
chain's memory is now a fact ledger with citation graphs and
source coverage enforcement. It is the proof that the library
is composable with the most popular LLM framework, not a toy.
