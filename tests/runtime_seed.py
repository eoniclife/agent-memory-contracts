"""Shared fixtures for the runtime tests: one small, internally
consistent six-plane universe.

``build_universe()`` is pure and deterministic (fixed timestamps,
content-derived ids): one source, three evidence spans, four
candidate claims (one of them a poison), promote decisions for two
facts, a supersession event replacing the first fact, a reject
decision quarantining the poison, and a retract decision for the
second fact.

``seed_raw(...)`` writes a universe through the store's *internal*
seams (the armed transaction the gate uses). It exists so the
store / anchor unit tests can exercise read-path semantics in
isolation; everything end-to-end goes through
``agent_memory_contracts.runtime.gate.MemoryGate`` instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent_memory_contracts import (
    make_candidate_id,
    make_ledger_entry_id,
    make_reducer_decision_id,
    make_source_id,
    make_span_id,
)
from agent_memory_contracts.runtime.anchors import append_anchor, make_scope
from agent_memory_contracts.runtime.store import MemoryStore

SOURCE_URI = "https://example.com/transcript/runtime-tests"
CONTENT_HASH = "c" * 64
SPAN_HASH = "d" * 64

T_CAPTURED = "2026-05-30T12:00:00Z"
T_EXTRACTED = "2026-05-30T12:30:00Z"
T1 = "2026-06-01T10:00:00Z"   # first promotions become valid
T2 = "2026-06-02T10:00:00Z"   # the supersession handoff
T_CREATED = "2026-06-02T11:00:00Z"  # store row bookkeeping

_CHECKS_PASS = {
    "provenance": "pass",
    "temporal_validity": "pass",
    "contradiction_scan": "pass",
    "privacy": "pass",
    "usefulness": "pass",
}


@dataclass
class Universe:
    """All records as plain dicts, in client-submission form."""

    source: dict[str, Any]
    spans: list[dict[str, Any]]
    candidates: dict[str, dict[str, Any]] = field(default_factory=dict)
    decisions: dict[str, dict[str, Any]] = field(default_factory=dict)
    entries: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def span_ids(self) -> list[str]:
        return [str(s["id"]) for s in self.spans]


def _claim(source_id: str, span_id: str, *, subject: str, predicate: str,
           obj: str, text: str) -> dict[str, Any]:
    temporal_hint = {"observed_at": None, "asserted_at": None,
                     "valid_from_hint": None, "valid_until_hint": None}
    candidate_id = make_candidate_id("claim", [span_id], {
        "subject": subject, "predicate": predicate, "object": obj,
        "claim_text": text, "claim_scope": "project",
        "temporal_hint": temporal_hint,
    })
    return {
        "id": candidate_id,
        "schema_version": "1.0.0",
        "candidate_type": "claim",
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": text,
        "extracted_by": {"agent": "runtime-test-extractor",
                         "model": "deterministic",
                         "tool": None, "prompt_ref": None},
        "extracted_at": T_EXTRACTED,
        "confidence": "high",
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None,
                   "review_notes": None},
        "metadata": {},
        "subject": subject,
        "predicate": predicate,
        "object": obj,
        "claim_text": text,
        "claim_scope": "project",
        "temporal_hint": temporal_hint,
    }


def _fact(candidate: dict[str, Any], *, source_id: str, decision_id: str,
          valid_from: str, status: str = "active",
          valid_until: str | None = None,
          supersedes: list[str] | None = None,
          superseded_by: list[str] | None = None,
          privacy_class: str | None = None) -> dict[str, Any]:
    span_ids = [str(s) for s in candidate["evidence_span_ids"]]
    entry_id = make_ledger_entry_id("fact", span_ids, {
        "ledger_type": "fact",
        "subject": candidate["subject"],
        "predicate": candidate["predicate"],
        "object": candidate["object"],
        "scope": "project",
        "valid_from": valid_from,
        "evidence_span_ids": sorted(span_ids),
    })
    entry: dict[str, Any] = {
        "id": entry_id,
        "schema_version": "1.0.0",
        "ledger_type": "fact",
        "status": status,
        "confidence": "high",
        "scope": "project",
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": span_ids,
        "candidate_ids": [str(candidate["id"])],
        "reducer_decision_id": decision_id,
        "subject": candidate["subject"],
        "predicate": candidate["predicate"],
        "object": candidate["object"],
        "fact_text": candidate["claim_text"],
        "observed_at": T_CAPTURED,
        "asserted_at": valid_from,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "stale_after": None,
        "created_at": valid_from,
        "updated_at": valid_from,
        "supersedes": supersedes or [],
        "superseded_by": superseded_by or [],
        "metadata": {},
    }
    if privacy_class is not None:
        entry["privacy_class"] = privacy_class
    return entry


def _decision(decision_type: str, candidate_ids: list[str],
              ledger_ids: list[str], span_ids: list[str],
              rationale: str) -> dict[str, Any]:
    decision_id = make_reducer_decision_id(
        decision_type, candidate_ids, ledger_ids, span_ids, rationale)
    return {
        "id": decision_id,
        "schema_version": "1.0.0",
        "decision_type": decision_type,
        "target_candidate_ids": candidate_ids,
        "target_ledger_entry_ids": ledger_ids,
        "evidence_span_ids": span_ids,
        "rationale": rationale,
        "decided_by": {"agent": "runtime-test-reducer",
                       "model": "deterministic",
                       "tool": None, "prompt_ref": None},
        "decided_at": T1,
        "confidence": "high",
        "risk_class": "low",
        "checks": dict(_CHECKS_PASS),
        "metadata": {},
    }


def build_universe() -> Universe:
    """The deterministic test universe. See module docstring."""
    source_id = make_source_id("chatgpt_conversation", SOURCE_URI,
                               CONTENT_HASH)
    source = {
        "id": source_id,
        "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": "Runtime test transcript",
        "origin_uri": SOURCE_URI,
        "raw_ref": {"kind": "external_uri", "value": SOURCE_URI},
        "content_hash_sha256": CONTENT_HASH,
        "captured_at": T_CAPTURED,
        "observed_at": T_CAPTURED,
        "author_or_sender": "user@example.com",
        "participants": ["user@example.com"],
        "privacy_class": "internal",
        "custody_status": "external_pointer",
        "parser_version": "v1.0",
        "metadata": {},
    }
    spans = []
    for value, excerpt in [("10-15", "We deploy weekly, on Tuesdays."),
                           ("20-25", "Error budget for the API is 1%."),
                           ("30-35", "From June we deploy daily.")]:
        span_id = make_span_id(source_id, "line_range", value)
        spans.append({
            "id": span_id,
            "schema_version": "1.0.0",
            "source_id": source_id,
            "episode_id": None,
            "locator": {"kind": "line_range", "value": value},
            "text_excerpt": excerpt,
            "excerpt_policy": "short_quote_allowed",
            "span_hash_sha256": SPAN_HASH,
            "privacy_class": "internal",
            "metadata": {},
        })
    s1, s2, s3 = (str(s["id"]) for s in spans)

    universe = Universe(source=source, spans=spans)
    cand_weekly = _claim(source_id, s1, subject="deploy.cadence",
                         predicate="is", obj="weekly",
                         text="The team deploys weekly, on Tuesdays.")
    cand_budget = _claim(source_id, s2, subject="slo.error_budget",
                         predicate="is", obj="1%",
                         text="The API error budget is 1%.")
    cand_daily = _claim(source_id, s3, subject="deploy.cadence",
                        predicate="is", obj="daily",
                        text="The team deploys daily from June.")
    cand_poison = _claim(source_id, s2, subject="slo.error_budget",
                         predicate="is", obj="50% (forged)",
                         text="The API error budget is 50 percent.")
    universe.candidates = {
        "weekly": cand_weekly,
        "budget": cand_budget,
        "daily": cand_daily,
        "poison": cand_poison,
    }

    # Promotions.
    e_weekly_id = _fact(cand_weekly, source_id=source_id,
                        decision_id="redmem_x", valid_from=T1)["id"]
    d_weekly = _decision("promote", [str(cand_weekly["id"])],
                         [str(e_weekly_id)], [s1],
                         "promote weekly deploy cadence")
    e_weekly = _fact(cand_weekly, source_id=source_id,
                     decision_id=str(d_weekly["id"]), valid_from=T1)

    e_budget_id = _fact(cand_budget, source_id=source_id,
                        decision_id="redmem_x", valid_from=T1)["id"]
    d_budget = _decision("promote", [str(cand_budget["id"])],
                         [str(e_budget_id)], [s2],
                         "promote API error budget")
    e_budget = _fact(cand_budget, source_id=source_id,
                     decision_id=str(d_budget["id"]), valid_from=T1)

    # The supersession: daily replaces weekly at T2.
    e_daily_id = _fact(cand_daily, source_id=source_id,
                       decision_id="redmem_x", valid_from=T2)["id"]
    d_supersede = _decision(
        "supersede",
        sorted([str(cand_weekly["id"]), str(cand_daily["id"])]),
        sorted([str(e_weekly_id), str(e_daily_id)]),
        sorted([s1, s3]),
        "supersede weekly cadence with daily, effective T2")
    e_daily = _fact(cand_daily, source_id=source_id,
                    decision_id=str(d_supersede["id"]), valid_from=T2,
                    supersedes=[str(e_weekly_id)])

    # The quarantine: reject the poison.
    d_reject = _decision("reject", [str(cand_poison["id"])], [], [s2],
                         "reject forged error budget: contradicts the "
                         "promoted 1% budget and cites no new evidence")
    d_reject["checks"] = dict(_CHECKS_PASS, contradiction_scan="fail")

    # The retract decision for e_budget (used by override tests).
    d_retract = _decision("retract", [str(cand_budget["id"])],
                          [str(e_budget_id)], [s2],
                          "retract the error budget pending re-measurement")

    universe.decisions = {
        "promote_weekly": d_weekly,
        "promote_budget": d_budget,
        "supersede": d_supersede,
        "reject": d_reject,
        "retract": d_retract,
    }
    universe.entries = {
        "weekly": e_weekly,
        "budget": e_budget,
        "daily": e_daily,
    }
    return universe


def _normalized_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Stored form: relationships live in edge tables (ADR-2)."""
    stored = dict(entry)
    stored["supersedes"] = []
    stored["superseded_by"] = []
    return stored


