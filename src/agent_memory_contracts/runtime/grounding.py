"""ADR-7/8: deterministic grounding -- receipted packs, cite-or-refuse.

Two functions, both deterministic and both receipted:

- :func:`build_context_pack` (ADR-7) assembles a ContextPack from
  the store: the active ledger at ``as_of`` (temporal +
  supersession + status filters), scope- and privacy-filtered,
  ranked by keyword score + authorizing-decision recency (weights
  in :data:`_PACK_RANK_WEIGHTS`, logged in the receipt;
  taste-plane weight stubbed at 0 per OPEN item O-2), selected
  greedily into a token budget (chars/4 proxy -- the product
  substitutes a real tokenizer). The pack itself is compiled by
  the library (``compile_context_pack``), which needs a state
  reference: the runtime synthesizes a deterministic
  ``ProjectStateSnapshot`` over the selected entries (a deliberate
  adaptation -- the state *reducer* is product scope, but the
  contracts require the state plane, so the runtime derives it
  content-addressed from the selection). Packs + receipts are
  persisted (content-addressed, anchored), so "what did the AI
  know when" is answerable by fingerprint forever.

  **Determinism rule (tested by the invariant suite):** same
  store state + same request => same selection => same pack id =>
  byte-identical fingerprint.

- :func:`answer` (ADR-8) is the refusal-is-the-feature contract.
  It builds a pack from the question and either:

  * **answers**, with every factual sentence carrying a ``[Fi]``
    citation that resolves to a pack fact -- enforced
    *mechanically* by :func:`verify_grounding`, not by prompt
    discipline. There are NO model calls here: the reference
    answerer is a deterministic template over the cited facts, so
    the whole contract is testable. (The product swaps in an LLM
    behind the same post-validation; the checker does not care
    who wrote the sentences.)
  * **refuses**, structured: ``reason="not_in_trusted_memory"``
    plus the ids of unreviewed candidates that look relevant --
    the honest "I don't know, but 2 unreviewed candidates may be
    relevant" screen.

  Every answer AND every refusal is persisted with the pack
  fingerprint it was grounded on (the ADR-8 time-travel audit).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..bundles import bundle_fingerprint
from ..compilation import (
    CompilationPolicy,
    CompilationResult,
    ContextPackTask,
    compile_context_pack,
)
from ..evidence_ids import sha256_hex
from ..state_contracts import ProjectStateSnapshot
from .anchors import append_anchor, make_scope
from .store import MemoryStore, _epoch_or_none, canonical_json

#: ADR-7 ranking weights. Taste-plane weight is stubbed at 0.0 per
#: OPEN item O-2 ("stub at 0%, interface in place"); the key exists
#: so turning it on is a config change, and the weights are logged
#: in every BuildReceipt.
_PACK_RANK_WEIGHTS: dict[str, float] = {
    "text_match": 0.6,
    "decision_recency": 0.2,
    "taste": 0.0,
}

#: Crude deterministic token estimate (chars / 4) for the ADR-7
#: greedy budget selection. The reference runtime does not depend
#: on a tokenizer; the product substitutes tiktoken.
_CHARS_PER_TOKEN = 4

#: Default refusal threshold: the best selected entry must match
#: at least this fraction of the question's tokens (ADR-8 step 2).
DEFAULT_SUPPORT_THRESHOLD = 0.25

#: Identifier persisted with every answer (ADR-8 "model + prompt
#: version" -- the reference answerer is a deterministic template).
ANSWERER_VERSION = "deterministic-template-v1"

_EPOCH_ISO = "1970-01-01T00:00:00Z"
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_CITATION_RE = re.compile(r"\[F(\d+)\]")

#: Function words carry no support: without this filter, "what is
#: the ...?" matches every entry containing "is"/"the" and the
#: refusal threshold (ADR-8) becomes meaningless.
_STOPWORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "by", "do", "does", "for",
    "from", "has", "have", "how", "in", "is", "it", "its", "of", "on",
    "or", "that", "the", "to", "was", "were", "what", "when", "where",
    "which", "who", "why", "will", "with",
})


def _normalize_token(token: str) -> str:
    """Crude deterministic suffix strip so "deploys" supports a
    question about "deploy". Not a stemmer; just enough to keep
    keyword support honest."""
    for suffix in ("ing", "es", "ed", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            return token[: -len(suffix)]
    return token


def _query_tokens(text: str) -> list[str]:
    return [_normalize_token(t) for t in MemoryStore._tokens(text)
            if t not in _STOPWORDS]


def _support_tokens(text: str) -> set[str]:
    return {_normalize_token(t) for t in MemoryStore._tokens(text)}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedEntry:
    """One entry's position in the ADR-7 ranking."""

    entry_id: str
    total_score: float
    text_score: float
    recency_score: float


