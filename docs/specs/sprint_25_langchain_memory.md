# Sprint 25 / v1.0.1: LangChain Memory Backend Integration

**Status:** implemented; v1.3.0 errata applied
**Target version:** historical `1.0.1` plan; current semantics corrected in `1.3.0`
**Depends on:** v1.0.0 final (commit `408606b`)
**Spec author:** Mavis (best-judgment draft)
**Spec written:** 2026-06-07

## v1.3.0 errata

The original planning draft overstated the LangChain adapter as writing
trusted ledger entries and reducer decisions. The shipped adapter is narrower:
`ContractsMemory.save_context()` records conversation turns as a session
`SourceRecord`, `EpisodeRecord`, and input/output `EvidenceSpan` records, then
`load_memory_variables()` returns a ContextPack-shaped session trace. It does
not promote turns into trusted facts, run a reducer, or make a LangChain
application's whole memory stack poisoning-resistant.

This spec is shipped in the sdist, so the text below describes the corrected
public behavior rather than preserving the stale draft claim.

## Why this sprint

Hermes competitive analysis (June 2026) identified three integration
gaps. Sprint 25 covers gap #3: **LangChain memory backend**.

LangChain's `BaseMemory` is the conventional interface for memory
in classic LangChain chains. The library has six planes, a
`ContextPack` shape, and citation/validation primitives, but no
adapter that lets a LangChain chain record turns in the contracts
format.

This is a 200-300 LOC adapter that:

1. Wraps the library's bundle-shaped records as a `BaseMemory`
   subclass.
2. Implements the 3 required methods (`memory_variables`,
   `load_memory_variables`, `save_context`, `clear`).
3. Maps LangChain's input/output keys onto session source,
   episode, and evidence-span records.

It is the smallest integration that exposes the library's record
shape to a LangChain chain. It is proof that "use it in your chain"
can be a 1-line trace-capture swap, not a 500-line rewrite. It is
not proof that a classic LangChain memory object is now a governed
trusted-fact channel.

## What this sprint is not

- **Not a wrapper around the LangChain `Memory` class hierarchy.**
  We do not implement `BaseChatMemory`, `ConversationBufferMemory`,
  `ConversationSummaryMemory`, or any of the conversation-shaped
  classes. Those are conversation abstractions; this adapter is
  a source/episode/evidence trace adapter.
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
- On `load_memory_variables`, returns a ContextPack-shaped session
  trace for the active session.
- On `save_context`, records the new turn's input/output as one
  `EpisodeRecord` plus input/output `EvidenceSpan` records under a
  session `SourceRecord`, and appends to the bundle.
- On `clear`, removes the session.

## Public API

| Name | Module | Description |
| --- | --- | --- |
| `ContractsMemory` | `integrations.langchain` | A `BaseMemory` subclass wrapping the library |
| `MemoryStore` | `integrations.langchain` | A bundle store, indexed by session id |
| `ContractsMemoryConfig` | `integrations.langchain` | Configuration: privacy class, max_bundles, max_records_per_load, and build metadata |

`integrations.langchain` is a module. It imports
`langchain_classic.base_memory.BaseMemory` and re-exports the 3 names.

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
  - 4 tests for the round-trip (write trace records, read them back via
    `load_memory_variables`).
  - 3 tests for the optional-dep gate (no langchain → `ImportError`
    on `from agent_memory_contracts.integrations.langchain import ...`,
    not on `import agent_memory_contracts`).
- The optional-dep gate follows the pattern from
  `tests/test_jsonschema_validator.py`: tests skip when
  `langchain_classic.base_memory` is unavailable, so the suite
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
2. **No access-scope enforcement in the adapter.** The privacy class
   is assigned to generated source/span trace records. Applications
   that need trusted access filtering should run the relevant
   contracts/runtime access path outside this adapter.
3. **`save_context` records trace, not facts or preferences.**
   LangChain input/output is conversational. Trusted facts or
   preferences require an extraction and reducer path outside this
   adapter.
4. **`MemoryStore` is in-memory only.** No file backend, no DB.
   v1.1.0+ will add a `PersistentMemoryStore` if a user asks.
5. **`MemoryStore` evicts the oldest bundle when
   `max_bundles` is exceeded.** Default: `max_bundles=100`.
   This is a soft limit, not a hard cap.
6. **No `ConversationSummaryMemory` integration.** This adapter
   records raw turn trace, not summaries. The "summary" pattern
   does not map cleanly.
7. **No `ConversationBufferWindowMemory` integration.** Same
   reason. A buffer window is a "last N messages" abstraction;
   this adapter returns a ContextPack-shaped trace.
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

11. **`save_context` does not call the LLM.** It writes raw
    input/output as source/episode/evidence trace. LLM-based
    extraction into candidate claims or preferences is product
    code outside this adapter and must still pass through a
    reducer before becoming trusted memory.

12. **The integration does not ship a `Memory` class that
    implements `BaseChatMemory`.** `BaseChatMemory` is for
    chat-model chains and adds message-list semantics. The
    adapter is trace-shaped, not chat-shaped. A
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
    from langchain_classic.base_memory import BaseMemory

from agent_memory_contracts import (
    Bundle,
    PrivacyClass,
    SourceRecord,
    EpisodeRecord,
    EvidenceSpan,
    make_episode_id,
    make_source_id,
    make_span_id,
    # ... etc
)

_LANGCHAIN_AVAILABLE = "langchain_classic.base_memory" in sys.modules
try:
    from langchain_classic.base_memory import BaseMemory  # type: ignore
    _LANGCHAIN_INSTALLED = True
except ImportError:
    _LANGCHAIN_INSTALLED = False


@dataclass(frozen=True)
class ContractsMemoryConfig:
    privacy_class: PrivacyClass = "internal"
    max_bundles: int = 100
    max_records_per_load: int = 20


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
        # save_context: records EpisodeRecord plus input/output EvidenceSpan
        # clear: removes session
        ...
```

The exact code is in the implementation step. The spec is the
shape, not the body.

## Out of scope for v1.0.1

- Vector store integration (Hermes gap #1).
- Tool / agent integration (Hermes gap #2; "agent that uses the
  library as a tool" is a different shape).
- LLM-based extraction from trace into candidates or preferences
  (gated on user model choice; product code outside this adapter).
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
      gated on `langchain_classic.base_memory` availability.
- [ ] `examples/langchain_memory.py` runs end-to-end with a
      mock LLM (or the OpenAI API key from env if available).
- [ ] `docs/STABILITY.md` updated with the 3 new public names.
- [ ] `CHANGELOG.md` updated with the v1.0.1 section.
- [ ] `docs/specs/DECISIONS.md` updated with the v1.0.1 entry.
- [ ] Historical target: all 501 existing tests still pass; ~15 new tests pass.
- [ ] `mypy --strict` clean on the new module (or skipped if
      langchain is not installed).
- [ ] `scripts/audit_public_api.py` passes.
- [ ] Commit, push.

## Bottom line

This sprint ships a 1-line LangChain integration: replace
`ConversationBufferMemory()` with `ContractsMemory()` and the
chain records turns as source/episode/evidence trace with a
ContextPack-shaped read surface. It is proof that the library
is composable with the most popular LLM framework, while keeping
trusted-fact promotion outside this adapter.