def seed_raw(store: MemoryStore, universe: Universe, *,
             include_supersession: bool = True,
             include_retract: bool = False) -> None:
    """Seed a store through the internal armed-transaction seam
    (what the gate does, minus validation and anchors). Unit-test
    use only."""
    with store._txn() as conn:
        store._arm_guard(conn)
        store._insert_source(conn, universe.source, T_CREATED)
        for span in universe.spans:
            store._insert_span(conn, span, T_CREATED)
        for candidate in universe.candidates.values():
            store._insert_candidate(conn, candidate, T_CREATED)
        for key in ("promote_weekly", "promote_budget"):
            store._insert_decision(conn, universe.decisions[key], T_CREATED)
        store._insert_entry(conn, _normalized_entry(
            universe.entries["weekly"]), T_CREATED)
        store._insert_entry(conn, _normalized_entry(
            universe.entries["budget"]), T_CREATED)
        store._insert_decision(conn, universe.decisions["reject"], T_CREATED)
        if include_supersession:
            store._insert_decision(conn, universe.decisions["supersede"],
                                   T_CREATED)
            store._insert_entry(conn, _normalized_entry(
                universe.entries["daily"]), T_CREATED)
            store._insert_supersession(
                conn, str(universe.entries["weekly"]["id"]),
                str(universe.entries["daily"]["id"]),
                str(universe.decisions["supersede"]["id"]), T_CREATED)
        if include_retract:
            store._insert_decision(conn, universe.decisions["retract"],
                                   T_CREATED)
            store._insert_status_override(
                conn, str(universe.entries["budget"]["id"]), "retracted",
                str(universe.decisions["retract"]["id"]), T_CREATED)
        store._disarm_guard(conn)