@dataclass(frozen=True)
class RuntimeBuildReceipt:
    """The runtime's BuildReceipt (ADR-7 step 5): query, filters,
    weights, as_of, ranking with scores, and the fingerprint --
    everything needed to replay or audit the selection. The
    library's own ``ContextPackBuildReceipt`` rides along inside
    the persisted envelope."""

    task: str
    as_of: str | None
    as_of_effective: str
    scope_subjects: tuple[str, ...]
    token_budget: int
    include_candidates: bool
    max_privacy: str | None
    weights: tuple[tuple[str, float], ...]
    ranking: tuple[RankedEntry, ...]
    selected_entry_ids: tuple[str, ...]
    candidate_ids_unverified: tuple[str, ...]
    state_snapshot_id: str | None
    pack_id: str | None
    fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "as_of": self.as_of,
            "as_of_effective": self.as_of_effective,
            "scope_subjects": list(self.scope_subjects),
            "token_budget": self.token_budget,
            "include_candidates": self.include_candidates,
            "max_privacy": self.max_privacy,
            "weights": {name: weight for name, weight in self.weights},
            "ranking": [asdict(r) for r in self.ranking],
            "selected_entry_ids": list(self.selected_entry_ids),
            "candidate_ids_unverified": list(self.candidate_ids_unverified),
            "state_snapshot_id": self.state_snapshot_id,
            "pack_id": self.pack_id,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class PackBuildResult:
    """Result of :func:`build_context_pack`.

    Attributes:
        compilation: The library's ``CompilationResult`` (pack +
            BuildReceipt + ValidationReport); ``None`` when the
            selection is empty (nothing to compile -- the refusal
            path).
        receipt: The runtime BuildReceipt (weights, ranking,
            filters, fingerprint).
        fingerprint: ``bundle_fingerprint`` over the full pack
            bundle (entries + evidence + state + candidates).
            Deterministic: same store state + same request =>
            byte-identical.
        selected_entry_ids: Ledger entries in the pack, rank order.
        candidate_ids_unverified: Pending candidates appended when
            ``include_candidates`` -- clearly segregated (they live
            in the pack's ``candidate_context``, never
            ``trusted_memory``).
        pack_id: Content-derived pack id (``None`` when empty).
        persisted: True when this call wrote the pack row (False
            for replays of an already-persisted pack and for
            ``persist=False`` builds).
        anchor_seq: The ADR-6 anchor covering the persisted write.
    """

    compilation: CompilationResult | None
    receipt: RuntimeBuildReceipt
    fingerprint: str
    selected_entry_ids: tuple[str, ...]
    candidate_ids_unverified: tuple[str, ...]
    pack_id: str | None
    persisted: bool
    anchor_seq: int | None


@dataclass(frozen=True)
class Citation:
    """One numbered fact block behind an answer."""

    index: int
    fact_id: str
    statement: str
    span_ids: tuple[str, ...]
    source_ids: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "fact_id": self.fact_id,
            "statement": self.statement,
            "span_ids": list(self.span_ids),
            "source_ids": list(self.source_ids),
        }


@dataclass(frozen=True)
class AnswerResult:
    """Result of :func:`answer` -- an answer or a structured refusal.

    Attributes:
        status: ``"answered"`` | ``"refused"``.
        answer_id: Content-derived id of the persisted answer row.
        question: The question asked.
        answer_text: The cited answer (``None`` when refused).
        reason: Refusal reason (``None`` when answered):
            ``not_in_trusted_memory`` | ``grounding_check_failed``.
        citations: The numbered fact blocks (empty when refused).
        related_candidate_ids: Unreviewed candidates that look
            relevant -- surfaced on refusal so the honest "review
            these?" screen is possible.
        pack: The pack the answer was grounded on.
        grounding_violations: Mechanical check findings (always
            empty for ``answered`` -- that is the contract).
    """

    status: str
    answer_id: str
    question: str
    answer_text: str | None
    reason: str | None
    citations: tuple[Citation, ...] = ()
    related_candidate_ids: tuple[str, ...] = ()
    pack: PackBuildResult | None = None
    grounding_violations: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# ADR-7: the deterministic, receipted pack build
