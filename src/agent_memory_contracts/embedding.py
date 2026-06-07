"""Embedding input: the canonical text-rendering surface for records.

The library's job in v1.0.0-alpha.1 is to define the
**embedding input boundary** — the contract between the
library and the product's embedding pipeline.

The product's pipeline is typically:

1. Apply a :class:`~agent_memory_contracts.access.BundleScope`
   to a bundle (which records are allowed at this scope).
2. For each allowed record, call
   :func:`record_to_embedding_input` to get an
   :class:`EmbeddingInput`.
3. Feed :attr:`EmbeddingInput.text` to the embedding model
   of choice (OpenAI, Cohere, sentence-transformers, etc.).
4. Store the resulting vector alongside
   :attr:`EmbeddingInput.metadata` in the product's vector DB.
5. Use :attr:`EmbeddingInput.content_hash_sha256` for
   deduplication.

The library does not call embedding models, does not provide a
vector store, and does not decide which records to embed. The
product owns those concerns. What the library *does* provide
is a deterministic, privacy-aware, well-typed
:class:`EmbeddingInput` and a per-record-type renderer that
produces natural-language text suitable for any text-
embedding model.

The text rendering is **deterministic**: two records with the
same content produce the same ``text`` and the same
``content_hash_sha256``. This makes deduplication trivial at
the embedding-input layer (the product can skip re-embedding
records whose ``content_hash_sha256`` is already in the
vector store).

The text rendering is **type-aware**: 12 per-type renderers
(one each for the 12 record types that are typically
embedded) emit a structured-template natural-language
rendering. Anything that doesn't match a known type falls
back to a generic "key: value" renderer.

The library surfaces the privacy class on the
:class:`EmbeddingInput` so the product can apply a
:class:`~agent_memory_contracts.access.BundleScope` before
deciding what to embed. The library does not enforce
privacy — the product owns that decision.

Like the rest of the bundle primitives, this module is
standard-library only.

.. versionadded:: 1.0.0-alpha.1
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from .evidence_contracts import EpisodeRecord, EvidenceSpan, SourceRecord
from .evidence_ids import sha256_hex
from .candidate_contracts import (
    CandidateClaim,
    CandidateDecision,
    CandidatePreference,
    CandidateTask,
    CandidateTasteSignal,
)
from .ledger_contracts import (
    DecisionLedgerEntry,
    FactLedgerEntry,
    MemoryReducerDecision,
    PreferenceLedgerEntry,
)
from .taste_contracts import TasteCard
from .contextpack_contracts import ContextPack


# ---------------------------------------------------------------------------
# Default constants
# ---------------------------------------------------------------------------


#: Default maximum text length for an :class:`EmbeddingInput`.
#: Generous for most embedding models (8K chars is roughly 2K
#: tokens); conservative for the long-input models.
DEFAULT_MAX_CHARS: int = 8192

#: Truncation marker appended at the end of a truncated
#: ``text`` field. The product can search for this in stored
#: embeddings to identify truncated inputs.
TRUNCATION_MARKER: str = "...[truncated]"

#: Maximum number of fields emitted by the generic renderer.
#: Caps the size of a fallback rendering for unknown record
#: types.
GENERIC_RENDERER_MAX_FIELDS: int = 50

#: Window size (in chars) for the sentence-boundary search
#: during truncation.
TRUNCATION_BOUNDARY_WINDOW: int = 200


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbeddingInput:
    """The canonical input to an embedding pipeline.

    The :attr:`text` field is the headline: a deterministic,
    natural-language rendering of the source record, suitable
    for any text-embedding model. The :attr:`content_hash_sha256`
    is the deduplication key — two records with the same
    content produce the same text and the same hash. The
    :attr:`metadata` field is structured (primitive values
    only) so the product can pass it verbatim to any vector
    DB's filter API.

    Attributes:
        record_id: The source record's content-derived id
            (e.g., ``src_<hex>`` for a ``SourceRecord``).
            Used by the product to link the embedding input
            back to the record.
        record_type: A stable record-type string
            (``"source_record"``, ``"fact_ledger_entry"``,
            ``"evidence_span"``, etc.). Used for filtering
            in the vector DB.
        text: The canonical natural-language rendering of
            the record. Deterministic; same record content
            produces the same text.
        privacy_class: The record's privacy class, surfaced
            so the product can apply a
            :class:`~agent_memory_contracts.access.BundleScope`
            before deciding what to embed. Defaults to
            ``"internal"`` for records that don't carry a
            privacy class.
        content_hash_sha256: A 64-char hex SHA-256 of the
            canonical text. The deduplication key.
        char_count: ``len(text)`` at construction time. The
            product can use this to estimate token cost
            without re-measuring.
        metadata: Flat key-value pairs (values are
            ``str | int | float | bool`` only). Default
            includes ``record_id``, ``record_type``,
            ``privacy_class``, and ``plane``. Per-type
            renderers add extras.
        truncated: ``True`` iff the text was clipped to
            ``max_chars``. The product can re-render with
            a larger ``max_chars`` to get the full text.
        plane: The memory plane this record belongs to
            (``"evidence"``, ``"candidate"``,
            ``"ledger"``, ``"taste"``, ``"state"``,
            ``"contextpack"``, or ``"audit"`` for
            audit-only records).
    """

    record_id: str
    record_type: str
    text: str
    privacy_class: str
    content_hash_sha256: str
    char_count: int
    metadata: Mapping[str, str | int | float | bool] = field(default_factory=dict)
    truncated: bool = False
    plane: str = "audit"

    def __post_init__(self) -> None:
        # Sanity-check invariants. These are caught at
        # construction time so that bad inputs don't make
        # it into the product's embedding pipeline.
        if not self.record_id:
            raise ValueError("EmbeddingInput.record_id is required")
        if not self.text:
            raise ValueError("EmbeddingInput.text is required (record is empty after rendering)")
        if len(self.content_hash_sha256) != 64:
            raise ValueError(
                f"content_hash_sha256 must be 64 hex chars, got {len(self.content_hash_sha256)}"
            )
        if self.char_count != len(self.text):
            raise ValueError(
                f"char_count={self.char_count} does not match len(text)={len(self.text)}"
            )


# ---------------------------------------------------------------------------
# Free function: record -> EmbeddingInput
# ---------------------------------------------------------------------------


def record_to_embedding_input(
    record: Any,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
) -> EmbeddingInput:
    """Render a record as an :class:`EmbeddingInput`.

    Dispatches on the record's type. The 12 most-common
    record types have hand-crafted renderers; anything else
    falls back to a generic "key: value" renderer.

    Args:
        record: a record (dataclass, dict, or Mapping).
        max_chars: maximum text length. If the rendered text
            exceeds this, it is truncated at the nearest
            sentence boundary (within the last 200 chars of
            the cut point), with a hard cut as fallback. The
            ``truncated`` field on the result is set
            accordingly. Default :data:`DEFAULT_MAX_CHARS`.

    Returns:
        A frozen :class:`EmbeddingInput`.

    Raises:
        ValueError: if the record is empty (no recognizable
            type, no fields, no text-equivalent content).
    """
    if max_chars <= 0:
        raise ValueError(f"max_chars must be > 0, got {max_chars}")

    record_id = _record_id(record) or ""
    record_type = _record_type_string(record)
    plane = _record_plane(record_type)
    privacy_class = _record_privacy_class(record)
    full_text = text_for_record_type(record)
    if not full_text:
        raise ValueError(
            f"cannot render empty record (id={record_id!r}, type={record_type!r}); "
            "no text-equivalent content found"
        )

    truncated, text = _truncate(full_text, max_chars)
    content_hash = sha256_hex(text)
    metadata = _build_metadata(
        record=record,
        record_id=record_id,
        record_type=record_type,
        privacy_class=privacy_class,
        plane=plane,
    )
    return EmbeddingInput(
        record_id=record_id,
        record_type=record_type,
        text=text,
        privacy_class=privacy_class,
        content_hash_sha256=content_hash,
        char_count=len(text),
        metadata=metadata,
        truncated=truncated,
        plane=plane,
    )


# ---------------------------------------------------------------------------
# Per-type text renderers (public)
# ---------------------------------------------------------------------------


def text_for_record_type(record: Any) -> str:
    """Return the canonical text rendering for a record.

    The 12 most-common record types have hand-crafted
    renderers. Anything that doesn't match a known type
    falls back to a generic "key: value" renderer.

    Per-type renderers, in dispatch order:

    1. :class:`~agent_memory_contracts.evidence_contracts.SourceRecord`
    2. :class:`~agent_memory_contracts.evidence_contracts.EpisodeRecord`
    3. :class:`~agent_memory_contracts.evidence_contracts.EvidenceSpan`
    4. :class:`~agent_memory_contracts.candidate_contracts.CandidateClaim`
    5. :class:`~agent_memory_contracts.candidate_contracts.CandidateDecision`
    6. :class:`~agent_memory_contracts.candidate_contracts.CandidatePreference`
    7. :class:`~agent_memory_contracts.candidate_contracts.CandidateTask`
    8. :class:`~agent_memory_contracts.candidate_contracts.CandidateTasteSignal`
    9. :class:`~agent_memory_contracts.ledger_contracts.FactLedgerEntry`
    10. :class:`~agent_memory_contracts.ledger_contracts.DecisionLedgerEntry`
    11. :class:`~agent_memory_contracts.ledger_contracts.PreferenceLedgerEntry`
    12. :class:`~agent_memory_contracts.taste_contracts.TasteCard`
    13. :class:`~agent_memory_contracts.contextpack_contracts.ContextPack`
    14. *generic fallback*

    Args:
        record: a record (dataclass, dict, or Mapping).

    Returns:
        A deterministic natural-language string. Empty
        string if the record has no text-equivalent content
        and no recognizable fields.
    """
    if record is None:
        return ""
    # The dispatch is isinstance-first, then shape-based
    # fallback. This makes the order matter: a dict that
    # looks like a SourceRecord is rendered as a SourceRecord.
    if isinstance(record, SourceRecord) or _looks_like(record, "source_record"):
        return _render_source_record(record)
    if isinstance(record, EpisodeRecord) or _looks_like(record, "episode_record"):
        return _render_episode_record(record)
    if isinstance(record, EvidenceSpan) or _looks_like(record, "evidence_span"):
        return _render_evidence_span(record)
    if isinstance(record, CandidateClaim) or _looks_like(record, "candidate_claim"):
        return _render_candidate_claim(record)
    if isinstance(record, CandidateDecision) or _looks_like(record, "candidate_decision"):
        return _render_candidate_decision(record)
    if isinstance(record, CandidatePreference) or _looks_like(record, "candidate_preference"):
        return _render_candidate_preference(record)
    if isinstance(record, CandidateTask) or _looks_like(record, "candidate_task"):
        return _render_candidate_task(record)
    if isinstance(record, CandidateTasteSignal) or _looks_like(record, "candidate_taste_signal"):
        return _render_candidate_taste_signal(record)
    if isinstance(record, FactLedgerEntry) or _looks_like(record, "fact_ledger_entry"):
        return _render_fact_ledger_entry(record)
    if isinstance(record, DecisionLedgerEntry) or _looks_like(record, "decision_ledger_entry"):
        return _render_decision_ledger_entry(record)
    if isinstance(record, PreferenceLedgerEntry) or _looks_like(record, "preference_ledger_entry"):
        return _render_preference_ledger_entry(record)
    if isinstance(record, TasteCard) or _looks_like(record, "taste_card"):
        return _render_taste_card(record)
    if isinstance(record, ContextPack) or _looks_like(record, "context_pack"):
        return _render_context_pack(record)
    return _render_generic(record)


# ---------------------------------------------------------------------------
# Per-type text renderers (private)
# ---------------------------------------------------------------------------


def _render_source_record(record: Any) -> str:
    title = _field(record, "title")
    author = _field(record, "author_or_sender") or "unknown"
    source_type = _field(record, "source_type") or "unknown"
    origin = _field(record, "origin_uri")
    if not origin:
        raw_ref = _field(record, "raw_ref")
        if isinstance(raw_ref, Mapping):
            origin = str(raw_ref.get("value", ""))
    captured = _field(record, "captured_at") or "unknown"
    parts: list[str] = []
    if title:
        parts.append(f"Title: {title}")
    parts.append(f"Author: {author}")
    parts.append(f"Type: {source_type}")
    if origin:
        parts.append(f"URI: {origin}")
    parts.append(f"Captured: {captured}")
    return "\n".join(parts)


def _render_episode_record(record: Any) -> str:
    title = _field(record, "title")
    ep_type = _field(record, "episode_type") or "unknown"
    source_id = _field(record, "source_id") or "unknown"
    summary = _field(record, "summary") or ""
    actors = _list_field(record, "actors")
    topics = _list_field(record, "topics")
    start = _field(record, "event_time_start") or "unknown"
    end = _field(record, "event_time_end") or "ongoing"
    parts: list[str] = []
    if title:
        parts.append(f"Episode: {title}")
    parts.append(f"Type: {ep_type}")
    parts.append(f"Source: {source_id}")
    if summary:
        parts.append(f"Summary: {summary}")
    if actors:
        parts.append(f"Actors: {', '.join(actors)}")
    if topics:
        parts.append(f"Topics: {', '.join(topics)}")
    parts.append(f"Time: {start} to {end}")
    parts.append(_evidence_footer(record))
    return "\n".join(parts)


def _render_evidence_span(record: Any) -> str:
    span_id = _field(record, "id") or "unknown"
    source_id = _field(record, "source_id") or "unknown"
    locator = _field(record, "locator")
    loc_str = ""
    if isinstance(locator, Mapping):
        loc_str = f"{locator.get('kind', '?')}={locator.get('value', '?')}"
    excerpt = _field(record, "text_excerpt")
    policy = _field(record, "excerpt_policy") or "none"
    privacy = _field(record, "privacy_class") or "internal"
    parts: list[str] = [f"Span {span_id} in source {source_id}"]
    if loc_str:
        parts.append(f"Locator: {loc_str}")
    if excerpt:
        parts.append(f"Text: {excerpt}")
    else:
        parts.append(f"Text: [no excerpt; excerpt_policy={policy}]")
    parts.append(f"Privacy: {privacy}")
    return "\n".join(parts)


def _render_candidate_claim(record: Any) -> str:
    claim_text = _field(record, "claim_text") or ""
    subject = _field(record, "subject") or ""
    predicate = _field(record, "predicate") or ""
    object_ = _field(record, "object") or ""
    scope = _field(record, "claim_scope") or ""
    confidence = _field(record, "confidence") or "unknown"
    parts: list[str] = []
    if claim_text:
        parts.append(f"Claim: {claim_text}")
    if subject and predicate and object_:
        parts.append(f"Subject: {subject} {predicate} {object_}")
    if scope:
        parts.append(f"Scope: {scope}")
    parts.append(f"Confidence: {confidence}")
    parts.append(_evidence_footer(record))
    return "\n".join(parts)


def _render_candidate_decision(record: Any) -> str:
    decision_text = _field(record, "decision_text") or ""
    scope = _field(record, "decision_scope") or ""
    owner = _field(record, "owner_hint") or ""
    rationale = _field(record, "rationale_text") or ""
    alts = _list_field(record, "alternatives_mentioned")
    reversibility = _field(record, "reversibility") or ""
    parts: list[str] = []
    if decision_text:
        parts.append(f"Decision: {decision_text}")
    if scope:
        parts.append(f"Scope: {scope}")
    if owner:
        parts.append(f"Owner: {owner}")
    if rationale:
        parts.append(f"Rationale: {rationale}")
    if alts:
        parts.append(f"Alternatives: {', '.join(alts)}")
    if reversibility:
        parts.append(f"Reversibility: {reversibility}")
    parts.append(_evidence_footer(record))
    return "\n".join(parts)


def _render_candidate_preference(record: Any) -> str:
    pref_text = _field(record, "preference_text") or ""
    subject = _field(record, "subject") or ""
    domain = _field(record, "domain") or ""
    scope = _field(record, "scope") or ""
    strength = _field(record, "strength_hint") or ""
    parts: list[str] = []
    if pref_text:
        parts.append(f"Preference: {pref_text}")
    if subject:
        parts.append(f"Subject: {subject}")
    if domain:
        parts.append(f"Domain: {domain}")
    if scope:
        parts.append(f"Scope: {scope}")
    if strength:
        parts.append(f"Strength: {strength}")
    parts.append(_evidence_footer(record))
    return "\n".join(parts)


def _render_candidate_task(record: Any) -> str:
    task_text = _field(record, "task_text") or ""
    task_kind = _field(record, "task_kind") or ""
    project_refs = _list_field(record, "project_refs")
    owner = _field(record, "owner_hint") or ""
    urgency = _field(record, "urgency_hint") or ""
    parts: list[str] = []
    if task_text:
        parts.append(f"Task: {task_text}")
    if task_kind:
        parts.append(f"Kind: {task_kind}")
    if project_refs:
        parts.append(f"Projects: {', '.join(project_refs)}")
    if owner:
        parts.append(f"Owner: {owner}")
    if urgency:
        parts.append(f"Urgency: {urgency}")
    parts.append(_evidence_footer(record))
    return "\n".join(parts)


def _render_candidate_taste_signal(record: Any) -> str:
    taste_text = _field(record, "taste_text") or ""
    domain = _field(record, "domain") or ""
    signal_kind = _field(record, "signal_kind") or ""
    strength = _field(record, "strength_hint") or ""
    examples = _list_field(record, "example_span_ids")
    contrast = _list_field(record, "contrast_span_ids")
    parts: list[str] = []
    if taste_text:
        parts.append(f"Taste: {taste_text}")
    if domain:
        parts.append(f"Domain: {domain}")
    if signal_kind:
        parts.append(f"Kind: {signal_kind}")
    if strength:
        parts.append(f"Strength: {strength}")
    if examples:
        parts.append(f"Examples: {', '.join(examples)}")
    if contrast:
        parts.append(f"Contrast: {', '.join(contrast)}")
    parts.append(_evidence_footer(record))
    return "\n".join(parts)


def _render_fact_ledger_entry(record: Any) -> str:
    fact_text = _field(record, "fact_text") or ""
    subject = _field(record, "subject") or ""
    predicate = _field(record, "predicate") or ""
    object_ = _field(record, "object") or ""
    scope = _field(record, "scope") or ""
    confidence = _field(record, "confidence") or "unknown"
    valid_from = _field(record, "valid_from") or ""
    parts: list[str] = []
    if fact_text:
        parts.append(fact_text)
    if subject and predicate and object_:
        parts.append(f"Subject: {subject} {predicate} {object_}")
    if scope:
        parts.append(f"Scope: {scope}")
    if confidence:
        parts.append(f"Confidence: {confidence}")
    if valid_from:
        parts.append(f"Valid from: {valid_from}")
    parts.append(_evidence_footer(record))
    return "\n".join(parts)


def _render_decision_ledger_entry(record: Any) -> str:
    decision_text = _field(record, "decision_text") or ""
    scope = _field(record, "decision_scope") or ""
    owner = _field(record, "owner") or ""
    rationale = _field(record, "rationale_text") or ""
    alts = _list_field(record, "alternatives_considered")
    reversibility = _field(record, "reversibility") or ""
    parts: list[str] = []
    if decision_text:
        parts.append(f"Decision: {decision_text}")
    if scope:
        parts.append(f"Scope: {scope}")
    if owner:
        parts.append(f"Owner: {owner}")
    if rationale:
        parts.append(f"Rationale: {rationale}")
    if alts:
        parts.append(f"Alternatives considered: {', '.join(alts)}")
    if reversibility:
        parts.append(f"Reversibility: {reversibility}")
    parts.append(_evidence_footer(record))
    return "\n".join(parts)


def _render_preference_ledger_entry(record: Any) -> str:
    pref_text = _field(record, "preference_text") or ""
    subject = _field(record, "subject") or ""
    domain = _field(record, "domain") or ""
    strength = _field(record, "strength") or ""
    parts: list[str] = []
    if pref_text:
        parts.append(f"Preference: {pref_text}")
    if subject:
        parts.append(f"Subject: {subject}")
    if domain:
        parts.append(f"Domain: {domain}")
    if strength:
        parts.append(f"Strength: {strength}")
    parts.append(_evidence_footer(record))
    return "\n".join(parts)


def _render_taste_card(record: Any) -> str:
    principle = _field(record, "principle") or ""
    rationale = _field(record, "rationale") or ""
    subject = _field(record, "subject") or ""
    domain = _field(record, "domain") or ""
    taste_kind = _field(record, "taste_kind") or ""
    strength = _field(record, "strength") or ""
    parts: list[str] = []
    if principle:
        parts.append(f"Principle: {principle}")
    if rationale:
        parts.append(f"Rationale: {rationale}")
    if subject:
        parts.append(f"Subject: {subject}")
    if domain:
        parts.append(f"Domain: {domain}")
    if taste_kind:
        parts.append(f"Kind: {taste_kind}")
    if strength:
        parts.append(f"Strength: {strength}")
    parts.append(_evidence_footer(record))
    return "\n".join(parts)


def _render_context_pack(record: Any) -> str:
    pack_type = _field(record, "pack_type") or "general"
    task = _field(record, "task")
    task_text = ""
    if isinstance(task, Mapping):
        # The ContextPack's task is a structured object with
        # task_id, task_title, task_type, task_summary, etc.
        # Surface the most useful text field for embedding:
        # task_summary (free-form) > task_title (the question)
        # > task_type (the kind).
        task_text = str(
            task.get("task_summary", "")
            or task.get("task_title", "")
            or task.get("task_type", "")
        )
    elif isinstance(task, str):
        task_text = task
    retrieval_trace = _field(record, "retrieval_trace")
    trace_str = ""
    if isinstance(retrieval_trace, list):
        trace_str = "; ".join(str(x) for x in retrieval_trace if x)
    elif isinstance(retrieval_trace, str):
        trace_str = retrieval_trace
    parts: list[str] = [f"ContextPack: {pack_type}"]
    if task_text:
        parts.append(f"Task: {task_text}")
    primary = _list_field(record, "trusted_memory")  # trusted_memory is a list of ledger ids
    if primary:
        parts.append(f"Trusted memory: {', '.join(primary)}")
    candidates = _list_field(record, "candidate_context")
    if candidates:
        parts.append(f"Candidate context: {', '.join(candidates)}")
    if trace_str:
        parts.append(f"Retrieval trace: {trace_str}")
    return "\n".join(parts)


def _render_generic(record: Any) -> str:
    """Render an unknown record type as a sorted key: value list.

    Used for audit records (MemoryReducerDecision,
    ContextPackBuildReceipt, ContextPackValidationReport),
    state snapshots, and any other record that doesn't match
    a hand-crafted renderer.
    """
    items = _iter_fields(record)
    if not items:
        return ""
    items = items[:GENERIC_RENDERER_MAX_FIELDS]
    # Sort by key for determinism.
    items.sort(key=lambda kv: kv[0])
    lines = [f"{k}: {_format_value(v)}" for k, v in items]
    return "\n".join(lines)


def _evidence_footer(record: Any) -> str:
    """Standard footer line listing evidence span ids for a claim."""
    span_ids = _list_field(record, "evidence_span_ids")
    if not span_ids:
        # Some claim types use evidence_id (singular) for
        # older record variants.
        single = _field(record, "evidence_id")
        if isinstance(single, str) and single:
            span_ids = [single]
    if not span_ids:
        return ""
    return f"Evidence: {', '.join(span_ids)}"


# ---------------------------------------------------------------------------
# Round-trip helpers
# ---------------------------------------------------------------------------


def embedding_input_to_dict(ei: EmbeddingInput) -> dict[str, Any]:
    """Serialize an :class:`EmbeddingInput` to a JSON-friendly dict."""
    return {
        "record_id": ei.record_id,
        "record_type": ei.record_type,
        "text": ei.text,
        "privacy_class": ei.privacy_class,
        "content_hash_sha256": ei.content_hash_sha256,
        "char_count": ei.char_count,
        "metadata": dict(ei.metadata),
        "truncated": ei.truncated,
        "plane": ei.plane,
    }


def embedding_input_from_dict(data: Mapping[str, Any]) -> EmbeddingInput:
    """Reconstruct an :class:`EmbeddingInput` from a dict.

    The inverse of :func:`embedding_input_to_dict`. Used by
    the product to persist embedding inputs to disk between
    runs.
    """
    return EmbeddingInput(
        record_id=str(data.get("record_id", "")),
        record_type=str(data.get("record_type", "")),
        text=str(data.get("text", "")),
        privacy_class=str(data.get("privacy_class", "internal")),
        content_hash_sha256=str(data.get("content_hash_sha256", "")),
        char_count=int(data.get("char_count", 0)),
        metadata=dict(data.get("metadata", {})),
        truncated=bool(data.get("truncated", False)),
        plane=str(data.get("plane", "audit")),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _field(record: Any, name: str) -> Any:
    """Read a field from a record (dataclass or Mapping), or None."""
    if record is None:
        return None
    if hasattr(record, name):
        return getattr(record, name, None)
    if isinstance(record, Mapping):
        return record.get(name)
    return None


def _list_field(record: Any, name: str) -> list[str]:
    """Read a list-of-strings field; return [] if absent or not a list."""
    value = _field(record, name)
    if isinstance(value, (list, tuple)):
        return [str(x) for x in value if x is not None]
    return []


def _iter_fields(record: Any) -> list[tuple[str, Any]]:
    """Iterate (key, value) pairs over a record's fields, in declaration order.

    For dataclass records, uses ``dataclasses.fields`` and
    ``getattr``. For Mapping/dict records, iterates ``items()``.
    The result excludes fields whose value is ``None`` (those
    are typically optional and not interesting for embedding).
    """
    import dataclasses
    if record is None:
        return []
    if hasattr(record, "__dataclass_fields__"):
        out: list[tuple[str, Any]] = []
        for f in dataclasses.fields(record):
            value = getattr(record, f.name, None)
            if value is None:
                continue
            out.append((f.name, value))
        return out
    if isinstance(record, Mapping):
        return [(str(k), v) for k, v in record.items() if v is not None]
    return []


def _format_value(value: Any) -> str:
    """Format a value for the generic renderer."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return ", ".join(_format_value(x) for x in value if x is not None)
    if isinstance(value, Mapping):
        # Stable JSON for nested dicts.
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)


