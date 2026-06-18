"""Audit pack: the chain of custody behind a memory bundle.

An *audit pack* answers the question a regulator, auditor, or CFO
actually asks of an AI system: **"every fact your AI relies on --
who authorized it, and what is the evidence behind it?"**

Where the memory hygiene report (:mod:`.hygiene`) is a structural
snapshot ("what does the bundle look like?"), the audit pack is a
provenance walk ("how did each trusted entry get here?"). For every
ledger entry in the bundle it assembles the full authorization
chain:

    ledger entry -> authorizing reducer decision -> candidate(s)
                 -> evidence span(s) -> source record(s)

and flags the chain ``COMPLETE`` or ``INCOMPLETE`` -- a missing
decision, a dangling candidate, an unresolved span, or an absent
source never crashes the pack; it is *reported*, because a broken
chain is exactly what an audit exists to find.

The pack also collects:

- **Rejections in the period** -- every ``decision_type="reject"``
  reducer decision in the bundle, with its rationale and failing
  checks. What the system refused to remember is as audit-relevant
  as what it accepted.
- **The supersession changelog** -- every governed replacement of
  one entry by another, with the temporal handoff.
- **The bundle fingerprint** -- the content-addressed digest tying
  the pack to the exact bundle state it describes.

Typical use cases:

- **Inspection prep.** Export the pack for "interest-rate policy,
  Jan--Jun" before the regulator's onsite; hand over one Markdown
  file instead of a week of reconstruction.
- **Quarterly attestation.** The pack id is content-derived; the
  same bundle and ``as_of`` produce the same pack, so an attested
  pack can be re-verified later.
- **CLI.** ``python -m agent_memory_contracts audit bundle.jsonl``
  emits the Markdown report (or a JSON envelope with ``--json``).

Like the rest of the bundle primitives, this module is
standard-library only.

.. versionadded:: 1.2.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .bundles import bundle_fingerprint
from .evidence_ids import _prefixed_id
from .hygiene import _now_iso8601, _validate_iso8601


#: The default pack title. This is the promise the report makes.
DEFAULT_AUDIT_TITLE = (
    "Every fact your AI relies on, who authorized it, "
    "and the evidence behind it."
)

CHAIN_COMPLETE = "COMPLETE"
CHAIN_INCOMPLETE = "INCOMPLETE"

#: Ledger-entry id prefixes (the trusted plane).
_LEDGER_PREFIXES = ("fact_", "pref_", "dec_")


# ---------------------------------------------------------------------------
# Record classification helpers
# ---------------------------------------------------------------------------


def _record_id(record: dict[str, Any]) -> str:
    return str(record.get("id", ""))


def _is_ledger_entry(record: dict[str, Any]) -> bool:
    rid = _record_id(record)
    if rid.startswith(_LEDGER_PREFIXES):
        return True
    return record.get("ledger_type") in ("fact", "preference", "decision")


def _is_reducer_decision(record: dict[str, Any]) -> bool:
    rid = _record_id(record)
    if rid.startswith("redmem_"):
        return True
    return ("decision_type" in record
            and "target_candidate_ids" in record)


def _statement_for_entry(record: dict[str, Any]) -> str:
    """A one-line human statement of what the entry asserts."""
    for key in ("fact_text", "preference_text", "decision_text"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    subject = record.get("subject")
    predicate = record.get("predicate")
    obj = record.get("object")
    if all(isinstance(v, str) and v for v in (subject, predicate, obj)):
        return f"{subject} {predicate} {obj}"
    return f"(unstated entry {_record_id(record)})"


def _string_list_field(record: dict[str, Any], key: str) -> list[str]:
    value = record.get(key)
    if not isinstance(value, list):
        return []
    return [str(v) for v in value]


def _decided_by_part(record: dict[str, Any], key: str) -> str:
    decided_by = record.get("decided_by")
    if isinstance(decided_by, dict):
        value = decided_by.get(key)
        if isinstance(value, str):
            return value
    return ""


# ---------------------------------------------------------------------------
# The pack's record types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEvidenceRef:
    """One evidence span cited by a ledger entry, resolved as far
    as the bundle allows.

    Attributes:
        span_id: The cited ``EvidenceSpan`` id.
        resolved: True if the span itself is present in the bundle.
        source_id: The span's source id (``""`` if unresolved).
        source_title: The source's title (``""`` if the source is
            absent from the bundle).
        locator: Human-readable locator, e.g. ``line_range 7-7``.
        excerpt: The span's ``text_excerpt`` (``""`` if none).
    """

    span_id: str
    resolved: bool
    source_id: str
    source_title: str
    locator: str
    excerpt: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditEvidenceRef:
        return cls(
            span_id=str(data["span_id"]),
            resolved=bool(data["resolved"]),
            source_id=str(data.get("source_id") or ""),
            source_title=str(data.get("source_title") or ""),
            locator=str(data.get("locator") or ""),
            excerpt=str(data.get("excerpt") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "resolved": self.resolved,
            "source_id": self.source_id,
            "source_title": self.source_title,
            "locator": self.locator,
            "excerpt": self.excerpt,
        }


@dataclass(frozen=True)
class AuditChainEntry:
    """The full authorization chain for one trusted ledger entry.

    Attributes:
        ledger_entry_id: The entry's id.
        ledger_type: ``fact`` / ``preference`` / ``decision`` (or
            ``""`` if unstated).
        status: The entry's lifecycle status.
        statement: One-line human statement of the entry's content.
        valid_from: The entry's ``valid_from`` (``""`` if unset).
        valid_until: The entry's ``valid_until`` (``""`` = open).
        reducer_decision_id: The authorizing decision's id (``""``
            if the entry carries none).
        decision_type: The decision's type (``""`` if unresolved).
        decided_by_agent: The decision's ``decided_by.agent``.
        decided_by_model: The decision's ``decided_by.model``.
        decided_at: When the decision was made.
        rationale: The decision's rationale text.
        candidate_ids: Candidate ids the entry cites.
        missing_candidate_ids: Cited candidates absent from the
            bundle.
        evidence: One :class:`AuditEvidenceRef` per cited span.
        chain_status: ``"COMPLETE"`` or ``"INCOMPLETE"``.
        missing: Human-readable list of everything the chain is
            missing. Empty iff ``chain_status == "COMPLETE"``.
    """

    ledger_entry_id: str
    ledger_type: str
    status: str
    statement: str
    valid_from: str
    valid_until: str
    reducer_decision_id: str
    decision_type: str
    decided_by_agent: str
    decided_by_model: str
    decided_at: str
    rationale: str
    candidate_ids: list[str]
    missing_candidate_ids: list[str]
    evidence: list[AuditEvidenceRef]
    chain_status: str
    missing: list[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditChainEntry:
        return cls(
            ledger_entry_id=str(data["ledger_entry_id"]),
            ledger_type=str(data.get("ledger_type") or ""),
            status=str(data.get("status") or ""),
            statement=str(data.get("statement") or ""),
            valid_from=str(data.get("valid_from") or ""),
            valid_until=str(data.get("valid_until") or ""),
            reducer_decision_id=str(data.get("reducer_decision_id") or ""),
            decision_type=str(data.get("decision_type") or ""),
            decided_by_agent=str(data.get("decided_by_agent") or ""),
            decided_by_model=str(data.get("decided_by_model") or ""),
            decided_at=str(data.get("decided_at") or ""),
            rationale=str(data.get("rationale") or ""),
            candidate_ids=[str(c) for c in (data.get("candidate_ids") or [])],
            missing_candidate_ids=[
                str(c) for c in (data.get("missing_candidate_ids") or [])],
            evidence=[AuditEvidenceRef.from_dict(e)
                      for e in (data.get("evidence") or [])],
            chain_status=str(data.get("chain_status") or CHAIN_INCOMPLETE),
            missing=[str(m) for m in (data.get("missing") or [])],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "ledger_entry_id": self.ledger_entry_id,
            "ledger_type": self.ledger_type,
            "status": self.status,
            "statement": self.statement,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "reducer_decision_id": self.reducer_decision_id,
            "decision_type": self.decision_type,
            "decided_by_agent": self.decided_by_agent,
            "decided_by_model": self.decided_by_model,
            "decided_at": self.decided_at,
            "rationale": self.rationale,
            "candidate_ids": list(self.candidate_ids),
            "missing_candidate_ids": list(self.missing_candidate_ids),
            "evidence": [e.to_dict() for e in self.evidence],
            "chain_status": self.chain_status,
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class AuditRejection:
    """One ``reject`` reducer decision found in the bundle.

    Attributes:
        reducer_decision_id: The reject decision's id.
        target_candidate_ids: The candidates it refused to promote.
        rationale: Why.
        decided_by_agent: ``decided_by.agent``.
        decided_by_model: ``decided_by.model``.
        decided_at: When.
        evidence_span_ids: The spans the decision judged.
        failed_checks: The check keys whose value is ``"fail"``.
        auto_rejected: True if the decision's metadata marks it
            ``auto_rejected`` (a machine gate rather than a human
            reviewer).
    """

    reducer_decision_id: str
    target_candidate_ids: list[str]
    rationale: str
    decided_by_agent: str
    decided_by_model: str
    decided_at: str
    evidence_span_ids: list[str]
    failed_checks: list[str]
    auto_rejected: bool

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditRejection:
        return cls(
            reducer_decision_id=str(data["reducer_decision_id"]),
            target_candidate_ids=[
                str(c) for c in (data.get("target_candidate_ids") or [])],
            rationale=str(data.get("rationale") or ""),
            decided_by_agent=str(data.get("decided_by_agent") or ""),
            decided_by_model=str(data.get("decided_by_model") or ""),
            decided_at=str(data.get("decided_at") or ""),
            evidence_span_ids=[
                str(s) for s in (data.get("evidence_span_ids") or [])],
            failed_checks=[str(c) for c in (data.get("failed_checks") or [])],
            auto_rejected=bool(data.get("auto_rejected", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "reducer_decision_id": self.reducer_decision_id,
            "target_candidate_ids": list(self.target_candidate_ids),
            "rationale": self.rationale,
            "decided_by_agent": self.decided_by_agent,
            "decided_by_model": self.decided_by_model,
            "decided_at": self.decided_at,
            "evidence_span_ids": list(self.evidence_span_ids),
            "failed_checks": list(self.failed_checks),
            "auto_rejected": self.auto_rejected,
        }


@dataclass(frozen=True)
class AuditSupersession:
    """One governed replacement: ``superseded_entry`` gave way to
    ``successor_entry``.

    Attributes:
        superseded_entry_id: The entry that was replaced.
        successor_entry_id: The entry that replaced it.
        superseded_valid_until: When the old entry stopped being
            current (``""`` if unset -- itself an audit finding).
        successor_valid_from: When the new entry became current.
        statement_before: The old entry's statement (``""`` if the
            old entry is absent from the bundle).
        statement_after: The new entry's statement.
    """

    superseded_entry_id: str
    successor_entry_id: str
    superseded_valid_until: str
    successor_valid_from: str
    statement_before: str
    statement_after: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditSupersession:
        return cls(
            superseded_entry_id=str(data["superseded_entry_id"]),
            successor_entry_id=str(data["successor_entry_id"]),
            superseded_valid_until=str(
                data.get("superseded_valid_until") or ""),
            successor_valid_from=str(
                data.get("successor_valid_from") or ""),
            statement_before=str(data.get("statement_before") or ""),
            statement_after=str(data.get("statement_after") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "superseded_entry_id": self.superseded_entry_id,
            "successor_entry_id": self.successor_entry_id,
            "superseded_valid_until": self.superseded_valid_until,
            "successor_valid_from": self.successor_valid_from,
            "statement_before": self.statement_before,
            "statement_after": self.statement_after,
        }


@dataclass(frozen=True)
class AuditPack:
    """The audit pack for a memory bundle.

    Attributes:
        id: ``audit_<sha256 hex>``. Content-derived from the
            identifying fields (bundle fingerprint, as_of, title,
            counts, chain summary, rejection ids, supersession
            pairs). Same bundle + same ``as_of`` = same pack id.
        schema_version: ``"1.0.0"``.
        title: The pack's title (the report's H1).
        bundle_fingerprint: SHA-256 of the bundle the pack
            describes.
        as_of: ISO 8601 UTC; when the pack was computed.
        total_records: Record count in the bundle.
        ledger_entry_count: Trusted ledger entries audited.
        complete_chain_count: Entries with a complete chain.
        incomplete_chain_count: Entries flagged ``INCOMPLETE``.
        rejected_count: ``reject`` decisions in the bundle.
        supersession_count: Supersession events.
        reducer_decision_count: All reducer decisions in the bundle.
        candidate_count: Candidate-plane records in the bundle.
        evidence_span_count: Evidence spans in the bundle.
        source_count: Source records in the bundle.
        entries: One :class:`AuditChainEntry` per ledger entry.
        rejections: One :class:`AuditRejection` per reject decision.
        supersessions: One :class:`AuditSupersession` per event.
        metadata: Free-form dict for product-specific fields.
    """

    id: str
    schema_version: str
    title: str
    bundle_fingerprint: str
    as_of: str
    total_records: int
    ledger_entry_count: int
    complete_chain_count: int
    incomplete_chain_count: int
    rejected_count: int
    supersession_count: int
    reducer_decision_count: int
    candidate_count: int
    evidence_span_count: int
    source_count: int
    entries: list[AuditChainEntry]
    rejections: list[AuditRejection]
    supersessions: list[AuditSupersession]
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditPack:
        """Build an ``AuditPack`` from a dict, recomputing the
        content-derived id. The input id, if any, is ignored --
        ids are derived, not assigned."""
        entries = [AuditChainEntry.from_dict(e)
                   for e in (data.get("entries") or [])]
        rejections = [AuditRejection.from_dict(r)
                      for r in (data.get("rejections") or [])]
        supersessions = [AuditSupersession.from_dict(s)
                         for s in (data.get("supersessions") or [])]
        counts = {
            "total_records": int(data["total_records"]),
            "ledger_entry_count": int(data["ledger_entry_count"]),
            "complete_chain_count": int(data["complete_chain_count"]),
            "incomplete_chain_count": int(data["incomplete_chain_count"]),
            "rejected_count": int(data["rejected_count"]),
            "supersession_count": int(data["supersession_count"]),
            "reducer_decision_count": int(data["reducer_decision_count"]),
            "candidate_count": int(data["candidate_count"]),
            "evidence_span_count": int(data["evidence_span_count"]),
            "source_count": int(data["source_count"]),
        }
        pack_id = _compute_audit_pack_id(
            bundle_fingerprint=str(data["bundle_fingerprint"]),
            as_of=str(data["as_of"]),
            title=str(data["title"]),
            counts=counts,
            entries=entries,
            rejections=rejections,
            supersessions=supersessions,
        )
        return cls(
            id=pack_id,
            schema_version="1.0.0",
            title=str(data["title"]),
            bundle_fingerprint=str(data["bundle_fingerprint"]),
            as_of=str(data["as_of"]),
            entries=entries,
            rejections=rejections,
            supersessions=supersessions,
            metadata=dict(data.get("metadata") or {}),
            **counts,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict (suitable for JSON output)."""
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "title": self.title,
            "bundle_fingerprint": self.bundle_fingerprint,
            "as_of": self.as_of,
            "total_records": self.total_records,
            "ledger_entry_count": self.ledger_entry_count,
            "complete_chain_count": self.complete_chain_count,
            "incomplete_chain_count": self.incomplete_chain_count,
            "rejected_count": self.rejected_count,
            "supersession_count": self.supersession_count,
            "reducer_decision_count": self.reducer_decision_count,
            "candidate_count": self.candidate_count,
            "evidence_span_count": self.evidence_span_count,
            "source_count": self.source_count,
            "entries": [e.to_dict() for e in self.entries],
            "rejections": [r.to_dict() for r in self.rejections],
            "supersessions": [s.to_dict() for s in self.supersessions],
            "metadata": dict(self.metadata),
        }


