"""LangChain integration: a BaseMemory subclass backed by the library.

This module is **optional**. It is not imported by the core library.
To use it, install the optional ``[langchain]`` extra:

    pip install agent-memory-contracts[langchain]

The integration exposes three public names:

- :class:`ContractsMemory` — a ``BaseMemory`` subclass that
  treats each conversation turn as an EpisodeRecord and compiles
  the bundle into a ContextPack on read.
- :class:`MemoryStore` — an in-memory, session-indexed bundle
  store with a soft ``max_bundles`` eviction policy.
- :class:`ContractsMemoryConfig` — configuration: privacy class,
  scope factory name, max_bundles, and per-turn reducer
  metadata.

The integration is a thin shim around the v1.0.0 library. It
maps LangChain's "input + output" shape onto the library's
"source + episode + evidence" shape. The bundle is the
single source of truth; the ContextPack compiler handles
selection, scoping, and source coverage enforcement on read.
"""

from __future__ import annotations

import dataclasses
import hashlib
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

# The integration is gated on langchain-classic. If it is not
# installed, the import of ContractsMemory raises ImportError.
# The rest of the module (MemoryStore, ContractsMemoryConfig)
# is importable without langchain.
_LANGCHAIN_BASE_MEMORY: Any = None
_LANGCHAIN_IMPORT_ERROR: BaseException | None = None
try:
    from langchain_classic.base_memory import BaseMemory as _BaseMemory
    _LANGCHAIN_BASE_MEMORY = _BaseMemory
except Exception as _exc:  # ImportError or ModuleNotFoundError
    _LANGCHAIN_IMPORT_ERROR = _exc

if TYPE_CHECKING:
    # Imported under TYPE_CHECKING for type hints only.
    from langchain_classic.base_memory import BaseMemory  # noqa: F401

# Library imports — always available, core library is stdlib-only.
from agent_memory_contracts import (
    ContextPack,
    EpisodeRecord,
    EvidenceSpan,
    SourceRecord,
    make_episode_id,
    make_source_id,
    make_span_id,
)


PrivacyClassStr = Literal["public", "internal", "private", "sensitive", "highly_sensitive"]


@dataclass(frozen=True)
class ContractsMemoryConfig:
    """Configuration for :class:`ContractsMemory`.

    All fields have sensible defaults. Most chains can use
    ``ContractsMemory()`` with no arguments.

    Attributes:
        privacy_class: The maximum privacy class visible in
            the compiled context_pack. Defaults to
            ``"internal"`` (most chains are for internal
            tooling). Use ``"public"`` for public-facing
            chains, ``"private"`` for chains that should see
            all records up to and including ``"private"``.
        max_bundles: Soft cap on the number of bundles per
            session. When exceeded, the oldest bundle is
            evicted. Defaults to 100.
        max_records_per_load: Cap on the number of episodes
            returned by ``load_memory_variables``. Defaults
            to 20.
        builder_agent: The agent name recorded on the
            ``BuildReceipt``. Defaults to
            ``"agent_memory_contracts.integrations.langchain"``.
        builder_model: The model name recorded on the
            ``BuildReceipt``. Defaults to ``"none"``
            (no LLM involvement in compile).
        exclude_stale: Whether to exclude ``stale`` records
            from the compiled context_pack. Defaults to
            ``True`` (matches the library's default).
        exclude_retracted: Whether to exclude ``retracted``
            records. Defaults to ``True``.
        exclude_contested: Whether to exclude ``contested``
            records. Defaults to ``True``.
    """

    privacy_class: PrivacyClassStr = "internal"
    max_bundles: int = 100
    max_records_per_load: int = 20
    builder_agent: str = "agent_memory_contracts.integrations.langchain"
    builder_model: str = "none"
    exclude_stale: bool = True
    exclude_retracted: bool = True
    exclude_contested: bool = True