def _record_id(record: Any) -> str | None:
    """Return the id of a record (dataclass or dict), or None."""
    if record is None:
        return None
    if hasattr(record, "id"):
        value = getattr(record, "id", None)
        if isinstance(value, str) and value:
            return value
    if isinstance(record, Mapping):
        value = record.get("id")
        if isinstance(value, str) and value:
            return value
    return None


def _record_type_string(record: Any) -> str:
    """Return a stable record-type string for the embedding input."""
    if record is None:
        return ""
    cls = getattr(record, "__class__", None)
    if cls is not None and hasattr(cls, "__name__") and cls.__name__ != "dict":
        return _snake_case(cls.__name__)
    if isinstance(record, Mapping):
        # Shape-based detection for dicts.
        if "source_type" in record:
            return "source_record"
        if "episode_type" in record:
            return "episode_record"
        if "span_hash_sha256" in record:
            return "evidence_span"
        if "candidate_type" in record:
            ct = str(record.get("candidate_type", ""))
            return f"candidate_{ct}" if ct else "candidate"
        if "ledger_type" in record:
            return f"{record['ledger_type']}_ledger_entry"
        if "card_type" in record:
            return "taste_card"
        if "pack_type" in record:
            return "context_pack"
        if "decision_type" in record:
            return "memory_reducer_decision"
        if "context_pack_id" in record and "builder" in record:
            return "context_pack_build_receipt"
        if "context_pack_id" in record and "validator" in record:
            return "context_pack_validation_report"
    return "unknown"


