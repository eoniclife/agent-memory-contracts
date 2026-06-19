"""LangChain integration: a BaseMemory subclass backed by the library.

This module is **optional**. It is not imported by the core library.
To use it, install the optional ``[langchain]`` extra:

    pip install agent-memory-contracts[langchain]

The integration exposes three public names:

- :class:`ContractsMemory` — a ``BaseMemory`` subclass that
  treats each conversation turn as an EpisodeRecord plus input/output
  EvidenceSpan records and returns a legacy ``context_pack`` memory
  variable containing a session-trace envelope on read.
- :class:`MemoryStore` — an in-memory, session-indexed bundle
  store with a soft ``max_bundles`` eviction policy. It is not
  synchronized for concurrent writers.
- :class:`ContractsMemoryConfig` — configuration: privacy class,
  max_bundles, max_records_per_load, and compatibility-retained
  metadata fields.

The integration is a thin shim around the v1.x library. It maps
LangChain's "input + output" shape onto the library's
"source + episode + evidence" shape. It does not promote conversation
turns into trusted ledger facts, run the reducer, or claim end-to-end
poisoning resistance for a LangChain application. Product code that
needs trusted memory should pass extracted candidates through the
library/runtime reducer path before serving them as facts.
"""

from __future__ import annotations

import dataclasses
import hashlib
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Iterator, Literal

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
    EpisodeRecord,
    EvidenceSpan,
    SourceRecord,
    make_episode_id,
    make_source_id,
    make_span_id,
)
from agent_memory_contracts.evidence_contracts import PRIVACY_CLASSES


PrivacyClassStr = Literal["public", "internal", "private", "sensitive", "highly_sensitive"]


def _validate_positive_int(name: str, value: object) -> None:
    """Reject non-positive or non-integer numeric configuration."""
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer; got {value!r}")


@dataclass(frozen=True)
class ContractsMemoryConfig:
    """Configuration for :class:`ContractsMemory`.

    All fields have sensible defaults. Most chains can use
    ``ContractsMemory()`` with no arguments.

    Attributes:
        privacy_class: The privacy class assigned to the
            SourceRecord and EvidenceSpan records generated
            for conversation turns. Defaults to ``"internal"``
            (most chains are for internal tooling). Use
            ``"public"`` for public-facing traces and
            ``"private"`` for private application traces.
        max_bundles: Soft cap on the number of bundles per
            session. When exceeded, the oldest bundle is
            evicted. Must be a positive integer. Defaults to 100.
        max_records_per_load: Cap on the number of episodes
            returned by ``load_memory_variables``. Must be a
            positive integer. Defaults to 20.
        builder_agent: Compatibility-retained metadata field
            from the earlier adapter draft. The current session-trace
            envelope does not build a receipt.
        builder_model: Compatibility-retained metadata field.
            Defaults to ``"none"``.
        exclude_stale: Compatibility-retained field. The adapter
            stores source/episode/evidence trace only and does not
            filter stale records.
        exclude_retracted: Compatibility-retained field; currently
            unused by the session-trace envelope.
        exclude_contested: Compatibility-retained field; currently
            unused by the session-trace envelope.
    """

    privacy_class: PrivacyClassStr = "internal"
    max_bundles: int = 100
    max_records_per_load: int = 20
    builder_agent: str = "agent_memory_contracts.integrations.langchain"
    builder_model: str = "none"
    exclude_stale: bool = True
    exclude_retracted: bool = True
    exclude_contested: bool = True

    def __post_init__(self) -> None:
        if self.privacy_class not in PRIVACY_CLASSES:
            raise ValueError(f"invalid privacy_class: {self.privacy_class!r}")
        _validate_positive_int("max_bundles", self.max_bundles)
        _validate_positive_int("max_records_per_load", self.max_records_per_load)