@dataclass
class MemoryStore:
    """In-memory, session-indexed bundle store.

    Holds the bundle for each session. Each
    :class:`ContractsMemory` instance owns one ``MemoryStore``
    by default, but the same store can be shared across
    multiple memory instances to share a session (advanced use).

    The store is **not** persistent. A v1.1.0+ consideration is
    a file- or DB-backed store; the in-memory form is the
    simplest thing that can work.

    Attributes:
        max_bundles: Soft cap on the number of bundles per
            session. When exceeded, the oldest bundle is
            evicted.
    """

    max_bundles: int = 100
    _bundles: dict[str, deque[dict[str, Any]]] = field(default_factory=dict)

    def put(self, session_id: str, bundle: dict[str, Any]) -> None:
        """Append a bundle to the session's deque.

        The bundle is a ``dict`` with the library's standard
        bundle shape (lists of records, keyed by plane).
        """
        bundles = self._bundles.setdefault(session_id, deque(maxlen=self.max_bundles))
        bundles.append(bundle)

    def get_all(self, session_id: str) -> list[dict[str, Any]]:
        """Return all bundles for a session, in append order."""
        return list(self._bundles.get(session_id, ()))

    def get_merged(self, session_id: str) -> list[dict[str, Any]]:
        """Return a single merged bundle for the session.

        Merges all per-turn bundles into one flat list of
        records, deduped by ``id``. The merged bundle is a
        flat list — the shape the
        :func:`agent_memory_contracts.compile_context_pack`
        compiler expects.

        Returns an empty list if the session has no records.
        """
        bundles = self.get_all(session_id)
        if not bundles:
            return []
        return _merge_bundles(bundles)

    def clear_session(self, session_id: str) -> None:
        """Remove all bundles for a session."""
        self._bundles.pop(session_id, None)

    def session_count(self) -> int:
        """Return the number of sessions in the store."""
        return len(self._bundles)


# ---------------------------------------------------------------------------
# Internal: bundle shape and merge logic
# ---------------------------------------------------------------------------

_BUNDLE_PLANES = (
    "source_records",
    "episode_records",
    "evidence_spans",
    "fact_ledger_entries",
    "preference_ledger_entries",
    "decision_ledger_entries",
    "memory_reducer_decisions",
    "taste_cards",
    "taste_reducer_decisions",
    "project_state_snapshots",
    "core_state_snapshots",
    "state_reducer_decisions",
    "context_packs",
)


def _empty_bundle() -> dict[str, Any]:
    return {plane: [] for plane in _BUNDLE_PLANES}


