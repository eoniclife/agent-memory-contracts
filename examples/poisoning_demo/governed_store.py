"""Store B: the governed memory store, built on the real contracts.

The same extraction fixtures that Store A appends blindly are routed
through the library's planes here:

    document  -> SourceRecord + EvidenceSpan        (evidence plane)
    extraction -> CandidateClaim                    (candidate plane)
    reducer   -> MemoryReducerDecision + FactLedgerEntry
                                                    (trusted ledger)

Promotion follows the reference-reducer pattern
(``examples/reference_reducer.py``): one promote decision per
promoted candidate, bidirectionally linked to the ledger entry it
authorizes, and the whole graph checked by
``validate_ledger_bundle``.

On top of the reference reducer's checks, this store adds the two
governance checks the poisoning demo is about:

1. **Source trust tier.** A candidate whose source is not from a
   recognised sender domain is rejected with ``untrusted_source``.
   The forged memo fails here: the sender is a spoofed free-mail
   address, not the established counterparty identity.
2. **Authorizing chain.** A payout-policy candidate must cite an
   authorization reference that a governed document established.
   The forged memo cites nothing ("agreed verbally") and is
   rejected with ``no_authorizing_chain``.

A rejection is not a log line -- it is a real
``MemoryReducerDecision`` with ``decision_type="reject"``, a
rationale, and the evidence spans it judged. The candidate itself
stays quarantined in the candidate plane with
``status="candidate"``; nothing reaches the trusted ledger, so the
ledger's ``bundle_fingerprint`` is unchanged by the attack.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from agent_memory_contracts import (
    CandidateClaim,
    EvidenceSpan,
    FactLedgerEntry,
    MemoryReducerDecision,
    SourceRecord,
    bundle_fingerprint,
    make_candidate_id,
    make_ledger_entry_id,
    make_reducer_decision_id,
    make_source_id,
    make_span_id,
    sha256_hex,
    validate_ledger_bundle,
)

REJECT_UNTRUSTED_SOURCE = "untrusted_source"
REJECT_NO_AUTHORIZING_CHAIN = "no_authorizing_chain"

_CHECKS_PASS = {
    "provenance": "pass",
    "temporal_validity": "pass",
    "contradiction_scan": "pass",
    "privacy": "pass",
    "usefulness": "pass",
}


def _doc_to_source(doc: dict[str, Any]) -> SourceRecord:
    """Build a SourceRecord for a fixture document.

    The content hash is the real SHA-256 of the document body, so
    the source id is content-derived end to end.
    """
    content_hash = sha256_hex(doc["body"])
    uri = f"demo://poisoning/{doc['doc_key']}"
    source_id = make_source_id(doc["channel"], uri, content_hash)
    return SourceRecord.from_dict({
        "id": source_id,
        "schema_version": "1.0.0",
        "source_type": doc["channel"],
        "title": doc["title"],
        "origin_uri": uri,
        "raw_ref": {"kind": "synthetic_fixture", "value": uri},
        "content_hash_sha256": content_hash,
        "captured_at": doc["received_at"],
        "observed_at": doc["received_at"],
        "author_or_sender": doc["sender"],
        "participants": [doc["sender"]],
        "privacy_class": "internal",
        "custody_status": "synthetic",
        "parser_version": "poisoning-demo-v1",
        "metadata": {
            "sender_domain": doc["sender_domain"],
            "authorization_ref": doc["authorization_ref"],
        },
    })


def _span_for_anchor(source: SourceRecord, body: str, anchor: str) -> EvidenceSpan:
    """Build an EvidenceSpan whose locator is the (1-based) line of
    ``anchor`` in the document body. Deterministic: same document,
    same anchor, same span id."""
    for line_no, line in enumerate(body.splitlines(), start=1):
        if anchor in line:
            excerpt = line.strip()
            locator_value = f"{line_no}-{line_no}"
            break
    else:
        raise ValueError(f"anchor not found in document: {anchor!r}")
    span_id = make_span_id(source.id, "line_range", locator_value)
    return EvidenceSpan.from_dict({
        "id": span_id,
        "schema_version": "1.0.0",
        "source_id": source.id,
        "episode_id": None,
        "locator": {"kind": "line_range", "value": locator_value},
        "text_excerpt": excerpt,
        "excerpt_policy": "short_quote_allowed",
        "span_hash_sha256": sha256_hex(excerpt),
        "privacy_class": "internal",
        "metadata": {},
    })


def _extraction_to_candidate(
    row: dict[str, Any],
    source: SourceRecord,
    span: EvidenceSpan,
    *,
    extracted_at: str,
) -> CandidateClaim:
    """Turn one extraction fixture row into a CandidateClaim."""
    temporal_hint = {
        "observed_at": None, "asserted_at": None,
        "valid_from_hint": None, "valid_until_hint": None,
    }
    candidate_id = make_candidate_id("claim", [span.id], {
        "subject": row["subject"],
        "predicate": row["predicate"],
        "object": row["object"],
        "claim_text": row["claim_text"],
        "claim_scope": "company",
        "temporal_hint": temporal_hint,
    })
    return CandidateClaim.from_dict({
        "id": candidate_id,
        "schema_version": "1.0.0",
        "candidate_type": "claim",
        "source_record_ids": [source.id],
        "episode_record_ids": [],
        "evidence_span_ids": [span.id],
        "natural_language_summary": row["claim_text"],
        "extracted_by": {"agent": "fixture-extractor", "model": "deterministic",
                         "tool": None, "prompt_ref": None},
        "extracted_at": extracted_at,
        "confidence": row["confidence"],
        "risk_class": "medium",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {"authorization_ref": row["authorization_ref"]},
        "subject": row["subject"],
        "predicate": row["predicate"],
        "object": row["object"],
        "claim_text": row["claim_text"],
        "claim_scope": "company",
        "temporal_hint": temporal_hint,
    })


class GovernedMemoryStore:
    """The contracts-backed store. Same inputs as Store A; the
    difference is that nothing becomes trusted memory without a
    reducer decision."""

    def __init__(
        self,
        *,
        trusted_sender_domains: set[str],
        known_authorization_refs: set[str],
        decided_at: str,
    ) -> None:
        self.trusted_sender_domains = set(trusted_sender_domains)
        self.known_authorization_refs = set(known_authorization_refs)
        self.decided_at = decided_at
        self.sources: list[SourceRecord] = []
        self.spans: list[EvidenceSpan] = []
        self.candidates: list[CandidateClaim] = []
        self.decisions: list[MemoryReducerDecision] = []
        self.ledger: list[FactLedgerEntry] = []
        #: candidate_id -> (reason_code, reject MemoryReducerDecision)
        self.quarantine: dict[str, tuple[str, MemoryReducerDecision]] = {}

    # -- the two governance checks ------------------------------------

    def _check_trust_tier(self, source: SourceRecord) -> tuple[bool, str]:
        domain = str(source.metadata.get("sender_domain", ""))
        if domain in self.trusted_sender_domains:
            return True, ""
        return False, (
            f"sender domain {domain!r} is not an established "
            f"counterparty identity (trusted: "
            f"{sorted(self.trusted_sender_domains)})"
        )

    def _check_authorizing_chain(self, candidate: CandidateClaim) -> tuple[bool, str]:
        ref = candidate.metadata.get("authorization_ref")
        if isinstance(ref, str) and ref in self.known_authorization_refs:
            return True, ""
        return False, (
            f"payout-policy change cites no recognised authorization "
            f"reference (got {ref!r}; policy requires a written CFO+MD "
            f"reference)"
        )

    # -- ingest = evidence + candidate + reducer ----------------------

    def ingest(self, doc: dict[str, Any], extractions: list[dict[str, Any]],
               *, extracted_at: str) -> dict[str, int]:
        """Route a document through evidence -> candidate -> reducer.

        Returns ``{"promoted": n, "rejected": m}``. Rejected
        candidates stay in the candidate plane (quarantined) with a
        reject MemoryReducerDecision as the receipt.
        """
        source = _doc_to_source(doc)
        self.sources.append(source)
        promoted = rejected = 0
        for row in extractions:
            if row["doc_key"] != doc["doc_key"]:
                continue
            span = _span_for_anchor(source, doc["body"], row["anchor"])
            self.spans.append(span)
            candidate = _extraction_to_candidate(
                row, source, span, extracted_at=extracted_at)
            self.candidates.append(candidate)

            ok_tier, tier_msg = self._check_trust_tier(source)
            ok_auth, auth_msg = self._check_authorizing_chain(candidate)
            if ok_tier and ok_auth:
                self._promote(candidate, source, span)
                promoted += 1
            else:
                # Primary reason code is the first failed gate; the
                # rationale records every failure so the receipt
                # shows the full kill chain.
                reason = (REJECT_UNTRUSTED_SOURCE if not ok_tier
                          else REJECT_NO_AUTHORIZING_CHAIN)
                messages = [m for m in (tier_msg, auth_msg) if m]
                self._reject(candidate, span, reason=reason,
                             message="; ".join(messages),
                             failed_tier=not ok_tier,
                             failed_auth=not ok_auth)
                rejected += 1
        self.validate()
        return {"promoted": promoted, "rejected": rejected}

    def _promote(self, candidate: CandidateClaim, source: SourceRecord,
                 span: EvidenceSpan) -> None:
        entry_id = make_ledger_entry_id("fact", [span.id], {
            "ledger_type": "fact",
            "subject": candidate.subject,
            "predicate": candidate.predicate,
            "object": candidate.object,
            "scope": "company",
            "valid_from": self.decided_at,
            "evidence_span_ids": [span.id],
        })
        rationale = (
            f"trusted source tier and authorization ref "
            f"{candidate.metadata.get('authorization_ref')!r} verified; "
            f"grounded in span {span.id}"
        )
        decision_id = make_reducer_decision_id(
            "promote", [candidate.id], [entry_id], [span.id], rationale)
        decision = MemoryReducerDecision.from_dict({
            "id": decision_id,
            "schema_version": "1.0.0",
            "decision_type": "promote",
            "target_candidate_ids": [candidate.id],
            "target_ledger_entry_ids": [entry_id],
            "evidence_span_ids": [span.id],
            "rationale": rationale,
            "decided_by": {"agent": "governed-reducer", "model": "deterministic",
                           "tool": None, "prompt_ref": None},
            "decided_at": self.decided_at,
            "confidence": candidate.confidence,
            "risk_class": candidate.risk_class,
            "checks": dict(_CHECKS_PASS),
            "metadata": {},
        })
        entry = FactLedgerEntry.from_dict({
            "id": entry_id,
            "schema_version": "1.0.0",
            "ledger_type": "fact",
            "status": "active",
            "confidence": candidate.confidence,
            "scope": "company",
            "source_record_ids": [source.id],
            "episode_record_ids": [],
            "evidence_span_ids": [span.id],
            "candidate_ids": [candidate.id],
            "reducer_decision_id": decision_id,
            "subject": candidate.subject,
            "predicate": candidate.predicate,
            "object": candidate.object,
            "fact_text": candidate.claim_text,
            "observed_at": source.observed_at,
            "asserted_at": self.decided_at,
            "valid_from": self.decided_at,
            "valid_until": None,
            "stale_after": None,
            "created_at": self.decided_at,
            "updated_at": self.decided_at,
            "supersedes": [],
            "superseded_by": [],
            "metadata": {},
        })
        self.decisions.append(decision)
        self.ledger.append(entry)

    def _reject(self, candidate: CandidateClaim, span: EvidenceSpan, *,
                reason: str, message: str, failed_tier: bool,
                failed_auth: bool) -> None:
        rationale = f"{reason}: {message}"
        decision_id = make_reducer_decision_id(
            "reject", [candidate.id], [], [span.id], rationale)
        checks = dict(_CHECKS_PASS)
        if failed_tier:
            checks["provenance"] = "fail"
        if failed_auth:
            checks["contradiction_scan"] = "fail"
        decision = MemoryReducerDecision.from_dict({
            "id": decision_id,
            "schema_version": "1.0.0",
            "decision_type": "reject",
            "target_candidate_ids": [candidate.id],
            "target_ledger_entry_ids": [],
            "evidence_span_ids": [span.id],
            "rationale": rationale,
            "decided_by": {"agent": "governed-reducer", "model": "deterministic",
                           "tool": None, "prompt_ref": None},
            "decided_at": self.decided_at,
            "confidence": "high",
            "risk_class": "high",
            "checks": checks,
            "metadata": {"reason_code": reason, "auto_rejected": True},
        })
        self.decisions.append(decision)
        self.quarantine[candidate.id] = (reason, decision)

    # -- read side -----------------------------------------------------

    def retrieve(self, query: str) -> FactLedgerEntry | None:
        """Answer from the trusted ledger only. Candidates --
        including quarantined ones -- are not queryable memory."""
        import re
        q = set(re.findall(r"[a-z0-9.%]+", query.lower()))
        best: FactLedgerEntry | None = None
        best_key = (-1, "")
        for entry in self.ledger:
            if entry.status != "active":
                continue
            text = f"{entry.subject} {entry.fact_text}".lower()
            overlap = len(q & set(re.findall(r"[a-z0-9.%]+", text)))
            if overlap > 0 and (overlap, entry.id) > best_key:
                best_key = (overlap, entry.id)
                best = entry
        return best

    def trusted_fingerprint(self) -> str:
        """Content-addressed digest of the trusted ledger plane."""
        return bundle_fingerprint([asdict(e) for e in self.ledger])

    def validate(self) -> None:
        """Run the library's bundle validator over the whole graph."""
        validate_ledger_bundle(
            source_records=[asdict(s) for s in self.sources],
            episode_records=[],
            evidence_spans=[asdict(s) for s in self.spans],
            candidate_records=[asdict(c) for c in self.candidates],
            reducer_decisions=[asdict(d) for d in self.decisions],
            ledger_entries=[asdict(e) for e in self.ledger],
        )

    def all_records(self) -> list[dict[str, Any]]:
        """Every record in every plane, as dicts (for reports)."""
        out: list[dict[str, Any]] = []
        for group in (self.sources, self.spans, self.candidates,
                      self.decisions, self.ledger):
            out.extend(asdict(r) for r in group)
        return out