# ---------------------------------------------------------------------------


def _as_of_effective(store: MemoryStore, as_of: str | None) -> str:
    """The deterministic as_of: the request's, else the latest
    decision time in the store, else the epoch. State-derived, so
    same store + same request stays deterministic."""
    if as_of is not None:
        return as_of
    best: tuple[float, str] | None = None
    for decision in store.list_decisions():
        decided_at = decision.get("decided_at")
        epoch = _epoch_or_none(decided_at)
        if epoch is None:
            continue
        key = (epoch, str(decided_at))
        if best is None or key > best:
            best = key
    return best[1] if best is not None else _EPOCH_ISO


def _rank_entries(
    store: MemoryStore,
    entries: Sequence[Mapping[str, Any]],
    task: str,
) -> list[RankedEntry]:
    decisions_by_id = {str(d.get("id") or ""): d
                       for d in store.list_decisions()}
    epochs: list[float] = []
    for entry in entries:
        decision = decisions_by_id.get(
            str(entry.get("reducer_decision_id") or ""), {})
        epochs.append(_epoch_or_none(decision.get("decided_at")) or 0.0)
    max_epoch = max(epochs, default=0.0)
    query_tokens = _query_tokens(task)
    ranked: list[RankedEntry] = []
    for entry, epoch in zip(entries, epochs):
        text_tokens = _support_tokens(MemoryStore._entry_text(dict(entry)))
        text_score = (sum(1 for t in query_tokens if t in text_tokens)
                      / len(query_tokens)) if query_tokens else 0.0
        recency = (epoch / max_epoch) if max_epoch > 0 else 0.0
        total = (_PACK_RANK_WEIGHTS["text_match"] * text_score
                 + _PACK_RANK_WEIGHTS["decision_recency"] * recency
                 + _PACK_RANK_WEIGHTS["taste"] * 0.0)
        ranked.append(RankedEntry(
            entry_id=str(entry.get("id") or ""), total_score=total,
            text_score=text_score, recency_score=recency))
    ranked.sort(key=lambda r: (-r.total_score, r.entry_id))
    return ranked