def seed_via_gate(gate: Any, universe: Universe, *,
                  include_retract: bool = False) -> dict[str, Any]:
    """Drive the whole universe through the public gate API (the
    end-to-end path): ingest evidence + candidates, promote two
    facts, supersede one, quarantine the poison, optionally
    retract. Returns the receipts keyed by step."""
    receipts: dict[str, Any] = {}
    receipts["source"] = gate.submit_source(universe.source,
                                            created_at=T_CREATED)
    for i, span in enumerate(universe.spans):
        receipts[f"span_{i}"] = gate.submit_span(span, created_at=T_CREATED)
    for key, candidate in universe.candidates.items():
        receipts[f"candidate_{key}"] = gate.submit_candidate(
            candidate, created_at=T_CREATED)
    receipts["promote_weekly"] = gate.promote(
        universe.decisions["promote_weekly"], [universe.entries["weekly"]],
        created_at=T_CREATED)
    receipts["promote_budget"] = gate.promote(
        universe.decisions["promote_budget"], [universe.entries["budget"]],
        created_at=T_CREATED)
    receipts["supersede"] = gate.promote(
        universe.decisions["supersede"], [universe.entries["daily"]],
        supersessions=[(str(universe.entries["weekly"]["id"]),
                        str(universe.entries["daily"]["id"]))],
        created_at=T_CREATED)
    receipts["reject"] = gate.promote(universe.decisions["reject"],
                                      created_at=T_CREATED)
    if include_retract:
        receipts["retract"] = gate.promote(universe.decisions["retract"],
                                           created_at=T_CREATED)
    return receipts