def _compute_audit_pack_id(
    *,
    bundle_fingerprint: str,
    as_of: str,
    title: str,
    counts: dict[str, int],
    entries: list[AuditChainEntry],
    rejections: list[AuditRejection],
    supersessions: list[AuditSupersession],
) -> str:
    """Compute the content-derived id for an AuditPack."""
    payload: dict[str, Any] = {
        "bundle_fingerprint": bundle_fingerprint,
        "as_of": as_of,
        "title": title,
        "counts": counts,
        "chain_summary": [
            [e.ledger_entry_id, e.chain_status] for e in entries],
        "rejection_ids": [r.reducer_decision_id for r in rejections],
        "supersession_pairs": [
            [s.superseded_entry_id, s.successor_entry_id]
            for s in supersessions],
    }
    return _prefixed_id("audit", payload, length=24)


# ---------------------------------------------------------------------------
# compute_audit_pack
# ---------------------------------------------------------------------------


def _build_chain_entry(
    entry: dict[str, Any],
    *,
    by_id: dict[str, dict[str, Any]],
) -> AuditChainEntry:
    """Assemble the authorization chain for one ledger entry.

    Never raises on a broken chain; every gap is recorded in
    ``missing`` and the chain is flagged ``INCOMPLETE``.
    """
    missing: list[str] = []
    entry_id = _record_id(entry)

    # The authorizing reducer decision.
    decision_id_raw = entry.get("reducer_decision_id")
    decision_id = str(decision_id_raw) if isinstance(
        decision_id_raw, str) and decision_id_raw else ""
    decision: dict[str, Any] | None = None
    if not decision_id:
        missing.append("no reducer_decision_id on the entry")
    else:
        decision = by_id.get(decision_id)
        if decision is None:
            missing.append(
                f"authorizing decision {decision_id} not in records")
        else:
            targets = _string_list_field(decision, "target_ledger_entry_ids")
            if entry_id not in targets:
                missing.append(
                    f"decision {decision_id} does not list this entry in "
                    f"target_ledger_entry_ids")

    # Candidates.
    candidate_ids = _string_list_field(entry, "candidate_ids")
    missing_candidates = [c for c in candidate_ids if c not in by_id]
    for candidate_id in missing_candidates:
        missing.append(f"candidate {candidate_id} not in records")

    # Evidence spans -> sources.
    span_ids = _string_list_field(entry, "evidence_span_ids")
    if not span_ids:
        missing.append("no evidence_span_ids on the entry")
    evidence: list[AuditEvidenceRef] = []
    for span_id in span_ids:
        span = by_id.get(span_id)
        if span is None:
            missing.append(f"evidence span {span_id} not in records")
            evidence.append(AuditEvidenceRef(
                span_id=span_id, resolved=False, source_id="",
                source_title="", locator="", excerpt=""))
            continue
        source_id = str(span.get("source_id") or "")
        source = by_id.get(source_id) if source_id else None
        if source_id and source is None:
            missing.append(
                f"source {source_id} for span {span_id} not in records")
        locator = span.get("locator")
        locator_text = ""
        if isinstance(locator, dict):
            locator_text = (f"{locator.get('kind', '')} "
                            f"{locator.get('value', '')}").strip()
        excerpt_raw = span.get("text_excerpt")
        evidence.append(AuditEvidenceRef(
            span_id=span_id,
            resolved=True,
            source_id=source_id,
            source_title=(str(source.get("title") or "")
                          if source is not None else ""),
            locator=locator_text,
            excerpt=str(excerpt_raw) if isinstance(excerpt_raw, str) else "",
        ))

    return AuditChainEntry(
        ledger_entry_id=entry_id,
        ledger_type=str(entry.get("ledger_type") or ""),
        status=str(entry.get("status") or ""),
        statement=_statement_for_entry(entry),
        valid_from=str(entry.get("valid_from") or ""),
        valid_until=str(entry.get("valid_until") or ""),
        reducer_decision_id=decision_id,
        decision_type=(str(decision.get("decision_type") or "")
                       if decision is not None else ""),
        decided_by_agent=(_decided_by_part(decision, "agent")
                          if decision is not None else ""),
        decided_by_model=(_decided_by_part(decision, "model")
                          if decision is not None else ""),
        decided_at=(str(decision.get("decided_at") or "")
                    if decision is not None else ""),
        rationale=(str(decision.get("rationale") or "")
                   if decision is not None else ""),
        candidate_ids=candidate_ids,
        missing_candidate_ids=missing_candidates,
        evidence=evidence,
        chain_status=CHAIN_COMPLETE if not missing else CHAIN_INCOMPLETE,
        missing=missing,
    )