def _record_plane(record_type: str) -> str:
    """Map a record type to its memory plane."""
    if record_type in ("source_record", "episode_record", "evidence_span"):
        return "evidence"
    if record_type.startswith("candidate_"):
        return "candidate"
    if record_type.endswith("_ledger_entry"):
        return "ledger"
    if record_type in ("taste_card", "taste_card_signal"):
        return "taste"
    if record_type == "context_pack":
        return "contextpack"
    if "state" in record_type:
        return "state"
    return "audit"


def _record_privacy_class(record: Any) -> str:
    """Return the privacy class of a record, defaulting to ``"internal"``."""
    if record is None:
        return "internal"
    value = _field(record, "privacy_class")
    if isinstance(value, str) and value:
        return value
    return "internal"


def _looks_like(record: Any, expected_type: str) -> bool:
    """Return whether a dict-shaped record looks like ``expected_type``.

    Used by :func:`text_for_record_type` to dispatch dict
    records to the right per-type renderer.
    """
    if not isinstance(record, Mapping):
        return False
    if expected_type == "source_record":
        return "source_type" in record and "title" in record
    if expected_type == "episode_record":
        return "episode_type" in record and "episode_locator" in record
    if expected_type == "evidence_span":
        return "span_hash_sha256" in record
    if expected_type == "candidate_claim":
        return (
            record.get("candidate_type") == "claim"
            or ("claim_text" in record and "ledger_type" not in record)
        )
    if expected_type == "candidate_decision":
        # Must not match a ledger entry; ledger entries also
        # have decision_text.
        return (
            record.get("candidate_type") == "decision"
            or ("decision_text" in record and "ledger_type" not in record)
        )
    if expected_type == "candidate_preference":
        return (
            record.get("candidate_type") == "preference"
            or ("preference_text" in record and "ledger_type" not in record)
        )
    if expected_type == "candidate_task":
        return (
            record.get("candidate_type") == "task"
            or ("task_text" in record and "ledger_type" not in record)
        )
    if expected_type == "candidate_taste_signal":
        return (
            record.get("candidate_type") == "taste_signal"
            or ("taste_text" in record and "card_type" not in record)
        )
    if expected_type == "fact_ledger_entry":
        return record.get("ledger_type") == "fact" or "fact_text" in record
    if expected_type == "decision_ledger_entry":
        return record.get("ledger_type") == "decision" or (
            "decision_text" in record and "owner" in record
        )
    if expected_type == "preference_ledger_entry":
        return record.get("ledger_type") == "preference" or (
            "preference_text" in record and "domain" in record
        )
    if expected_type == "taste_card":
        return "principle" in record and "taste_kind" in record
    if expected_type == "context_pack":
        return "pack_type" in record and "pack_hash_sha256" in record
    return False