def _synthesize_state_snapshot(
    store: MemoryStore,
    *,
    task: str,
    as_of_effective: str,
    selected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """A deterministic ProjectStateSnapshot over the selection.

    Deliberate adaptation: the library's pack compiler requires a
    state reference, and the state *reducer* is product scope --
    so the runtime derives the state plane content-addressed from
    the selected entries (same selection => same snapshot id).
    The synthetic ``reducer_decision_id`` is hash-derived and
    prefixed per the contracts; a real state reducer replaces
    this in the product repo.
    """
    span_ids: set[str] = set()
    source_ids: set[str] = set()
    fact_ids: list[str] = []
    pref_ids: list[str] = []
    dec_ids: list[str] = []
    for entry in selected:
        span_ids.update(str(s) for s in entry.get("evidence_span_ids") or [])
        source_ids.update(str(s)
                          for s in entry.get("source_record_ids") or [])
        entry_id = str(entry.get("id") or "")
        if entry_id.startswith("fact_"):
            fact_ids.append(entry_id)
        elif entry_id.startswith("pref_"):
            pref_ids.append(entry_id)
        elif entry_id.startswith("dec_"):
            dec_ids.append(entry_id)
    project_id = f"runtime:{store.tenant_id}"
    synthetic_decision = "redstate_" + sha256_hex(canonical_json({
        "project_id": project_id,
        "as_of": as_of_effective,
        "selection": sorted(fact_ids + pref_ids + dec_ids),
    }))[:24]
    data: dict[str, Any] = {
        "id": "projstate_pending",
        "schema_version": "1.0.0",
        "state_type": "project_state",
        "status": "active",
        "as_of": as_of_effective,
        "summary": f"Deterministic runtime grounding state for task "
                   f"{task!r} as of {as_of_effective}.",
        "active_fact_ids": sorted(fact_ids),
        "active_preference_ids": sorted(pref_ids),
        "active_decision_ids": sorted(dec_ids),
        "active_taste_card_ids": [],
        "source_record_ids": sorted(source_ids),
        "episode_record_ids": [],
        "evidence_span_ids": sorted(span_ids),
        "reducer_decision_id": synthetic_decision,
        "valid_from": None,
        "valid_until": None,
        "stale_after": None,
        "created_at": as_of_effective,
        "updated_at": as_of_effective,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {"synthesized_by": "runtime-grounding",
                     "task": task},
        "project_id": project_id,
        "project_name": f"Runtime tenant {store.tenant_id}",
        "project_status": "active",
        "current_objective": f"Ground the task: {task}"[:500] or "Ground",
        "current_strategy": "Answer only from reducer-approved memory; "
                            "cite or refuse.",
        "current_priorities": [],
        "active_blockers": [],
        "open_questions": [],
        "next_actions": [],
        "candidate_task_ids": [],
    }
    probe = ProjectStateSnapshot(**data)
    data["id"] = probe.expected_id()
    snapshot = ProjectStateSnapshot.from_dict(data)
    return asdict(snapshot)


def build_context_pack(
    store: MemoryStore,
    *,
    task: str,
    scope_subjects: Iterable[str] = (),
    as_of: str | None = None,
    token_budget: int = 4000,
    include_candidates: bool = False,
    max_privacy: str | None = None,
    persist: bool = True,
    actor: str = "runtime-grounding",
    created_at: str | None = None,
) -> PackBuildResult:
    """Build (and persist) a deterministic, receipted ContextPack.

    See the module docstring for the ADR-7 algorithm. Determinism:
    same store state + same arguments => same selection => same
    pack id => byte-identical ``fingerprint``.

    Args:
        store: The store to read (and persist receipts into).
        task: Free-text task / question driving the ranking.
        scope_subjects: When given, only entries whose ``subject``
            is in this set are considered.
        as_of: Temporal filter (ADR-3 time travel); defaults to
            the latest decision time in the store.
        token_budget: Greedy selection budget (chars/4 proxy).
        include_candidates: Append pending candidates, segregated
            in ``candidate_context`` -- never interleaved with
            trusted facts.
        max_privacy: Privacy clearance ceiling (ADR-13).
        persist: Store the pack + receipts (content-addressed,
            anchored). Replays of an existing pack id never
            rewrite.
        actor: Recorded on the audit anchor.
        created_at: Bookkeeping timestamp for the persisted rows.
    """
    subjects = tuple(sorted(str(s) for s in scope_subjects))
    entries = store.active_entries(as_of=as_of, max_privacy=max_privacy)
    if subjects:
        allowed = set(subjects)
        entries = [e for e in entries
                   if str(e.get("subject") or "") in allowed]
    as_of_eff = _as_of_effective(store, as_of)
    ranking = _rank_entries(store, entries, task)
    entries_by_id = {str(e.get("id") or ""): e for e in entries}

    selected: list[dict[str, Any]] = []
    selected_ids: list[str] = []
    budget_left = max(token_budget, 0)
    for ranked in ranking:
        entry = entries_by_id[ranked.entry_id]
        cost = max(len(MemoryStore._entry_text(entry)) // _CHARS_PER_TOKEN,
                   1)
        if cost > budget_left:
            continue
        budget_left -= cost
        selected.append(entry)
        selected_ids.append(ranked.entry_id)

    candidate_ids: list[str] = []
    if not selected:
        receipt = RuntimeBuildReceipt(
            task=task, as_of=as_of, as_of_effective=as_of_eff,
            scope_subjects=subjects, token_budget=token_budget,
            include_candidates=include_candidates, max_privacy=max_privacy,
            weights=tuple(sorted(_PACK_RANK_WEIGHTS.items())),
            ranking=tuple(ranking), selected_entry_ids=(),
            candidate_ids_unverified=(), state_snapshot_id=None,
            pack_id=None, fingerprint=bundle_fingerprint([]))
        return PackBuildResult(
            compilation=None, receipt=receipt,
            fingerprint=receipt.fingerprint, selected_entry_ids=(),
            candidate_ids_unverified=(), pack_id=None, persisted=False,
            anchor_seq=None)

    # Evidence closure for the pack bundle: spans + sources of the
    # selected entries (the compiler validates source coverage).
    span_ids: set[str] = set()
    source_ids: set[str] = set()
    for entry in selected:
        span_ids.update(str(s) for s in entry.get("evidence_span_ids") or [])
        source_ids.update(str(s)
                          for s in entry.get("source_record_ids") or [])
    snapshot = _synthesize_state_snapshot(
        store, task=task, as_of_effective=as_of_eff, selected=selected)
    bundle: list[dict[str, Any]] = [snapshot]
    bundle.extend(selected)
    for span_id in sorted(span_ids):
        span = store.get_span(span_id)
        if span is not None:
            bundle.append(span)
    for source_id in sorted(source_ids):
        source = store.get_source(source_id)
        if source is not None:
            bundle.append(source)

    if include_candidates:
        for candidate in store.list_candidates(status="candidate"):
            bundle.append(candidate)
            candidate_ids.append(str(candidate.get("id") or ""))
            for span_id in [str(s) for s
                            in candidate.get("evidence_span_ids") or []]:
                if span_id in span_ids:
                    continue
                span = store.get_span(span_id)
                if span is None:
                    continue
                bundle.append(span)
                span_ids.add(span_id)
                source_id = str(span.get("source_id") or "")
                if source_id and source_id not in source_ids:
                    source = store.get_source(source_id)
                    if source is not None:
                        bundle.append(source)
                        source_ids.add(source_id)

    pack_task = ContextPackTask(
        task_id="task_" + sha256_hex(canonical_json({
            "task": task, "as_of": as_of, "scope_subjects": list(subjects),
            "token_budget": token_budget,
            "include_candidates": include_candidates,
            "max_privacy": max_privacy}))[:16],
        task_title=task or "(unspecified)",
        task_type="research",
        task_summary=task or "(unspecified)",
        project_id=f"runtime:{store.tenant_id}",
        risk_class="low",
        sensitivity=max_privacy or "internal",
    )
    compilation = compile_context_pack(
        bundle,
        task=pack_task,
        policy=CompilationPolicy(max_records=0,
                                 require_source_coverage=True),
    )
    fingerprint = bundle_fingerprint(bundle)
    pack_id = compilation.context_pack.id
    receipt = RuntimeBuildReceipt(
        task=task, as_of=as_of, as_of_effective=as_of_eff,
        scope_subjects=subjects, token_budget=token_budget,
        include_candidates=include_candidates, max_privacy=max_privacy,
        weights=tuple(sorted(_PACK_RANK_WEIGHTS.items())),
        ranking=tuple(ranking),
        selected_entry_ids=tuple(selected_ids),
        candidate_ids_unverified=tuple(candidate_ids),
        state_snapshot_id=str(snapshot["id"]), pack_id=pack_id,
        fingerprint=fingerprint)

    persisted = False
    anchor_seq: int | None = None
    if persist and store.get_context_pack(pack_id) is None:
        envelope = {
            "id": pack_id,
            "task": task,
            "as_of": as_of,
            "fingerprint": fingerprint,
            "context_pack": asdict(compilation.context_pack),
            "build_receipt": asdict(compilation.build_receipt),
            "validation_report": asdict(compilation.validation_report),
            "runtime_receipt": receipt.to_dict(),
        }
        stamp = created_at or as_of_eff
        snapshot_id = str(snapshot["id"])
        snapshot_is_new = store.get_state_snapshot(snapshot_id) is None
        with store._txn() as conn:
            store._arm_guard(conn)
            scope_ids = [pack_id]
            if snapshot_is_new:
                store._insert_state_snapshot(conn, snapshot, stamp)
                scope_ids.append(snapshot_id)
            store._insert_context_pack(conn, pack_id, task, as_of,
                                       fingerprint, envelope, stamp)
            anchor = append_anchor(store, conn,
                                   scope=make_scope(scope_ids),
                                   kind="write", actor=actor,
                                   created_at=stamp)
            store._disarm_guard(conn)
        persisted = True
        anchor_seq = anchor.seq

    return PackBuildResult(
        compilation=compilation, receipt=receipt, fingerprint=fingerprint,
        selected_entry_ids=tuple(selected_ids),
        candidate_ids_unverified=tuple(candidate_ids), pack_id=pack_id,
        persisted=persisted, anchor_seq=anchor_seq)


# ---------------------------------------------------------------------------
# ADR-8: cite-or-refuse
# ---------------------------------------------------------------------------


def _statement(entry: Mapping[str, Any]) -> str:
    for key in ("fact_text", "preference_text", "decision_text"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    subject = str(entry.get("subject") or "")
    predicate = str(entry.get("predicate") or "")
    obj = str(entry.get("object") or "")
    return f"{subject} {predicate} {obj}".strip()


def _cited_block(statement: str, index: int) -> str:
    """Render one fact as citation-carrying sentences: every
    sentence inside the statement gets the ``[Fi]`` marker, so the
    grounded-or-refused invariant holds by construction regardless
    of the fact text's internal punctuation."""
    pieces = _SENTENCE_SPLIT_RE.split(statement.strip())
    out: list[str] = []
    for piece in pieces:
        piece = piece.strip()
        if not piece:
            continue
        while piece and piece[-1] in ".!?":
            piece = piece[:-1].rstrip()
        if piece:
            out.append(f"{piece} [F{index}].")
    return " ".join(out)


def verify_grounding(answer_text: str,
                     fact_count: int) -> tuple[str, ...]:
    """The mechanical cite-or-refuse check (ADR-8 step 4).

    Every sentence must carry at least one ``[Fi]`` citation, and
    every citation must resolve to one of the pack's numbered
    facts. Returns the violations (empty = grounded). Heuristic
    sentence split, exactly as the ADR prescribes -- log misses,
    do not trust prose.
    """
    violations: list[str] = []
    text = answer_text.strip()
    if not text:
        return ("answer text is empty",)
    for sentence in _SENTENCE_SPLIT_RE.split(text):
        sentence = sentence.strip()
        if not sentence:
            continue
        cited = _CITATION_RE.findall(sentence)
        if not cited:
            violations.append(f"uncited factual sentence: {sentence!r}")
            continue
        for number in cited:
            index = int(number)
            if index < 1 or index > fact_count:
                violations.append(
                    f"citation [F{number}] does not resolve to a pack "
                    f"fact (pack has {fact_count})")
    return tuple(violations)


def related_candidates(store: MemoryStore, question: str, *,
                       limit: int = 5) -> tuple[str, ...]:
    """Pending candidates that look relevant to a question --
    surfaced on refusal (ADR-8: "2 unreviewed candidates may be
    relevant -- review?"). Deterministic keyword overlap; rejected
    candidates are excluded (they were reviewed and refused)."""
    query_tokens = _query_tokens(question)
    if not query_tokens:
        return ()
    rejected = set(store.rejected_candidate_ids())
    scored: list[tuple[float, str]] = []
    for candidate in store.list_candidates(status="candidate"):
        candidate_id = str(candidate.get("id") or "")
        if candidate_id in rejected:
            continue
        if store.promoting_decision_for(candidate_id) is not None:
            continue
        parts: list[str] = []
        for key in ("natural_language_summary", "claim_text",
                    "preference_text", "decision_text", "taste_text",
                    "subject", "predicate", "object"):
            value = candidate.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
        text_tokens = _support_tokens(" ".join(parts))
        matched = sum(1 for t in query_tokens if t in text_tokens)
        if matched:
            scored.append((matched / len(query_tokens), candidate_id))
    scored.sort(key=lambda pair: (-pair[0], pair[1]))
    return tuple(candidate_id for _score, candidate_id
                 in scored[:max(limit, 0)])


def answer(
    store: MemoryStore,
    *,
    question: str,
    as_of: str | None = None,
    token_budget: int = 4000,
    support_threshold: float = DEFAULT_SUPPORT_THRESHOLD,
    max_facts: int = 8,
    max_privacy: str | None = None,
    persist: bool = True,
    actor: str = "runtime-grounding",
    created_at: str | None = None,
) -> AnswerResult:
    """Answer a question from trusted memory, or refuse (ADR-8).

    1. Build a pack from the question (ADR-7).
    2. If nothing is selected, or the best selected entry's
       keyword support is below ``support_threshold``: structured
       refusal with related unreviewed candidate ids.
    3. Otherwise compose the deterministic template answer over
       the top ``max_facts`` facts -- every sentence cited.
    4. Post-validate mechanically (:func:`verify_grounding`); a
       failed check degrades to a refusal, never to an uncited
       answer.
    5. Persist the answer (or refusal) with the pack fingerprint.
    """
    pack = build_context_pack(
        store, task=question, as_of=as_of, token_budget=token_budget,
        max_privacy=max_privacy, persist=persist, actor=actor,
        created_at=created_at)
    text_score_by_id = {r.entry_id: r.text_score
                        for r in pack.receipt.ranking}
    top_support = max(
        (text_score_by_id.get(entry_id, 0.0)
         for entry_id in pack.selected_entry_ids),
        default=0.0)

    def _persist_and_build(status: str, answer_text: str | None,
                           reason: str | None,
                           citations: tuple[Citation, ...],
                           related: tuple[str, ...],
                           violations: tuple[str, ...]) -> AnswerResult:
        answer_id = "ans_" + sha256_hex(canonical_json({
            "question": question,
            "pack_id": pack.pack_id,
            "fingerprint": pack.fingerprint,
            "status": status,
            "answer_text": answer_text,
            "reason": reason,
            "as_of": pack.receipt.as_of_effective,
        }))[:24]
        payload = {
            "id": answer_id,
            "status": status,
            "question": question,
            "answer_text": answer_text,
            "reason": reason,
            "citations": [c.to_dict() for c in citations],
            "related_candidate_ids": list(related),
            "pack_id": pack.pack_id,
            "pack_fingerprint": pack.fingerprint,
            "answerer_version": ANSWERER_VERSION,
            "support_threshold": support_threshold,
            "top_support": top_support,
            "as_of_effective": pack.receipt.as_of_effective,
            "grounding_violations": list(violations),
        }
        if persist and store.get_answer(answer_id) is None:
            stamp = created_at or pack.receipt.as_of_effective
            with store._txn() as conn:
                store._arm_guard(conn)
                store._insert_answer(conn, answer_id, status, question,
                                     pack.pack_id, pack.fingerprint,
                                     payload, stamp)
                append_anchor(store, conn, scope=make_scope([answer_id]),
                              kind="write", actor=actor, created_at=stamp)
                store._disarm_guard(conn)
        return AnswerResult(
            status=status, answer_id=answer_id, question=question,
            answer_text=answer_text, reason=reason, citations=citations,
            related_candidate_ids=related, pack=pack,
            grounding_violations=violations)

    if not pack.selected_entry_ids or top_support < support_threshold:
        related = related_candidates(store, question)
        return _persist_and_build("refused", None, "not_in_trusted_memory",
                                  (), related, ())

    # Cite only entries that actually support the question
    # (text_score > 0); zero-support entries are pack context, not
    # answer material.
    fact_ids = [entry_id for entry_id in pack.selected_entry_ids
                if text_score_by_id.get(entry_id, 0.0) > 0.0]
    fact_ids = fact_ids[:max(max_facts, 1)]
    citations: list[Citation] = []
    blocks: list[str] = []
    for index, fact_id in enumerate(fact_ids, start=1):
        entry = store.get_ledger_entry(fact_id) or {}
        statement = _statement(entry)
        citations.append(Citation(
            index=index, fact_id=fact_id, statement=statement,
            span_ids=tuple(str(s)
                           for s in entry.get("evidence_span_ids") or []),
            source_ids=tuple(str(s)
                             for s in entry.get("source_record_ids") or []),
        ))
        blocks.append(_cited_block(statement, index))
    answer_text = " ".join(block for block in blocks if block)
    violations = verify_grounding(answer_text, len(fact_ids))
    if violations:
        # ADR-8 step 4: degrade to refusal-with-partials; never
        # emit an uncited answer.
        related = related_candidates(store, question)
        return _persist_and_build("refused", None,
                                  "grounding_check_failed", (), related,
                                  violations)
    return _persist_and_build("answered", answer_text, None,
                              tuple(citations), (), ())


__all__ = [
    "DEFAULT_SUPPORT_THRESHOLD",
    "ANSWERER_VERSION",
    "RankedEntry",
    "RuntimeBuildReceipt",
    "PackBuildResult",
    "Citation",
    "AnswerResult",
    "build_context_pack",
    "verify_grounding",
    "related_candidates",
    "answer",
]