@dataclass
class MemoryStore:
    """In-memory, session-indexed bundle store.

    Holds the bundle for each session. Each
    :class:`ContractsMemory` instance owns one ``MemoryStore``
    by default, but the same store can be shared across
    multiple memory instances to share a session (advanced use).

    The store is **not** persistent. A v1.1.0+ consideration is
    a file- or DB-backed store; the in-memory form is the
    simplest thing that can work. It is also not synchronized for
    concurrent writers; multithreaded applications sharing one
    store must serialize writes externally or provide a synchronized
    store implementation.

    Attributes:
        max_bundles: Soft cap on the number of bundles per
            session. When exceeded, the oldest bundle is
            evicted. Must be a positive integer.
    """

    max_bundles: int = 100
    _bundles: dict[str, deque[dict[str, Any]]] = field(default_factory=dict)
    _turn_indices: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_positive_int("max_bundles", self.max_bundles)

    def put(self, session_id: str, bundle: dict[str, Any]) -> None:
        """Append a bundle to the session's deque.

        The bundle is a ``dict`` with the library's standard
        bundle shape (lists of records, keyed by plane).
        """
        bundles = self._bundles.setdefault(session_id, deque(maxlen=self.max_bundles))
        bundles.append(bundle)
        self._advance_turn_index_from_bundle(session_id, bundle)

    def next_turn_index(self, session_id: str) -> int:
        """Allocate the next turn index for a session.

        The counter lives on the shared store, not on a
        ``ContractsMemory`` instance, so serialized writers sharing
        a session continue the same sequence instead of each starting
        at zero. The in-memory store is not synchronized for
        concurrent writers.
        """
        if session_id not in self._turn_indices:
            self._turn_indices[session_id] = self._infer_next_turn_index(session_id)
        turn_index = self._turn_indices[session_id]
        self._turn_indices[session_id] = turn_index + 1
        return turn_index

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
        self._turn_indices.pop(session_id, None)

    def session_count(self) -> int:
        """Return the number of sessions in the store."""
        return len(self._bundles)

    def _infer_next_turn_index(self, session_id: str) -> int:
        """Infer a counter from already-stored turn metadata."""
        max_seen = -1
        for bundle in self.get_all(session_id):
            max_seen = max(max_seen, _max_turn_index_in_bundle(bundle))
        return max_seen + 1

    def _advance_turn_index_from_bundle(
        self,
        session_id: str,
        bundle: dict[str, Any],
    ) -> None:
        """Keep the counter ahead of manually inserted bundles."""
        max_seen = _max_turn_index_in_bundle(bundle)
        if max_seen < 0:
            return
        self._turn_indices[session_id] = max(
            self._turn_indices.get(session_id, 0),
            max_seen + 1,
        )


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


def _iter_bundle_records(bundle: Any) -> Iterator[dict[str, Any]]:
    """Yield records from a bundle-shaped dict or legacy flat list."""
    if isinstance(bundle, list):
        for record in bundle:
            if isinstance(record, dict):
                yield record
        return
    if not isinstance(bundle, dict):
        return
    for plane in _BUNDLE_PLANES:
        for record in bundle.get(plane, []):
            if isinstance(record, dict):
                yield record


def _max_turn_index_in_bundle(bundle: Any) -> int:
    """Return the largest integer turn_index found in bundle metadata."""
    max_seen = -1
    for record in _iter_bundle_records(bundle):
        turn_index = record.get("metadata", {}).get("turn_index")
        if isinstance(turn_index, int):
            max_seen = max(max_seen, turn_index)
    return max_seen