def _snake_case(name: str) -> str:
    """Convert CamelCase to snake_case without importing ``re``."""
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _truncate(text: str, max_chars: int) -> tuple[bool, str]:
    """Truncate text to ``max_chars``, with sentence-boundary preference.

    Returns ``(truncated, text)``. If no truncation was
    needed, ``truncated=False`` and the original text is
    returned unchanged.
    """
    if len(text) <= max_chars:
        return False, text

    # Find the last sentence boundary in the last
    # TRUNCATION_BOUNDARY_WINDOW chars before max_chars.
    # A "sentence boundary" is a ``.``, ``!``, ``?``, or
    # ``\n`` followed by whitespace or end-of-string.
    boundary_window_start = max(0, max_chars - TRUNCATION_BOUNDARY_WINDOW)
    cut_at = max_chars  # fallback: hard cut.
    for i in range(max_chars - 1, boundary_window_start - 1, -1):
        ch = text[i]
        if ch in (".", "!", "?", "\n"):
            # Confirm this is a sentence boundary (followed
            # by whitespace, end-of-string, or another
            # terminator).
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if nxt == "" or nxt.isspace() or nxt in (".", "!", "?"):
                cut_at = i + 1
                break

    truncated_text = text[:cut_at].rstrip() + TRUNCATION_MARKER
    # If even the truncated text exceeds max_chars (rare; only
    # if the marker pushed us over), hard-cut.
    if len(truncated_text) > max_chars + len(TRUNCATION_MARKER):
        truncated_text = text[:max_chars].rstrip() + TRUNCATION_MARKER
    return True, truncated_text


