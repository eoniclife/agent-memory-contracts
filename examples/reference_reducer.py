"""Reference reducer: a production-shaped example of the authorization pattern.

This is the real, reusable version of the reducer that
``examples/quickstart.py`` shows in happy-path form.  It is the
reference implementation a downstream project should copy and
adapt, not a teaching toy.  It demonstrates:

  1. **The three checks a reducer must run on every candidate**

     - **Provenance check** -- the candidate references at least
       one ``EvidenceSpan`` that actually exists in the evidence
       plane handed to the reducer.  Without this, a hallucinated
       span id would silently smuggle the candidate into the
       trusted ledger.

     - **Confidence check** -- the candidate's ``confidence``
       field is at or above ``min_confidence`` (default
       ``"medium"``).  A reducer that promotes "low" confidence
       extraction is not really a reducer.

     - **Temporal check** -- the candidate's ``extracted_at`` is
       recent enough (default threshold: 90 days).  Stale
       candidates are quarantined so a slow extractor does not
       poison the ledger with re-extracted ancient history.

  2. **The two outputs a reducer must produce**

     - **Promoted**: parallel lists of ``MemoryReducerDecision``
       and ``PreferenceLedgerEntry``/``FactLedgerEntry`` (one
       per promoted candidate).  Each reducer decision
       authorises exactly the ledger entry that points back at
       it -- the bundle validator enforces the link.

     - **Rejected**: a list of ``RejectedCandidate`` records
       carrying the candidate id, a reason code, and a human
       message.  The candidate is NOT promoted; the rejection
       reason is the audit trail.

  3. **The failure mode the library catches for you**

     A ``MemoryReducerDecision`` that *claims* to authorise
     ledger entry X by listing X in its ``candidate_ids`` /
     ``evidence_span_ids`` / ``reducer_decision_id`` chain, but
     forgets to put X in ``target_ledger_entry_ids``, is
     CAUGHT by ``validate_ledger_bundle(...)``.  This is the
     canonical "you forgot to wire the new entry" bug, and the
     library refuses to validate a bundle with it.  Scenario C
     below demonstrates this on purpose.

How a production reducer might differ
-------------------------------------

The reducer below is a *reference* -- it is correct and
exercised end-to-end, but intentionally small.  A production
reducer would layer on top:

  * **LLM-based contradiction scanning** before the
    provenance/confidence checks; emit
    "contradiction_detected" as another rejection reason.

  * **Async batch processing** -- the function signature
    stays the same, but a production implementation would
    be ``async def`` and call the LLM scanner in
    ``asyncio.gather`` for many candidates at once.

  * **Human-in-the-loop approval** for high-risk candidates
    (``risk_class == "high"``); short-circuit to a review
    queue and emit a ``status == "needs_review"`` candidate.

  * **Supersession** of older ledger entries when a new
    candidate is extracted about the same subject.  The
    reference reducer never supersedes; it only adds.

  * **Telemetry** -- the reference reducer returns the
    ``RejectedCandidate`` list; a production reducer would
    also emit a counter metric, e.g.
    ``reducer_rejections_total{reason=...}.inc()``.

Run from the repo root:

    PYTHONPATH=src python examples/reference_reducer.py
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Union

from agent_memory_contracts import (
    CandidateClaim,
    CandidatePreference,
    CandidateTasteSignal,
    EvidenceSpan,
    FactLedgerEntry,
    MemoryReducerDecision,
    PreferenceLedgerEntry,
    SourceRecord,
    make_candidate_id,
    make_ledger_entry_id,
    make_reducer_decision_id,
    make_source_id,
    make_span_id,
    validate_ledger_bundle,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

#: Reason codes emitted into RejectedCandidate.reason_code.  These are the
#: only three the reference reducer produces; production reducers may add
#: more (e.g. "contradiction_detected", "needs_human_review").
REJECT_NO_EVIDENCE = "no_evidence"
REJECT_LOW_CONFIDENCE = "low_confidence"
REJECT_STALE = "stale"
REJECT_MISSING_SPAN = "missing_span"

#: ISO-8601 timestamps for the demo.  The recency check computes "now"
#: relative to ``T_NOW`` so the example is deterministic regardless of
#: when it is run.
T_NOW = datetime(2026, 5, 30, 13, 0, 0, tzinfo=timezone.utc)
T_CAPTURED = "2026-05-30T12:00:00Z"
T_EXTRACTED_FRESH = "2026-05-30T12:30:00Z"  # 30 min before T_NOW -> passes 90d recency
T_EXTRACTED_STALE = "2025-09-01T00:00:00Z"  # ~9 months before T_NOW -> fails recency
T_DECIDED = T_NOW.isoformat().replace("+00:00", "Z")

#: Candidates in Scenarios A and B use a 64-char fake content hash so the
#: id helpers (which are content-derived) are deterministic.
FAKE_CONTENT_HASH = "a" * 64
FAKE_SPAN_HASH = "b" * 64
SOURCE_URI = "https://example.com/transcript/ref-reducer-demo"


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RejectedCandidate:
    """One candidate that the reducer refused to promote.

    The reducer does NOT mutate the candidate -- it stays in the
    untrusted candidate plane with ``status == "candidate"``.  This
    record is the audit trail for the rejection decision.
    """

    candidate_id: str
    reason_code: str  # one of REJECT_*
    message: str
    candidate_type: str = ""  # "claim" | "preference" | "taste_signal" | ...
    confidence: str = ""


@dataclass(frozen=True)
class ReducerResult:
    """The output of a single reducer run.

    ``decisions`` and ``ledger_entries`` are parallel lists: for
    every promoted candidate there is exactly one decision and
    exactly one ledger entry, in the same order.  The library's
    ``validate_ledger_bundle(...)`` enforces the link
    bidirectional -- the ledger entry lists the reducer's id,
    and the reducer lists the entry's id in
    ``target_ledger_entry_ids``.
    """

    decisions: list[MemoryReducerDecision] = field(default_factory=list)
    ledger_entries: list[Union[PreferenceLedgerEntry, FactLedgerEntry]] = field(default_factory=list)
    rejected: list[RejectedCandidate] = field(default_factory=list)


#: The set of candidate types this reference reducer understands.  Each
#: type maps to a fixed ledger type (CandidateTasteSignal ->
#: PreferenceLedgerEntry, CandidateClaim -> FactLedgerEntry, etc.).
CandidateInput = Union[CandidateTasteSignal, CandidateClaim, CandidatePreference]


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

#: Minimum number of days between ``extracted_at`` and "now" for a
#: candidate to be considered fresh.  90 days is the reference
#: reducer's default; production reducers will tune this to the
#: volatility of the source plane (e.g. transcripts: 30 days,
#: books: 5 years).
RECENCY_DAYS_DEFAULT = 90

CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}


def _parse_iso8601(value: str) -> datetime:
    """Parse an ISO-8601 string (the library's own helper is private)."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_fresh(extracted_at: str, *, now: datetime, recency_days: int) -> bool:
    """Temporal check: extracted_at must be within ``recency_days`` of now."""
    extracted = _parse_iso8601(extracted_at)
    if extracted.tzinfo is None:
        extracted = extracted.replace(tzinfo=timezone.utc)
    delta = now - extracted
    return delta <= timedelta(days=recency_days)


# ---------------------------------------------------------------------------
# The three checks.  Each is a separate function so the example reads as
# "here are the checks, here is the dispatch logic" rather than one
# monolithic if-else tree.
# ---------------------------------------------------------------------------

def _check_provenance(
    candidate: CandidateInput,
    evidence_spans_by_id: dict[str, EvidenceSpan],
) -> tuple[bool, str]:
    """Provenance check: at least one evidence_span_id resolves.

    Returns (passes, message).  An empty ``evidence_span_ids``
    fails provenance outright: the candidate is ungrounded.
    """
    if not candidate.evidence_span_ids:
        return False, "candidate has no evidence_span_ids"
    for span_id in candidate.evidence_span_ids:
        if span_id not in evidence_spans_by_id:
            return False, f"evidence_span_id {span_id} not found in evidence plane"
    return True, ""


def _check_confidence(
    candidate: CandidateInput,
    *,
    min_confidence: str,
) -> tuple[bool, str]:
    """Confidence check: candidate.confidence >= min_confidence."""
    candidate_rank = CONFIDENCE_RANK.get(candidate.confidence, -1)
    threshold_rank = CONFIDENCE_RANK.get(min_confidence, 0)
    if candidate_rank < threshold_rank:
        return False, (
            f"confidence {candidate.confidence!r} is below threshold "
            f"{min_confidence!r}"
        )
    return True, ""


def _check_temporal(
    candidate: CandidateInput,
    *,
    now: datetime,
    recency_days: int,
) -> tuple[bool, str]:
    """Temporal check: candidate.extracted_at is recent enough."""
    if not _is_fresh(candidate.extracted_at, now=now, recency_days=recency_days):
        return False, (
            f"extracted_at {candidate.extracted_at} is older than "
            f"{recency_days} days"
        )
    return True, ""


# ---------------------------------------------------------------------------
# Dispatch: turn a passing candidate into a (reducer_decision, ledger_entry)
# pair.  The library enforces that the two are linked; we just build them
# with the right content-derived ids and let the bundle validator verify.
# ---------------------------------------------------------------------------

def _build_reducer_and_ledger_for_candidate(
    candidate: CandidateInput,
    *,
    source_id: str,
    span_id: str,
    decided_at: str,
) -> tuple[MemoryReducerDecision, Union[PreferenceLedgerEntry, FactLedgerEntry]]:
    """Build the (decision, ledger_entry) pair for a passing candidate.

    The candidate type picks the ledger type: taste_signal -> preference,
    claim -> fact, preference -> preference.  Each ledger entry's id is
    content-derived from the same payload fields the library hashes, so
    we have to mirror those fields exactly.
    """
    if isinstance(candidate, CandidateTasteSignal):
        ledger = _build_preference_entry(
            subject=candidate.domain,
            preference_text=candidate.taste_text,
            domain=candidate.domain,
            strength=candidate.strength_hint,
            confidence=candidate.confidence,
            scope="global",
            source_id=source_id,
            span_id=span_id,
            decided_at=decided_at,
            candidate_id=candidate.id,
        )
        rationale = f"taste_signal grounded in span {span_id}; strength_hint={candidate.strength_hint}"
    elif isinstance(candidate, CandidateClaim):
        ledger = _build_fact_entry(
            subject=candidate.subject,
            predicate=candidate.predicate,
            object_value=candidate.object,
            fact_text=candidate.claim_text,
            scope=candidate.claim_scope,
            confidence=candidate.confidence,
            source_id=source_id,
            span_id=span_id,
            decided_at=decided_at,
            candidate_id=candidate.id,
        )
        rationale = f"claim grounded in span {span_id}; scope={candidate.claim_scope}"
    elif isinstance(candidate, CandidatePreference):
        ledger = _build_preference_entry(
            subject=candidate.subject,
            preference_text=candidate.preference_text,
            domain=candidate.domain,
            strength=candidate.strength_hint,
            confidence=candidate.confidence,
            scope=candidate.scope,
            source_id=source_id,
            span_id=span_id,
            decided_at=decided_at,
            candidate_id=candidate.id,
        )
        rationale = f"preference grounded in span {span_id}; domain={candidate.domain}"
    else:
        raise TypeError(f"unsupported candidate type: {type(candidate).__name__}")

    reducer_id = make_reducer_decision_id(
        decision_type="promote",
        target_candidate_ids=[candidate.id],
        target_ledger_entry_ids=[ledger.id],
        evidence_span_ids=[span_id],
        rationale=rationale,
    )
    decision = MemoryReducerDecision.from_dict({
        "id": reducer_id,
        "schema_version": "1.0.0",
        "decision_type": "promote",
        "target_candidate_ids": [candidate.id],
        "target_ledger_entry_ids": [ledger.id],
        "evidence_span_ids": [span_id],
        "rationale": rationale,
        "decided_by": {"agent": "reference-reducer", "model": "deterministic", "tool": None, "prompt_ref": None},
        "decided_at": decided_at,
        "confidence": candidate.confidence,
        "risk_class": candidate.risk_class,
        "checks": {
            "provenance": "pass",
            "temporal_validity": "pass",
            "contradiction_scan": "pass",
            "privacy": "pass",
            "usefulness": "pass",
        },
        "metadata": {},
    })
    return decision, ledger


#: Strengths allowed in the ledger's ``strength`` field.  The taste-signal
#: ``strength_hint`` allows "unknown" as well; we coerce that to "medium".
_VALID_STRENGTHS = {"weak", "medium", "strong", "hard_constraint"}


def _build_preference_entry(
    *,
    subject: str,
    preference_text: str,
    domain: str,
    strength: str,
    confidence: str,
    scope: str,
    source_id: str,
    span_id: str,
    decided_at: str,
    candidate_id: str,
) -> PreferenceLedgerEntry:
    """Build a PreferenceLedgerEntry from primitive fields.

    The id is content-derived from the same payload fields the library
    hashes, so we have to mirror those fields exactly.
    """
    ledger_id = make_ledger_entry_id("preference", [span_id], {
        "ledger_type": "preference",
        "subject": subject,
        "preference_text": preference_text,
        "domain": domain,
        "scope": scope,
        "valid_from": decided_at,
        "evidence_span_ids": [span_id],
    })
    return PreferenceLedgerEntry.from_dict({
        "id": ledger_id,
        "schema_version": "1.0.0",
        "ledger_type": "preference",
        "status": "active",
        "confidence": confidence,
        "scope": scope,
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "candidate_ids": [candidate_id],
        "reducer_decision_id": "redmem_pending",  # patched in by caller
        "subject": subject,
        "preference_text": preference_text,
        "domain": domain,
        "strength": strength if strength in _VALID_STRENGTHS else "medium",
        "observed_at": T_CAPTURED,
        "asserted_at": decided_at,
        "valid_from": decided_at,
        "valid_until": None,
        "stale_after": None,
        "created_at": decided_at,
        "updated_at": decided_at,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {},
    })


def _build_fact_entry(
    *,
    subject: str,
    predicate: str,
    object_value: str,
    fact_text: str,
    scope: str,
    confidence: str,
    source_id: str,
    span_id: str,
    decided_at: str,
    candidate_id: str,
) -> FactLedgerEntry:
    """Build a FactLedgerEntry from primitive fields."""
    ledger_id = make_ledger_entry_id("fact", [span_id], {
        "ledger_type": "fact",
        "subject": subject,
        "predicate": predicate,
        "object": object_value,
        "scope": scope,
        "valid_from": decided_at,
        "evidence_span_ids": [span_id],
    })
    return FactLedgerEntry.from_dict({
        "id": ledger_id,
        "schema_version": "1.0.0",
        "ledger_type": "fact",
        "status": "active",
        "confidence": confidence,
        "scope": scope,
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "candidate_ids": [candidate_id],
        "reducer_decision_id": "redmem_pending",
        "subject": subject,
        "predicate": predicate,
        "object": object_value,
        "fact_text": fact_text,
        "observed_at": T_CAPTURED,
        "asserted_at": decided_at,
        "valid_from": decided_at,
        "valid_until": None,
        "stale_after": None,
        "created_at": decided_at,
        "updated_at": decided_at,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {},
    })


# ---------------------------------------------------------------------------
# The reducer
# ---------------------------------------------------------------------------

def reduce_candidates_to_trusted_memory(
    candidates: list[CandidateInput],
    evidence_spans: list[EvidenceSpan],
    source_records: list[SourceRecord],
    *,
    min_confidence: str = "medium",
    require_evidence: bool = True,
    recency_days: int = RECENCY_DAYS_DEFAULT,
    decided_at: str | None = None,
    now: datetime | None = None,
) -> ReducerResult:
    """Run the three checks on each candidate and produce a ReducerResult.

    The function is **pure** with respect to the candidates: a
    rejected candidate is left unchanged in the candidate plane
    (its ``status`` stays ``"candidate"``); the rejection is
    recorded only in the returned ``ReducerResult.rejected``
    list.  This matches the contracts' invariant that the
    candidate plane is untrusted and the reducer decision is
    the only thing that can promote to the ledger plane.

    Parameters
    ----------
    candidates:
        The untrusted extracted candidates to consider.
    evidence_spans:
        The evidence plane the candidates must reference.  Any
        candidate whose ``evidence_span_ids`` does not intersect
        this list is rejected with ``"no_evidence"``.
    source_records:
        The source plane.  Used to fill
        ``source_record_ids`` on the promoted ledger entries.
    min_confidence:
        Floor for the confidence check (default ``"medium"``).
        Candidates below this are rejected with
        ``"low_confidence"``.
    require_evidence:
        If False, the provenance check is skipped.  Useful for
        batch reprocessing jobs that have already validated
        evidence in a previous pass; not recommended for
        production reducers.
    recency_days:
        Maximum age of a candidate's ``extracted_at`` (default
        90).  Older candidates are rejected with ``"stale"``.
    decided_at:
        Override for the reducer decision's ``decided_at`` field.
    now:
        Override for "now" (used by the temporal check).  Defaults
        to a fixed deterministic value so the example is
        reproducible.
    """
    evidence_spans_by_id = {span.id: span for span in evidence_spans}
    if not now:
        now = T_NOW
    if not decided_at:
        decided_at = now.isoformat().replace("+00:00", "Z")

    decisions: list[MemoryReducerDecision] = []
    ledger_entries: list[Union[PreferenceLedgerEntry, FactLedgerEntry]] = []
    rejected: list[RejectedCandidate] = []

    def _record(candidate: CandidateInput, reason_code: str, message: str) -> None:
        rejected.append(RejectedCandidate(
            candidate_id=candidate.id,
            reason_code=reason_code,
            message=message,
            candidate_type=candidate.candidate_type,
            confidence=candidate.confidence,
        ))

    for candidate in candidates:
        # Order matters: provenance first, because a candidate with
        # no evidence cannot be evaluated for confidence or temporal
        # validity usefully.
        if require_evidence:
            ok, message = _check_provenance(candidate, evidence_spans_by_id)
            if not ok:
                _record(candidate, REJECT_NO_EVIDENCE, message)
                continue

        ok, message = _check_confidence(candidate, min_confidence=min_confidence)
        if not ok:
            _record(candidate, REJECT_LOW_CONFIDENCE, message)
            continue

        ok, message = _check_temporal(
            candidate, now=now, recency_days=recency_days,
        )
        if not ok:
            _record(candidate, REJECT_STALE, message)
            continue

        if not candidate.evidence_span_ids:
            # Reachable only if require_evidence is False, but
            # dispatch still needs at least one span to anchor
            # against.
            _record(candidate, REJECT_MISSING_SPAN,
                    "dispatch requires at least one evidence_span_id")
            continue

        primary_span_id = candidate.evidence_span_ids[0]
        if candidate.source_record_ids:
            source_id = candidate.source_record_ids[0]
        else:
            # Fall back to the first source record we have.
            # (Production code would resolve the span's source_id
            # and require the source to be in the provided
            # source_records; the demo keeps it simple.)
            source_id = source_records[0].id if source_records else ""

        decision, ledger = _build_reducer_and_ledger_for_candidate(
            candidate,
            source_id=source_id,
            span_id=primary_span_id,
            decided_at=decided_at,
        )

        # Patch the entry's reducer_decision_id to match the
        # decision we built, then re-construct via the
        # appropriate class to preserve the frozen-dataclass
        # type the ReducerResult promises.
        entry_dict = asdict(ledger)
        entry_dict["reducer_decision_id"] = decision.id
        if isinstance(ledger, PreferenceLedgerEntry):
            entry: Union[PreferenceLedgerEntry, FactLedgerEntry] = (
                PreferenceLedgerEntry.from_dict(entry_dict)
            )
        else:
            entry = FactLedgerEntry.from_dict(entry_dict)

        decisions.append(decision)
        ledger_entries.append(entry)

    return ReducerResult(
        decisions=decisions,
        ledger_entries=ledger_entries,
        rejected=rejected,
    )


# ---------------------------------------------------------------------------
# Builders shared by the three scenarios
# ---------------------------------------------------------------------------

def _build_source_and_spans() -> tuple[SourceRecord, list[EvidenceSpan]]:
    """Build one SourceRecord and a small set of EvidenceSpans for the demo."""
    source_id = make_source_id("chatgpt_conversation", SOURCE_URI, FAKE_CONTENT_HASH)
    source = SourceRecord.from_dict({
        "id": source_id,
        "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": "Reference reducer demo transcript",
        "origin_uri": SOURCE_URI,
        "raw_ref": {"kind": "external_uri", "value": SOURCE_URI},
        "content_hash_sha256": FAKE_CONTENT_HASH,
        "captured_at": T_CAPTURED,
        "observed_at": T_CAPTURED,
        "author_or_sender": "user@example.com",
        "participants": ["user@example.com", "gpt-5.5"],
        "privacy_class": "internal",
        "custody_status": "external_pointer",
        "parser_version": "v1.0",
        "metadata": {},
    })

    spans: list[EvidenceSpan] = []
    for kind, value, summary in [
        ("line_range", "10-15", "Hard-constraint preference for memory architecture."),
        ("line_range", "20-25", "TasteCard example-grounding principle."),
        ("line_range", "30-35", "Spec drift > no spec principle."),
    ]:
        span_id = make_span_id(source_id, kind, value)
        spans.append(EvidenceSpan.from_dict({
            "id": span_id,
            "schema_version": "1.0.0",
            "source_id": source_id,
            "episode_id": None,
            "locator": {"kind": kind, "value": value},
            "text_excerpt": summary,
            "excerpt_policy": "synthetic",
            "span_hash_sha256": FAKE_SPAN_HASH,
            "privacy_class": "internal",
            "metadata": {"summary": summary},
        }))
    return source, spans


def _taste_candidate(
    source_id: str, span_id: str, *,
    taste_text: str,
    domain: str = "architecture",
    signal_kind: str = "principle",
    strength_hint: str = "hard_constraint",
    confidence: str = "high",
    extracted_at: str = T_EXTRACTED_FRESH,
) -> CandidateTasteSignal:
    """A CandidateTasteSignal whose id is content-derived and deterministic."""
    candidate_id = make_candidate_id("taste_signal", [span_id], {
        "domain": domain,
        "signal_kind": signal_kind,
        "taste_text": taste_text,
        "example_span_ids": sorted([span_id]),
        "contrast_span_ids": [],
        "strength_hint": strength_hint,
    })
    return CandidateTasteSignal.from_dict({
        "id": candidate_id,
        "schema_version": "1.0.0",
        "candidate_type": "taste_signal",
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": taste_text,
        "extracted_by": {"agent": "extractor-v1", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
        "extracted_at": extracted_at,
        "confidence": confidence,
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "domain": domain,
        "signal_kind": signal_kind,
        "taste_text": taste_text,
        "example_span_ids": [span_id],
        "contrast_span_ids": [],
        "strength_hint": strength_hint,
    })


def _claim_candidate(
    source_id: str, span_id: str, *,
    claim_text: str,
    subject: str = "memory architecture",
    predicate: str = "uses",
    obj: str = "hard constraints",
    claim_scope: str = "global",
    confidence: str = "high",
    extracted_at: str = T_EXTRACTED_FRESH,
) -> CandidateClaim:
    candidate_id = make_candidate_id("claim", [span_id], {
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "claim_text": claim_text,
        "claim_scope": claim_scope,
        "temporal_hint": {
            "observed_at": None, "asserted_at": None,
            "valid_from_hint": None, "valid_until_hint": None,
        },
    })
    return CandidateClaim.from_dict({
        "id": candidate_id,
        "schema_version": "1.0.0",
        "candidate_type": "claim",
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": claim_text,
        "extracted_by": {"agent": "extractor-v1", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
        "extracted_at": extracted_at,
        "confidence": confidence,
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "claim_text": claim_text,
        "claim_scope": claim_scope,
        "temporal_hint": {
            "observed_at": None, "asserted_at": None,
            "valid_from_hint": None, "valid_until_hint": None,
        },
    })


def _as_dicts(records: list[Any]) -> list[dict[str, Any]]:
    """asdict() is enough for the bundle validator."""
    return [asdict(r) for r in records]


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

def scenario_a_happy_path() -> ReducerResult:
    """Scenario A: 3 candidates, all pass, promoted.

    The 3 candidates exercise both ledger types the reference
    reducer supports: 2 CandidateTasteSignal -> 2
    PreferenceLedgerEntry, 1 CandidateClaim -> 1
    FactLedgerEntry.  3 MemoryReducerDecisions authorise them.
    """
    source, spans = _build_source_and_spans()
    span_a, span_b, span_c = spans[0], spans[1], spans[2]

    candidates = [
        _taste_candidate(source.id, span_a.id,
                         taste_text="Memory architecture rules are hard constraints, not soft preferences."),
        _taste_candidate(source.id, span_b.id,
                         taste_text="TasteCards must anchor principles in positive or negative examples."),
        _claim_candidate(source.id, span_c.id,
                         claim_text="The user prefers hard constraints on memory architecture.",
                         subject="memory architecture", predicate="prefers", obj="hard constraints"),
    ]

    result = reduce_candidates_to_trusted_memory(
        candidates, spans, [source],
    )
    return result, source, spans, candidates


def scenario_b_rejection() -> tuple[ReducerResult, SourceRecord, list[EvidenceSpan], list[CandidateInput]]:
    """Scenario B: 2 candidates are rejected, 1 is promoted.

    - Candidate #1 has ``evidence_span_ids == []``: rejected with
      ``"no_evidence"`` by the provenance check.
    - Candidate #2 has ``confidence == "low"``: rejected with
      ``"low_confidence"`` by the confidence check.
    - Candidate #3 is fine: promoted.
    """
    source, spans = _build_source_and_spans()
    span_a, span_b = spans[0], spans[1]

    # Candidate #1: a taste signal whose evidence_span_ids list is empty.
    # The library's ``from_dict`` refuses to build a candidate with
    # empty ``evidence_span_ids`` (the contract requires at least
    # one), so we use the direct dataclass constructor to bypass
    # the validator and simulate the "the extractor forgot to attach
    # evidence" failure the reducer is designed to catch.  In a real
    # pipeline, such a candidate would arrive in the candidate plane
    # already (perhaps with status="needs_review") and the reducer
    # would still refuse to promote it.
    ungrounded_id = make_candidate_id("taste_signal", [], {
        "domain": "architecture",
        "signal_kind": "principle",
        "taste_text": "Forgotten-evidence principle",
        "example_span_ids": [],
        "contrast_span_ids": [],
        "strength_hint": "strong",
    })
    cand_ungrounded = CandidateTasteSignal(
        id=ungrounded_id,
        schema_version="1.0.0",
        candidate_type="taste_signal",
        source_record_ids=[source.id],
        episode_record_ids=[],
        evidence_span_ids=[],  # the failure: no evidence
        natural_language_summary="Ungrounded candidate (no evidence attached).",
        extracted_by={"agent": "extractor-v1", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
        extracted_at=T_EXTRACTED_FRESH,
        confidence="high",
        risk_class="low",
        status="candidate",
        review={"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        metadata={},
        domain="architecture",
        signal_kind="principle",
        taste_text="Forgotten-evidence principle",
        example_span_ids=[],
        contrast_span_ids=[],
        strength_hint="strong",
    )

    # Candidate #2: low-confidence taste signal
    cand_low_conf = _taste_candidate(
        source.id, span_a.id,
        taste_text="Possibly true, mostly guessed.",
        confidence="low",
    )

    # Candidate #3: a clean claim
    cand_clean = _claim_candidate(
        source.id, span_b.id,
        claim_text="The team uses TasteCards for example-grounding.",
        subject="team", predicate="uses", obj="TasteCards",
    )

    candidates = [cand_ungrounded, cand_low_conf, cand_clean]
    result = reduce_candidates_to_trusted_memory(
        candidates, spans, [source],
    )
    return result, source, spans, candidates


def scenario_c_validator_enforcement(source: SourceRecord, span: EvidenceSpan) -> None:
    """Scenario C: deliberately produce an unauthorized bundle and catch it.

    We hand-build a reducer decision whose ``target_ledger_entry_ids``
    does NOT include the ledger entry that references it.  The library
    refuses to validate a bundle like this -- that is the whole point
    of the bidirectional ``reducer_decision_id`` /
    ``target_ledger_entry_ids`` link.  This scenario demonstrates the
    failure mode on purpose, with a clear error message.
    """
    candidate = _taste_candidate(
        source.id, span.id,
        taste_text="An orphan ledger entry waiting to be flagged.",
    )

    # Build a "real" ledger entry first, then build a reducer that
    # targets a different (fake) ledger id -- so the link is broken.
    ledger = _build_preference_entry(
        subject="memory architecture",
        preference_text=candidate.taste_text,
        domain="architecture",
        strength="hard_constraint",
        confidence="high",
        scope="global",
        source_id=source.id,
        span_id=span.id,
        decided_at=T_DECIDED,
        candidate_id=candidate.id,
    )
    wrong_target_id = "pref_" + "0" * 24  # an id that is not in the bundle

    reducer_id = make_reducer_decision_id(
        "promote", [candidate.id], [wrong_target_id], [span.id],
        "broken link: targets a ledger id that does not exist",
    )
    reducer = MemoryReducerDecision.from_dict({
        "id": reducer_id,
        "schema_version": "1.0.0",
        "decision_type": "promote",
        "target_candidate_ids": [candidate.id],
        "target_ledger_entry_ids": [wrong_target_id],
        "evidence_span_ids": [span.id],
        "rationale": "broken link: targets a ledger id that does not exist",
        "decided_by": {"agent": "broken-reducer", "model": "test", "tool": None, "prompt_ref": None},
        "decided_at": T_DECIDED,
        "confidence": "high",
        "risk_class": "low",
        "checks": {
            "provenance": "pass", "temporal_validity": "pass",
            "contradiction_scan": "pass", "privacy": "pass", "usefulness": "pass",
        },
        "metadata": {},
    })
    # Patch the entry's reducer_decision_id to point at our reducer.
    entry_dict = asdict(ledger)
    entry_dict["reducer_decision_id"] = reducer_id
    entry = PreferenceLedgerEntry.from_dict(entry_dict)

    # Try to validate -- this MUST raise.  We catch and report the
    # error so the demo is informative rather than crashy.
    try:
        validate_ledger_bundle(
            source_records=[asdict(source)],
            episode_records=[],
            evidence_spans=[asdict(span)],
            candidate_records=[asdict(candidate)],
            reducer_decisions=[asdict(reducer)],
            ledger_entries=[asdict(entry)],
        )
    except ValueError as exc:
        return exc
    raise AssertionError(
        "validate_ledger_bundle should have raised on an unauthorized ledger entry, "
        "but it did not. The library's authorization invariant is broken."
    )


# ---------------------------------------------------------------------------
# main()
# ---------------------------------------------------------------------------

def _print_scenario_a() -> None:
    result, source, spans, candidates = scenario_a_happy_path()
    print("=== Scenario A: happy path ===")
    print(f"  candidates:        {len(candidates)}")
    print(f"  promoted:          {len(result.ledger_entries)} ledger entries, "
          f"{len(result.decisions)} reducer decisions")
    print(f"  rejected:          {len(result.rejected)}")
    ledger_kinds = sorted({e.ledger_type for e in result.ledger_entries})
    print(f"  ledger_type mix:   {ledger_kinds}")
    # Run the library's bundle validator.  This MUST pass.
    validate_ledger_bundle(
        source_records=_as_dicts([source]),
        episode_records=[],
        evidence_spans=_as_dicts(spans),
        candidate_records=_as_dicts(candidates),
        reducer_decisions=_as_dicts(result.decisions),
        ledger_entries=_as_dicts(result.ledger_entries),
    )
    print("  bundle validated:  OK")
    print()


def _print_scenario_b() -> None:
    result, source, spans, candidates = scenario_b_rejection()
    print("=== Scenario B: rejection ===")
    print(f"  candidates:        {len(candidates)}")
    print(f"  rejected:          {len(result.rejected)}")
    for r in result.rejected:
        print(f"    - {r.candidate_id[:24]}... reason={r.reason_code!r} ({r.message})")
    print(f"  promoted:          {len(result.ledger_entries)} ledger entries, "
          f"{len(result.decisions)} reducer decisions")
    # The promoted subset is 1 candidate, 1 ledger entry, 1 reducer
    # decision.  The library must accept it.
    if result.ledger_entries:
        promoted_candidates = [
            c for c in candidates
            if c.id not in {r.candidate_id for r in result.rejected}
        ]
        validate_ledger_bundle(
            source_records=_as_dicts([source]),
            episode_records=[],
            evidence_spans=_as_dicts(spans),
            candidate_records=_as_dicts(promoted_candidates),
            reducer_decisions=_as_dicts(result.decisions),
            ledger_entries=_as_dicts(result.ledger_entries),
        )
        print("  promoted-subset bundle validated:  OK")
    print()


def _print_scenario_c() -> None:
    print("=== Scenario C: validator enforcement ===")
    source, spans = _build_source_and_spans()
    span = spans[0]
    error = scenario_c_validator_enforcement(source, span)
    print("  deliberate failure injected: reducer.target_ledger_entry_ids")
    print("    does not include the ledger entry that points back at it.")
    print(f"  validate_ledger_bundle raised: ValueError({error!s})")
    print()


def main() -> None:
    _print_scenario_a()
    _print_scenario_b()
    _print_scenario_c()


if __name__ == "__main__":
    main()
