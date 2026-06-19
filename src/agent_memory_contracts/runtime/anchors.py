"""ADR-6: the hash-chained audit anchors -- tamper evidence for the
store.

Every committed write batch (ingestion, promotion, pack build,
answer) appends one anchor row::

    seq | tenant_id | scope | fingerprint | prev_fingerprint
        | actor | kind | created_at

- ``scope`` is canonical JSON naming exactly what the fingerprint
  covers: the record ids written in the batch plus the
  supersession / status-override edge keys. Scopes make every
  anchor **replayable**: anyone can recompute the fingerprint from
  the immutable stored payloads at any later time.
- ``fingerprint`` is the library's :func:`bundle_fingerprint` over
  the scope's stored payloads (never the materialized read forms,
  which change as edges accrue) plus edge pseudo-records.
- ``prev_fingerprint`` chains each anchor to its predecessor
  (genesis links to ``"0" * 64``), so reordering or rewriting
  history breaks the chain.

Verification is two checks, both security-grade and both run by
the invariant suite:

- :func:`verify_chain` walks the chain and recomputes every
  anchor, returning the **first divergence**: a broken link, a
  fingerprint mismatch (edited payload), a missing record
  (deleted row), an unparseable scope, or a committed write-guard
  flag (someone armed the gate bypass by hand).
- :func:`verify_coverage` answers the complementary question --
  "is there anything in the trusted tables that **no anchor
  accounts for**?" A row inserted past the triggers without going
  through the gate is flagged here, as is drift in the derived
  edge indexes (which are reconstructible projections of anchored
  payloads).

Honest limitation, by design: a hash chain without an external
trust root cannot detect truncation of its own **tail** (dropping
the last N anchors *and* every row they cover). The product
publishes the chain head to an external store (ADR-6 nightly
``verified`` anchor + off-box copy); see
``docs/ROADMAP-to-product.md``.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from .._canonical import canonical_json
from ..bundles import bundle_fingerprint
from .store import MemoryStore, StoreError

#: The fingerprint the first anchor of a tenant chains to.
GENESIS_PREV = "0" * 64

#: Anchor kinds. ``write`` after every committed batch; ``verified``
#: after a full-tenant revalidation (ADR-4's global net).
ANCHOR_KINDS = ("write", "verified")

_DERIVED_EDGE_TABLES = ("decision_authorizations", "candidate_evidence",
                        "entry_evidence")


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AnchorReceipt:
    """One appended anchor: its chain position and fingerprint."""

    seq: int
    fingerprint: str
    kind: str


@dataclass(frozen=True)
class AnchorDivergence:
    """The first point at which the chain stops being trustworthy.

    Attributes:
        seq: The anchor sequence number at which verification
            failed (0 for pre-chain checks like the guard
            tripwire).
        kind: ``chain_link_broken`` | ``fingerprint_mismatch`` |
            ``missing_record`` | ``scope_invalid`` |
            ``guard_armed``.
        detail: Human-readable description (ids, expected vs
            stored fingerprints).
    """

    seq: int
    kind: str
    detail: str


@dataclass(frozen=True)
class ChainVerification:
    """Result of :func:`verify_chain`."""

    ok: bool
    anchors_checked: int
    divergence: AnchorDivergence | None


@dataclass(frozen=True)
class CoverageReport:
    """Result of :func:`verify_coverage`.

    Attributes:
        ok: True when every governed row is covered by some
            anchor's scope and the derived edge indexes match
            their payload-derived expectation.
        unanchored: Record ids / edge keys present in the store
            that no anchor accounts for (a silent write got past
            the gate).
        derived_index_drift: Human-readable findings where a
            derived edge index disagrees with the anchored
            payloads it is supposed to project.
    """

    ok: bool
    unanchored: tuple[str, ...]
    derived_index_drift: tuple[str, ...]


# ---------------------------------------------------------------------------
# Scopes
# ---------------------------------------------------------------------------


def make_scope(
    record_ids: Iterable[str],
    *,
    supersession_edges: Iterable[tuple[str, str, str]] = (),
    status_overrides: Iterable[tuple[str, str, str]] = (),
) -> dict[str, Any]:
    """Build a canonical anchor scope.

    Args:
        record_ids: ids of the plane-table rows the batch wrote.
        supersession_edges: ``(superseded_id, superseding_id,
            decision_id)`` edges the batch wrote.
        status_overrides: ``(entry_id, status, decision_id)``
            overrides the batch wrote.
    """
    return {
        "record_ids": sorted(set(record_ids)),
        "supersession_edges": sorted(
            [list(edge) for edge in supersession_edges]),
        "status_overrides": sorted(
            [list(edge) for edge in status_overrides]),
    }


def full_scope(store: MemoryStore) -> dict[str, Any]:
    """The whole-tenant scope, for ``verified`` anchors (ADR-4's
    nightly full revalidation)."""
    record_ids: list[str] = []
    for ids in store.governed_row_ids().values():
        record_ids.extend(ids)
    return make_scope(
        record_ids,
        supersession_edges=store.supersession_edges(),
        status_overrides=store.status_override_edges(),
    )


def _edge_key(kind: str, parts: Sequence[str]) -> str:
    return f"edge:{kind}:" + "->".join(parts)


def _edge_pseudo_records(scope: Mapping[str, Any]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for edge in scope.get("supersession_edges") or []:
        superseded, superseding, decision = (str(v) for v in edge)
        records.append({
            "id": _edge_key("supersession", (superseded, superseding)),
            "edge_kind": "supersession",
            "superseded_id": superseded,
            "superseding_id": superseding,
            "decision_id": decision,
        })
    for edge in scope.get("status_overrides") or []:
        entry_id, status, decision = (str(v) for v in edge)
        records.append({
            "id": _edge_key("status_override", (entry_id,)),
            "edge_kind": "status_override",
            "entry_id": entry_id,
            "status": status,
            "decision_id": decision,
        })
    return records


def compute_scope_fingerprint(
    store: MemoryStore, scope: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    """Recompute the fingerprint a scope should have right now.

    Returns ``(fingerprint, None)`` on success or ``(None,
    missing_record_id)`` when a scoped record no longer resolves
    (the deleted-row tamper case). Fingerprints cover **stored**
    payloads -- the immutable form -- so recomputation gives the
    same answer at any later time, regardless of supersessions
    that accrued since.
    """
    records: list[dict[str, Any]] = []
    for record_id in scope.get("record_ids") or []:
        rid = str(record_id)
        try:
            payload = store.stored_payload(rid)
        except StoreError:
            payload = None
        if payload is None:
            return None, rid
        records.append(payload)
    records.extend(_edge_pseudo_records(scope))
    return bundle_fingerprint(records), None


# ---------------------------------------------------------------------------
# Writing (gate-internal)
# ---------------------------------------------------------------------------


def append_anchor(
    store: MemoryStore,
    conn: sqlite3.Connection,
    *,
    scope: Mapping[str, Any],
    kind: str,
    actor: str,
    created_at: str,
) -> AnchorReceipt:
    """Append one anchor inside an already-armed gate transaction.

    Gate-internal: the caller holds the store's write transaction
    with the guard armed; the rows named by ``scope`` are already
    inserted (this function reads them back through the same
    connection to compute the fingerprint).
    """
    if kind not in ANCHOR_KINDS:
        raise ValueError(f"anchor kind must be one of {ANCHOR_KINDS},"
                         f" got {kind!r}")
    fingerprint, missing = compute_scope_fingerprint(store, scope)
    if fingerprint is None:
        raise StoreError(
            f"anchor scope references a record that is not in the store:"
            f" {missing} (gate bug: anchors must cover exactly what the"
            f" batch wrote)")
    prev_row = conn.execute(
        "SELECT fingerprint FROM audit_anchors WHERE tenant_id = ?"
        " ORDER BY seq DESC LIMIT 1", (store.tenant_id,)).fetchone()
    prev = str(prev_row[0]) if prev_row is not None else GENESIS_PREV
    cursor = conn.execute(
        "INSERT INTO audit_anchors (tenant_id, scope, fingerprint,"
        " prev_fingerprint, actor, kind, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (store.tenant_id,
         canonical_json(scope),
         fingerprint, prev, actor, kind, created_at))
    seq = int(cursor.lastrowid or 0)
    return AnchorReceipt(seq=seq, fingerprint=fingerprint, kind=kind)


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_chain(store: MemoryStore) -> ChainVerification:
    """Walk the tenant's anchor chain and recompute every anchor.

    Returns the first divergence, if any (ADR-6: ``GET
    /audit/verify``). The checks, in order per anchor: the
    committed write-guard tripwire (once, before the walk), the
    ``prev_fingerprint`` link, scope parseability, scoped-record
    existence, and the recomputed fingerprint.
    """
    if store.guard_committed_value() != 0:
        return ChainVerification(
            ok=False, anchors_checked=0,
            divergence=AnchorDivergence(
                seq=0, kind="guard_armed",
                detail="the write guard is armed in committed state: "
                       "something bypassed the gate"))
    prev = GENESIS_PREV
    checked = 0
    for anchor in store.list_anchors():
        seq = int(anchor["seq"])
        checked += 1
        if anchor["prev_fingerprint"] != prev:
            return ChainVerification(
                ok=False, anchors_checked=checked,
                divergence=AnchorDivergence(
                    seq=seq, kind="chain_link_broken",
                    detail=f"anchor {seq} links to "
                           f"{anchor['prev_fingerprint']!r} but the "
                           f"previous fingerprint is {prev!r}"))
        try:
            scope = json.loads(anchor["scope"])
        except ValueError:
            return ChainVerification(
                ok=False, anchors_checked=checked,
                divergence=AnchorDivergence(
                    seq=seq, kind="scope_invalid",
                    detail=f"anchor {seq} scope is not valid JSON"))
        if not isinstance(scope, dict):
            return ChainVerification(
                ok=False, anchors_checked=checked,
                divergence=AnchorDivergence(
                    seq=seq, kind="scope_invalid",
                    detail=f"anchor {seq} scope is not an object"))
        fingerprint, missing = compute_scope_fingerprint(store, scope)
        if fingerprint is None:
            return ChainVerification(
                ok=False, anchors_checked=checked,
                divergence=AnchorDivergence(
                    seq=seq, kind="missing_record",
                    detail=f"anchor {seq} covers {missing} which is no "
                           f"longer in the store"))
        if fingerprint != anchor["fingerprint"]:
            return ChainVerification(
                ok=False, anchors_checked=checked,
                divergence=AnchorDivergence(
                    seq=seq, kind="fingerprint_mismatch",
                    detail=f"anchor {seq} recomputes to {fingerprint} but "
                           f"stores {anchor['fingerprint']}"))
        prev = str(anchor["fingerprint"])
    return ChainVerification(ok=True, anchors_checked=checked,
                             divergence=None)


def _expected_derived_edges(store: MemoryStore,
                            table: str) -> list[tuple[str, ...]]:
    """Recompute a derived edge index from the stored payloads."""
    expected: list[tuple[str, ...]] = []
    if table == "decision_authorizations":
        for decision in store.list_decisions():
            decision_id = str(decision.get("id") or "")
            decision_type = str(decision.get("decision_type") or "")
            for candidate_id in decision.get("target_candidate_ids") or []:
                expected.append((decision_id, "candidate",
                                 str(candidate_id), decision_type))
            for entry_id in decision.get("target_ledger_entry_ids") or []:
                expected.append((decision_id, "ledger_entry",
                                 str(entry_id), decision_type))
    elif table == "candidate_evidence":
        for candidate in store.list_candidates():
            for span_id in candidate.get("evidence_span_ids") or []:
                expected.append((str(candidate.get("id") or ""),
                                 str(span_id)))
    elif table == "entry_evidence":
        for entry_id in store.governed_row_ids()["ledger_entries"]:
            stored = store.stored_payload(entry_id) or {}
            for span_id in stored.get("evidence_span_ids") or []:
                expected.append((entry_id, str(span_id)))
    else:  # pragma: no cover - defensive
        raise ValueError(f"not a derived edge table: {table}")
    return sorted(set(expected))


def verify_coverage(store: MemoryStore) -> CoverageReport:
    """Flag governed rows that no anchor accounts for.

    The gate anchors every committed batch, so the union of all
    anchor scopes must cover every plane row and every
    supersession / status-override edge. A row outside that union
    was written past the gate (e.g. by hand-arming the guard) --
    the silent write the triggers could not stop but the audit
    layer still catches. Derived edge indexes are additionally
    recomputed from the anchored payloads and compared.
    """
    covered_ids: set[str] = set()
    covered_supersessions: set[tuple[str, str, str]] = set()
    covered_overrides: set[tuple[str, str, str]] = set()
    for anchor in store.list_anchors():
        try:
            scope = json.loads(anchor["scope"])
        except ValueError:
            continue  # verify_chain reports unparseable scopes
        if not isinstance(scope, dict):
            continue
        covered_ids.update(str(r) for r in scope.get("record_ids") or [])
        for edge in scope.get("supersession_edges") or []:
            parts = [str(v) for v in edge]
            if len(parts) == 3:
                covered_supersessions.add((parts[0], parts[1], parts[2]))
        for edge in scope.get("status_overrides") or []:
            parts = [str(v) for v in edge]
            if len(parts) == 3:
                covered_overrides.add((parts[0], parts[1], parts[2]))

    unanchored: list[str] = []
    for table, ids in sorted(store.governed_row_ids().items()):
        for record_id in ids:
            if record_id not in covered_ids:
                unanchored.append(f"{table}:{record_id}")
    for edge in store.supersession_edges():
        if edge not in covered_supersessions:
            unanchored.append(
                "supersessions:" + _edge_key("supersession", edge[:2]))
    for override in store.status_override_edges():
        if override not in covered_overrides:
            unanchored.append(
                "status_overrides:"
                + _edge_key("status_override", override[:1]))

    drift: list[str] = []
    for table in _DERIVED_EDGE_TABLES:
        actual = sorted(set(store.derived_edge_rows(table)))
        expected = _expected_derived_edges(store, table)
        for row in actual:
            if row not in expected:
                drift.append(f"{table}: unexpected row {row!r}")
        for row in expected:
            if row not in actual:
                drift.append(f"{table}: missing row {row!r}")

    return CoverageReport(
        ok=not unanchored and not drift,
        unanchored=tuple(unanchored),
        derived_index_drift=tuple(drift),
    )


__all__ = [
    "GENESIS_PREV",
    "ANCHOR_KINDS",
    "AnchorReceipt",
    "AnchorDivergence",
    "ChainVerification",
    "CoverageReport",
    "make_scope",
    "full_scope",
    "compute_scope_fingerprint",
    "append_anchor",
    "verify_chain",
    "verify_coverage",
]