def _merge_bundles(bundles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge N dict-of-plane-lists bundles into a flat list of records."""
    merged: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bundle in bundles:
        for record in _iter_bundle_records(bundle):
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


def _session_source(
    session_id: str,
    *,
    privacy_class: PrivacyClassStr,
) -> dict[str, Any]:
    """Build a SourceRecord dict for a session.

    One source per session. The raw reference uses a supported
    synthetic fixture shape so the generated record can pass the
    same validators as normal evidence-plane records.
    """
    text = f"conversation-session:{session_id}"
    content_hash = _hash_text(text)
    raw_ref = {
        "kind": "synthetic_fixture",
        "value": f"langchain-session:{session_id}",
    }
    src = SourceRecord.from_dict({
        "id": make_source_id("other", raw_ref, content_hash),
        "schema_version": "1.0.0",
        "source_type": "other",
        "title": f"Conversation session {session_id}",
        "origin_uri": None,
        "raw_ref": raw_ref,
        "content_hash_sha256": content_hash,
        "captured_at": _now_iso(),
        "observed_at": _now_iso(),
        "author_or_sender": None,
        "participants": ["user", "assistant"],
        "privacy_class": privacy_class,
        "custody_status": "synthetic",
        "parser_version": "1.0.0",
        "metadata": {"integration": "langchain", "session_id": session_id},
    })
    return dataclasses.asdict(src)


def _turn_records(
    session_id: str,
    source_id: str,
    turn_index: int,
    inputs: dict[str, Any],
    outputs: dict[str, Any],
    source_dict: dict[str, Any],
    privacy_class: PrivacyClassStr,
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
    consumes it through the legacy ``context_pack`` memory
    variable.

    Returns a dict with the bundle-shaped lists (each
    containing one record).
    """
    locator_kind = "ordinal"
    locator_value = str(turn_index)
    input_text = _stringify_value(inputs)
    output_text = _stringify_value(outputs)

    # Two evidence spans: input and output
    input_locator_value = f"{turn_index}:input"
    output_locator_value = f"{turn_index}:output"
    input_span_id = make_span_id(source_id, "message_range", input_locator_value)
    output_span_id = make_span_id(source_id, "message_range", output_locator_value)

    # Episode
    ep_id = make_episode_id(
        source_id,
        "conversation_segment",
        locator_kind,
        locator_value,
    )
    episode = EpisodeRecord.from_dict({
        "id": ep_id,
        "schema_version": "1.0.0",
        "source_id": source_id,
        "episode_type": "conversation_segment",
        "episode_locator": {"kind": locator_kind, "value": locator_value},
        "title": f"Turn {turn_index} of session {session_id}",
        "summary": (output_text or input_text)[:200],
        "event_time_start": None,
        "event_time_end": None,
        "actors": ["user", "assistant"],
        "topics": [],
        "project_refs": [],
        "evidence_span_ids": [input_span_id, output_span_id],
        "metadata": {"turn_index": turn_index, "session_id": session_id},
    })
    input_span = EvidenceSpan.from_dict({
        "id": input_span_id,
        "schema_version": "1.0.0",
        "source_id": source_id,
        "episode_id": ep_id,
        "locator": {"kind": "message_range", "value": input_locator_value},
        "text_excerpt": input_text,
        "excerpt_policy": "short_quote_allowed",
        "span_hash_sha256": _hash_text(input_text),
        "privacy_class": privacy_class,
        "metadata": {"role": "input", "turn_index": turn_index},
    })
    output_span = EvidenceSpan.from_dict({
        "id": output_span_id,
        "schema_version": "1.0.0",
        "source_id": source_id,
        "episode_id": ep_id,
        "locator": {"kind": "message_range", "value": output_locator_value},
        "text_excerpt": output_text,
        "excerpt_policy": "short_quote_allowed",
        "span_hash_sha256": _hash_text(output_text),
        "privacy_class": privacy_class,
        "metadata": {"role": "output", "turn_index": turn_index},
    })

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
    """Build the legacy ``context_pack`` session-trace envelope.

    The integration does not use the library's
    :func:`compile_context_pack` because that compiler
    requires a state record (a long-term ledger shape),
    which is the wrong fit for a session memory. Instead,
    the integration shapes the bundle directly: episodes
    in chronological order, evidence spans grouped by
    episode, sources listed once.

    Returns a dict with a ``context_pack_id``, a list of
    ``records`` (each an episode dict), and an ``evidence``
    list (each a span dict). This is not a valid ContextPack
    record and has no build receipt, authority block, trusted
    memory, or reducer semantics.
    """
    episodes = [
        r for r in bundle
        if r.get("episode_type") == "conversation_segment"
    ]
    episodes = episodes[-max_records:]  # most-recent N
    ep_ids = {e["id"] for e in episodes}
    evidence = [
        r for r in bundle
        if r.get("episode_id") in ep_ids
    ]
    sources = [
        r for r in bundle
        if r.get("metadata", {}).get("integration") == "langchain"
        and r.get("metadata", {}).get("session_id") == session_id
    ]
    return {
        "context_pack_id": f"cpsess_{session_id}",
        "session_id": session_id,
        "records": episodes,
        "evidence": evidence,
        "sources": sources,
        "metadata": {
            "builder": "agent_memory_contracts.integrations.langchain",
            "envelope_type": "langchain_session_trace",
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
        output). It does not write trusted ledger entries or
        reducer decisions.

        Each call to :meth:`load_memory_variables` returns a
        legacy ``context_pack`` memory variable containing a
        session-trace envelope. It is not a valid ContextPack
        and carries no build receipt or trusted-memory semantics.
        Chains reference it via
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
            # which records the turn. Subsequent calls load
            # the trace envelope for the same session.
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
            """Return the session trace under the legacy context_pack key.

            The returned dict has one key, ``"context_pack"``,
            and the value is a session-trace envelope containing
            the most-recent N episodes, their evidence, and the
            source. It is not a valid ContextPack record.
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
            """Record a turn as an EpisodeRecord plus evidence spans."""
            # Build (or fetch) the session source.
            existing = self.store.get_merged(self.session_id)
            turn_index = self.store.next_turn_index(self.session_id)
            self._turn_index = turn_index + 1
            existing_source = next(
                (
                    r for r in existing
                    if r.get("metadata", {}).get("integration") == "langchain"
                    and r.get("metadata", {}).get("session_id") == self.session_id
                ),
                None,
            )
            if existing_source is None:
                source = _session_source(
                    self.session_id,
                    privacy_class=self.config.privacy_class,
                )
            else:
                if existing_source.get("privacy_class") != self.config.privacy_class:
                    raise ValueError(
                        "shared LangChain memory session already has "
                        f"privacy_class={existing_source.get('privacy_class')!r}; "
                        f"got {self.config.privacy_class!r}"
                    )
                source = existing_source
            source_id = source["id"]
            turn_bundle = _turn_records(
                session_id=self.session_id,
                source_id=source_id,
                turn_index=turn_index,
                inputs=inputs,
                outputs=outputs,
                source_dict=source,
                privacy_class=self.config.privacy_class,
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