def compute_audit_pack(
    records: list[dict[str, Any]],
    *,
    as_of: str | None = None,
    title: str = DEFAULT_AUDIT_TITLE,
) -> AuditPack:
    """Compute an :class:`AuditPack` for a bundle of records.

    For every trusted ledger entry in ``records``, the pack walks
    the full authorization chain (entry -> authorizing decision ->
    candidates -> evidence spans -> sources) and flags it
    ``COMPLETE`` or ``INCOMPLETE``. It also collects every
    ``reject`` reducer decision and every supersession event in the
    bundle. Broken chains are findings, not errors: nothing in the
    walk raises on missing references.

    Args:
        records: A list of record dicts (any mix of planes; the
            pack classifies them by id prefix and fields).
        as_of: ISO 8601 UTC "computed at" timestamp. ``None`` for
            the current UTC time. Passing a fixed value makes the
            pack -- and its content-derived id -- reproducible.
        title: The pack title (the Markdown report's H1). Defaults
            to :data:`DEFAULT_AUDIT_TITLE`.

    Returns:
        An :class:`AuditPack`.

    Raises:
        TypeError: if ``records`` is not a list of dicts.
        ValueError: if ``as_of`` is provided but not valid ISO
            8601, or ``title`` is empty.
    """
    if not isinstance(records, list):
        raise TypeError(
            f"records must be a list; got {type(records).__name__}")
    for i, rec in enumerate(records):
        if not isinstance(rec, dict):
            raise TypeError(
                f"each record must be a dict; got "
                f"{type(rec).__name__} at index {i}")
    if not isinstance(title, str) or not title:
        raise ValueError(f"title must be a non-empty string; got {title!r}")
    if as_of is None:
        as_of = _now_iso8601()
    _validate_iso8601(as_of, field_name="as_of")

    by_id: dict[str, dict[str, Any]] = {}
    for rec in records:
        rid = _record_id(rec)
        if rid:
            by_id[rid] = rec

    ledger_entries: list[dict[str, Any]] = []
    reducer_decisions: list[dict[str, Any]] = []
    candidate_count = 0
    evidence_span_count = 0
    source_count = 0
    for rec in records:
        rid = _record_id(rec)
        if _is_ledger_entry(rec):
            ledger_entries.append(rec)
        elif _is_reducer_decision(rec):
            reducer_decisions.append(rec)
        elif rid.startswith("cand_"):
            candidate_count += 1
        elif rid.startswith("span_"):
            evidence_span_count += 1
        elif rid.startswith("src_"):
            source_count += 1

    # Per-entry authorization chains, in a deterministic order:
    # by valid_from (unset last), then by id.
    def _entry_sort_key(entry: dict[str, Any]) -> tuple[int, str, str]:
        valid_from = entry.get("valid_from")
        has_vf = isinstance(valid_from, str) and bool(valid_from)
        return (0 if has_vf else 1,
                str(valid_from) if has_vf else "",
                _record_id(entry))

    chain_entries = [
        _build_chain_entry(entry, by_id=by_id)
        for entry in sorted(ledger_entries, key=_entry_sort_key)
    ]

    # Rejections, ordered by decided_at then id.
    rejections: list[AuditRejection] = []
    for decision in sorted(
            reducer_decisions,
            key=lambda d: (str(d.get("decided_at") or ""), _record_id(d))):
        if decision.get("decision_type") != "reject":
            continue
        checks = decision.get("checks")
        failed = (sorted(k for k, v in checks.items() if v == "fail")
                  if isinstance(checks, dict) else [])
        metadata = decision.get("metadata")
        auto = (bool(metadata.get("auto_rejected"))
                if isinstance(metadata, dict) else False)
        rejections.append(AuditRejection(
            reducer_decision_id=_record_id(decision),
            target_candidate_ids=_string_list_field(
                decision, "target_candidate_ids"),
            rationale=str(decision.get("rationale") or ""),
            decided_by_agent=_decided_by_part(decision, "agent"),
            decided_by_model=_decided_by_part(decision, "model"),
            decided_at=str(decision.get("decided_at") or ""),
            evidence_span_ids=_string_list_field(
                decision, "evidence_span_ids"),
            failed_checks=failed,
            auto_rejected=auto,
        ))

    # Supersession changelog: one event per (old, new) edge, taken
    # from the successor side (`supersedes`), deduplicated against
    # the old side (`superseded_by`) which describes the same edge.
    supersessions: list[AuditSupersession] = []
    for entry in ledger_entries:
        new_id = _record_id(entry)
        for old_id in _string_list_field(entry, "supersedes"):
            old = by_id.get(old_id)
            supersessions.append(AuditSupersession(
                superseded_entry_id=old_id,
                successor_entry_id=new_id,
                superseded_valid_until=(
                    str(old.get("valid_until") or "")
                    if old is not None else ""),
                successor_valid_from=str(entry.get("valid_from") or ""),
                statement_before=(_statement_for_entry(old)
                                  if old is not None else ""),
                statement_after=_statement_for_entry(entry),
            ))
    supersessions.sort(key=lambda s: (
        s.successor_valid_from, s.superseded_entry_id,
        s.successor_entry_id))

    complete = sum(1 for e in chain_entries
                   if e.chain_status == CHAIN_COMPLETE)
    counts = {
        "total_records": len(records),
        "ledger_entry_count": len(chain_entries),
        "complete_chain_count": complete,
        "incomplete_chain_count": len(chain_entries) - complete,
        "rejected_count": len(rejections),
        "supersession_count": len(supersessions),
        "reducer_decision_count": len(reducer_decisions),
        "candidate_count": candidate_count,
        "evidence_span_count": evidence_span_count,
        "source_count": source_count,
    }
    bundle_fp = bundle_fingerprint(records)
    pack_id = _compute_audit_pack_id(
        bundle_fingerprint=bundle_fp,
        as_of=as_of,
        title=title,
        counts=counts,
        entries=chain_entries,
        rejections=rejections,
        supersessions=supersessions,
    )
    return AuditPack(
        id=pack_id,
        schema_version="1.0.0",
        title=title,
        bundle_fingerprint=bundle_fp,
        as_of=as_of,
        entries=chain_entries,
        rejections=rejections,
        supersessions=supersessions,
        metadata={},
        **counts,
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------


def audit_pack_to_markdown(pack: AuditPack) -> str:
    """Format an :class:`AuditPack` as a CFO-readable Markdown report.

    Pure function. No I/O, no side effects. Returns a string the
    caller can print, write to disk, or attach to an inspection
    response.

    The output is a single Markdown document with:

    - The pack title as the H1.
    - A header block (as-of timestamp, bundle fingerprint, pack id)
      and a one-paragraph headline of the counts.
    - One section per trusted ledger entry: the statement, who
      authorized it (decision id, decider, timestamp, rationale),
      the candidates it came from, the evidence behind it (span ->
      source -> excerpt), and the chain status. Incomplete chains
      list exactly what is missing.
    - A "Rejected in this period" section (every reject decision,
      with rationale and failing checks; "None." when empty).
    - A "Supersession changelog" section ("None." when empty).
    - A footer tying the pack to the bundle fingerprint.
    """
    lines: list[str] = []
    lines.append(f"# {pack.title}")
    lines.append("")
    lines.append(f"**As of:** {pack.as_of}  ")
    lines.append(f"**Bundle fingerprint:** `{pack.bundle_fingerprint}`  ")
    lines.append(f"**Pack id:** `{pack.id}`")
    lines.append("")
    lines.append(
        f"**{pack.ledger_entry_count} trusted ledger entries** — "
        f"{pack.complete_chain_count} with a complete authorization "
        f"chain, {pack.incomplete_chain_count} incomplete. "
        f"{pack.rejected_count} rejection(s) on record. "
        f"{pack.supersession_count} supersession event(s). "
        f"({pack.total_records} records in the bundle: "
        f"{pack.source_count} sources, {pack.evidence_span_count} "
        f"evidence spans, {pack.candidate_count} candidates, "
        f"{pack.reducer_decision_count} reducer decisions.)")
    lines.append("")

    lines.append("## Trusted ledger — authorization chains")
    lines.append("")
    if not pack.entries:
        lines.append("No trusted ledger entries in this bundle.")
        lines.append("")
    for n, entry in enumerate(pack.entries, start=1):
        lines.append(f"### {n}. {entry.statement}")
        lines.append("")
        lines.append(f"- **Entry:** `{entry.ledger_entry_id}` "
                     f"({entry.ledger_type or 'untyped'}, "
                     f"{entry.status or 'no status'})")
        valid_until = entry.valid_until or "open"
        lines.append(f"- **Valid:** {entry.valid_from or 'unset'} → "
                     f"{valid_until}")
        if entry.reducer_decision_id and entry.decision_type:
            lines.append(
                f"- **Authorized by:** `{entry.reducer_decision_id}` "
                f"({entry.decision_type}) — {entry.decided_by_agent} / "
                f"{entry.decided_by_model} at {entry.decided_at}")
            if entry.rationale:
                lines.append(f"- **Rationale:** {entry.rationale}")
        elif entry.reducer_decision_id:
            lines.append(
                f"- **Authorized by:** `{entry.reducer_decision_id}` "
                f"(decision not present in the bundle)")
        else:
            lines.append("- **Authorized by:** (no decision recorded)")
        if entry.candidate_ids:
            rendered = ", ".join(f"`{c}`" for c in entry.candidate_ids)
            lines.append(f"- **Candidates:** {rendered}")
        else:
            lines.append("- **Candidates:** none recorded")
        if entry.evidence:
            lines.append("- **Evidence:**")
            for ev in entry.evidence:
                if not ev.resolved:
                    lines.append(f"  - `{ev.span_id}` — UNRESOLVED "
                                 f"(span not in bundle)")
                    continue
                source_part = ev.source_title or ev.source_id or "unknown source"
                excerpt_part = f': "{ev.excerpt}"' if ev.excerpt else ""
                locator_part = f" ({ev.locator})" if ev.locator else ""
                lines.append(f"  - `{ev.span_id}` — {source_part}"
                             f"{locator_part}{excerpt_part}")
        else:
            lines.append("- **Evidence:** none recorded")
        if entry.chain_status == CHAIN_COMPLETE:
            lines.append(f"- **Chain:** {CHAIN_COMPLETE}")
        else:
            lines.append(f"- **Chain:** **{CHAIN_INCOMPLETE}**")
            for item in entry.missing:
                lines.append(f"  - missing: {item}")
        lines.append("")

    lines.append("## Rejected in this period")
    lines.append("")
    if not pack.rejections:
        lines.append("None.")
        lines.append("")
    for rejection in pack.rejections:
        auto_part = " (auto-rejected)" if rejection.auto_rejected else ""
        lines.append(f"- `{rejection.reducer_decision_id}`{auto_part} — "
                     f"{rejection.rationale or 'no rationale recorded'}")
        if rejection.target_candidate_ids:
            rendered = ", ".join(
                f"`{c}`" for c in rejection.target_candidate_ids)
            lines.append(f"  - rejected candidates: {rendered}")
        if rejection.failed_checks:
            lines.append(f"  - failed checks: "
                         f"{', '.join(rejection.failed_checks)}")
        lines.append(f"  - decided by {rejection.decided_by_agent} / "
                     f"{rejection.decided_by_model} at "
                     f"{rejection.decided_at}")
    if pack.rejections:
        lines.append("")

    lines.append("## Supersession changelog")
    lines.append("")
    if not pack.supersessions:
        lines.append("None.")
        lines.append("")
    for event in pack.supersessions:
        effective = event.successor_valid_from or "unset"
        lines.append(f"- {effective}: `{event.superseded_entry_id}` → "
                     f"`{event.successor_entry_id}`")
        if event.statement_before:
            lines.append(f"  - before: \"{event.statement_before}\" "
                         f"(valid until "
                         f"{event.superseded_valid_until or 'unset'})")
        lines.append(f"  - after: \"{event.statement_after}\"")
    if pack.supersessions:
        lines.append("")

    lines.append("---")
    lines.append("")
    lines.append(f"Pack id: `{pack.id}`  ")
    lines.append(f"Schema version: `{pack.schema_version}`  ")
    lines.append(
        f"This pack describes the bundle whose fingerprint is "
        f"`{pack.bundle_fingerprint}`. Re-run on the same bundle with "
        f"the same as-of timestamp to reproduce it; the pack id is "
        f"content-derived and will only differ if the bundle or the "
        f"chains change.")
    return "\n".join(lines) + "\n"


__all__ = [
    "DEFAULT_AUDIT_TITLE",
    "AuditEvidenceRef",
    "AuditChainEntry",
    "AuditRejection",
    "AuditSupersession",
    "AuditPack",
    "compute_audit_pack",
    "audit_pack_to_markdown",
]