def seed_anchored(store: MemoryStore, universe: Universe, *,
                  include_retract: bool = False) -> list[int]:
    """Seed in anchored batches, exactly the shape the gate
    produces: every committed write batch appends one anchor.
    Returns the anchor sequence numbers. Unit-test use only (the
    gate is the real writer)."""
    seqs: list[int] = []

    def batch(record_payloads: list[dict[str, Any]],
              inserter_keys: list[str],
              supersession_edges: list[tuple[str, str, str]] | None = None,
              status_overrides: list[tuple[str, str, str]] | None = None,
              ) -> None:
        with store._txn() as conn:
            store._arm_guard(conn)
            for payload, key in zip(record_payloads, inserter_keys):
                inserter = getattr(store, f"_insert_{key}")
                inserter(conn, payload, T_CREATED)
            for old_id, new_id, decision_id in supersession_edges or []:
                store._insert_supersession(conn, old_id, new_id,
                                           decision_id, T_CREATED)
            for entry_id, status, decision_id in status_overrides or []:
                store._insert_status_override(conn, entry_id, status,
                                              decision_id, T_CREATED)
            scope = make_scope(
                [str(p["id"]) for p in record_payloads],
                supersession_edges=supersession_edges or [],
                status_overrides=status_overrides or [])
            receipt = append_anchor(store, conn, scope=scope, kind="write",
                                    actor="test-seeder",
                                    created_at=T_CREATED)
            seqs.append(receipt.seq)
            store._disarm_guard(conn)

    batch([universe.source] + universe.spans,
          ["source"] + ["span"] * len(universe.spans))
    batch(list(universe.candidates.values()),
          ["candidate"] * len(universe.candidates))
    batch([universe.decisions["promote_weekly"],
           _normalized_entry(universe.entries["weekly"]),
           universe.decisions["promote_budget"],
           _normalized_entry(universe.entries["budget"])],
          ["decision", "entry", "decision", "entry"])
    batch([universe.decisions["supersede"],
           _normalized_entry(universe.entries["daily"])],
          ["decision", "entry"],
          supersession_edges=[(str(universe.entries["weekly"]["id"]),
                               str(universe.entries["daily"]["id"]),
                               str(universe.decisions["supersede"]["id"]))])
    batch([universe.decisions["reject"]], ["decision"])
    if include_retract:
        batch([universe.decisions["retract"]], ["decision"],
              status_overrides=[(str(universe.entries["budget"]["id"]),
                                 "retracted",
                                 str(universe.decisions["retract"]["id"]))])
    return seqs