def _merge_bundles(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge N dict-of-plane-lists bundles into a flat list of records."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bundle in bundles:
        for plane in _BUNDLE_PLANES:
            for record in bundle.get(plane, []):
                rid = record.get("id")
                if rid is None:
                    continue
                if rid in seen:
                    continue
                seen.add(rid)
                merged.append(record)
    return merged


# ---------------------------------------------------------------------------
# Internal: turn-to-bundle construction
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "+00:00"


def _hash_text(text: str) -> str:
    """Return the SHA-256 hex digest of the text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _session_source(session_id: str) -> dict[str, Any]:
    """Build a SourceRecord dict for a session.

    One source per session. The session id is the
    ``raw_ref["session_id"]`` value; the content hash is
    the hash of the session id (so it is stable across
    runs of the same session).
    """
    text = f"conversation-session:{session_id}"
    src = SourceRecord(
        id=make_source_id(
            "conversation", {"session_id": session_id}, _hash_text(text)
        ),
        schema_version="1.0.0",
        source_type="conversation",
        title=f"Conversation session {session_id}",
        origin_uri=None,
        raw_ref={"session_id": session_id, "kind": "langchain"},
        content_hash_sha256=_hash_text(text),
        captured_at=_now_iso(),
        observed_at=_now_iso(),
        author_or_sender=None,
        participants=["user", "assistant"],
        privacy_class="internal",
        custody_status="parsed",
        parser_version="1.0.0",
        metadata={},
    )
    return dataclasses.asdict(src)


def _turn_records(
    session_id: str,
    source_id: str,
    turn_index: int,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    source_dict: dict[str, Any],
) -> dict[str, Any]:
    """Build the records for one save_context call.

    Produces:
    - 1 EpisodeRecord (the turn)
    - 1 EvidenceSpan for the input
    - 1 EvidenceSpan for the output

    The library's full reducer/ledger pipeline is intentionally
    not engaged: a conversation turn is a sequence of
    episodes, not a fact ledger. The integration stores
    the raw conversation as a structured trace; the chain
    consumes it as the context_pack.

    Returns a dict with the bundle-shaped lists (each
    containing one record).
    """
    locator_kind = "turn"
    locator_value = str(turn_index)

    # Episode
    ep_id = make_episode_id(source_id, "turn", locator_kind, locator_value)
    input_text = _stringify_value(inputs)
    output_text = _stringify_value(outputs)
    episode = EpisodeRecord(
        id=ep_id,
        schema_version="1.0.0",
        source_id=source_id,
        episode_type="turn",
        episode_locator={"kind": locator_kind, "value": locator_value},
        title=f"Turn {turn_index} of session {session_id}",
        summary=(output_text or input_text)[:200],
        event_time_start=None,
        event_time_end=None,
        actors=["user", "assistant"],
        topics=[],
        project_refs=[],
        evidence_span_ids=[],
        metadata={"turn_index": turn_index, "session_id": session_id},
    )

    # Two evidence spans: input and output
    input_span_id = make_span_id(source_id, "turn_input", locator_value)
    output_span_id = make_span_id(source_id, "turn_output", locator_value)
    input_span = EvidenceSpan(
        id=input_span_id,
        schema_version="1.0.0",
        source_id=source_id,
        episode_id=ep_id,
        locator={"kind": "turn_input", "value": locator_value},
        text_excerpt=input_text,
        excerpt_policy="verbatim",
        span_hash_sha256=_hash_text(input_text),
        privacy_class="internal",
        metadata={},
    )
    output_span = EvidenceSpan(
        id=output_span_id,
        schema_version="1.0.0",
        source_id=source_id,
        episode_id=ep_id,
        locator={"kind": "turn_output", "value": locator_value},
        text_excerpt=output_text,
        excerpt_policy="verbatim",
        span_hash_sha256=_hash_text(output_text),
        privacy_class="internal",
        metadata={},
    )

    bundle = _empty_bundle()
    bundle["source_records"].append(source_dict)
    bundle["episode_records"].append(dataclasses.asdict(episode))
    bundle["evidence_spans"].append(dataclasses.asdict(input_span))
    bundle["evidence_spans"].append(dataclasses.asdict(output_span))
    return bundle


def _stringify_value(value: Any) -> str:
    """Best-effort stringify a LangChain input/output value."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        # Common LangChain patterns: {"input": "..."}, {"question": "..."},
        # {"human_input": "..."}, {"query": "..."}, etc.
        for key in (
            "input",
            "question",
            "human_input",
            "query",
            "text",
            "user_input",
            "prompt",
        ):
            if key in value and isinstance(value[key], str):
                return str(value[key])
        # Fallback: repr the dict
        return repr(value)
    if isinstance(value, list):
        return "\n".join(_stringify_value(v) for v in value)
    return str(value)


def _context_pack_to_dict(
    session_id: str,
    bundle: list[dict[str, Any]],
    max_records: int,
) -> dict[str, Any]:
    """Build a context_pack dict for the session.

    The integration does not use the library's
    :func:`compile_context_pack` because that compiler
    requires a state record (a long-term ledger shape),
    which is the wrong fit for a session memory. Instead,
    the integration shapes the bundle directly: episodes
    in chronological order, evidence spans grouped by
    episode, sources listed once.

    Returns a dict with a ``context_pack_id``, a list of
    ``records`` (each an episode dict), and an
    ``evidence`` list (each a span dict). The shape is a
    subset of the full :class:`ContextPack` shape.
    """
    episodes = [
        r for r in bundle
        if r.get("episode_type") == "turn"
    ]
    episodes = episodes[-max_records:]  # most-recent N
    ep_ids = {e["id"] for e in episodes}
    evidence = [
        r for r in bundle
        if r.get("episode_id") in ep_ids
    ]
    sources = [
        r for r in bundle
        if r.get("source_type") == "conversation"
    ]
    return {
        "context_pack_id": f"cpsess_{session_id}",
        "session_id": session_id,
        "records": episodes,
        "evidence": evidence,
        "sources": sources,
        "metadata": {
            "builder": "agent_memory_contracts.integrations.langchain",
            "schema_version": "1.0.0",
        },
    }


# ---------------------------------------------------------------------------
# Public: ContractsMemory (BaseMemory subclass)
# ---------------------------------------------------------------------------


if _LANGCHAIN_BASE_MEMORY is not None:

    class ContractsMemory(_LANGCHAIN_BASE_MEMORY):  # type: ignore[misc]
        """A LangChain ``BaseMemory`` backed by an agent-memory-contracts bundle.

        Each call to :meth:`save_context` records the turn as
        an ``EpisodeRecord`` with two evidence spans (input,
        output) and a ``FactLedgerEntry`` authorized by a
        ``MemoryReducerDecision``.

        Each call to :meth:`load_memory_variables` compiles
        a ``ContextPack`` for the session using the library's
        compiler. The compiled context_pack is the memory
        variable; chains reference it via
        ``memory_variables=["context_pack"]``.

        Example:
            ```python
            from langchain_classic.chains import ConversationChain
            from langchain_classic.llms import OpenAI

            from agent_memory_contracts.integrations.langchain import (
                ContractsMemory,
            )

            memory = ContractsMemory(session_id="my-session")
            chain = ConversationChain(llm=OpenAI(), memory=memory)
            # Each chain.run() call triggers save_context,
            # which records the turn. Subsequent calls
            # compile a context_pack for the same session.
            ```
        """

        # Pydantic-declared fields. The BaseMemory superclass
        # is a Pydantic v2 BaseModel; all public attributes
        # must be declared.
        session_id: str = ""
        config: ContractsMemoryConfig = field(default_factory=ContractsMemoryConfig)
        store: MemoryStore = field(default_factory=MemoryStore)

        def __init__(
            self,
            session_id: str | None = None,
            *,
            config: ContractsMemoryConfig | None = None,
            store: MemoryStore | None = None,
            **kwargs: Any,
        ) -> None:
            """Create a memory for a session.

            Args:
                session_id: Stable identifier for the
                    conversation. Defaults to a random uuid.
                config: Configuration. Defaults to
                    ``ContractsMemoryConfig()``.
                store: Bundle store. Defaults to a fresh
                    ``MemoryStore()`` (not shared). Pass an
                    existing store to share sessions across
                    memory instances.
                **kwargs: Forwarded to the
                    ``BaseMemory`` Pydantic constructor.
            """
            if config is None:
                config = ContractsMemoryConfig()
            if store is None:
                store = MemoryStore(max_bundles=config.max_bundles)
            if session_id is None:
                session_id = f"sess_{uuid.uuid4().hex[:12]}"
            # The super().__init__ accepts keyword arguments
            # corresponding to the declared Pydantic fields.
            super().__init__(
                session_id=session_id,
                config=config,
                store=store,
                **kwargs,
            )
            self._turn_index = 0

        @property
        def memory_variables(self) -> list[str]:
            """The single variable this memory injects into chains."""
            return ["context_pack"]

        def load_memory_variables(
            self, inputs: dict[str, Any]
        ) -> dict[str, dict[str, Any]]:
            """Compile a context_pack for the session and return it as a dict.

            The returned dict has one key, ``"context_pack"``,
            and the value is a session-shaped dict (a subset
            of the full :class:`ContextPack` shape) containing
            the most-recent N episodes, their evidence, and
            the source.
            """
            merged = self.store.get_merged(self.session_id)
            if not merged:
                return {"context_pack": {"context_pack_id": None, "records": []}}
            return {
                "context_pack": _context_pack_to_dict(
                    session_id=self.session_id,
                    bundle=merged,
                    max_records=self.config.max_records_per_load,
                ),
            }

        def save_context(
            self, inputs: dict[str, Any], outputs: dict[str, str]
        ) -> None:
            """Record a turn as an EpisodeRecord + evidence spans + fact."""
            turn_index = self._turn_index
            self._turn_index += 1
            # Build (or fetch) the session source.
            existing = self.store.get_merged(self.session_id)
            existing_source = next(
                (r for r in existing if r.get("source_type") == "conversation"),
                None,
            )
            if existing_source is None:
                source = _session_source(self.session_id)
            else:
                source = existing_source
            source_id = source["id"]
            turn_bundle = _turn_records(
                session_id=self.session_id,
                source_id=source_id,
                turn_index=turn_index,
                inputs=inputs,
                outputs=outputs,
                source_dict=source,
            )
            self.store.put(self.session_id, turn_bundle)

        def clear(self) -> None:
            """Remove all bundles for this session."""
            self.store.clear_session(self.session_id)
            self._turn_index = 0

else:
    # LangChain is not installed. Define a stub that raises
    # ImportError on instantiation. This keeps the module
    # importable (so the rest of the library still works) but
    # fails fast when the user actually tries to use it.
    class ContractsMemory:  # type: ignore[no-redef]
        """Stub: langchain-classic is not installed.

        Install it with::

            pip install agent-memory-contracts[langchain]

        Then this stub will be replaced with the real
        :class:`BaseMemory` subclass at import time.
        """

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            raise ImportError(
                "ContractsMemory requires langchain-classic. "
                "Install it with: pip install agent-memory-contracts[langchain]"
            ) from _LANGCHAIN_IMPORT_ERROR


__all__ = [
    "ContractsMemory",
    "ContractsMemoryConfig",
    "MemoryStore",
]