def _build_metadata(
    *,
    record: Any,
    record_id: str,
    record_type: str,
    privacy_class: str,
    plane: str,
) -> dict[str, str | int | float | bool]:
    """Build the flat metadata dict for an EmbeddingInput.

    Includes the four universal keys (``record_id``,
    ``record_type``, ``privacy_class``, ``plane``) plus
    per-type extras (e.g., ``subject``/``predicate``/
    ``object`` for a FactLedgerEntry). All values are
    coerced to ``str | int | float | bool``.
    """
    metadata: dict[str, str | int | float | bool] = {
        "record_id": record_id,
        "record_type": record_type,
        "privacy_class": privacy_class,
        "plane": plane,
    }
    # Per-type extras.
    for name in (
        "source_type",
        "subject",
        "predicate",
        "object",
        "scope",
        "confidence",
        "context_pack_kind",
        "domain",
        "taste_kind",
        "claim_scope",
    ):
        value = _field(record, name)
        coerced = _coerce_metadata_value(value)
        if coerced is not None:
            metadata[name] = coerced
    return metadata


def _coerce_metadata_value(value: Any) -> str | int | float | bool | None:
    """Coerce a value to a primitive type for the metadata dict.

    Returns ``None`` for ``None``, empty strings, and empty
    lists (so the metadata dict doesn't carry empty keys).
    Nested dicts and lists are converted to compact JSON
    strings so the metadata remains a flat primitive-value
    dict (compatible with any vector DB filter API).
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, str):
        return value if value else None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        return ", ".join(str(x) for x in value if x is not None)
    if isinstance(value, Mapping):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    return str(value)
