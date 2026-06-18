"""ADR-4/5: the validation gate -- the only write path into memory.

Two write surfaces, nothing else:

- **Ingestion** (``submit_source`` / ``submit_episode`` /
  ``submit_span`` / ``submit_candidate``): individually
  transactional and idempotent (ADR-1 -- the server recomputes
  every content-derived id by parsing the payload through the
  contracts classes, whose ``validate()`` recomputes the id from
  canonical content; canonicalization is never reimplemented
  here). Re-submitting the same payload is a no-op
  (``created=False``); the same id with different content is a
  structured conflict.
- **Promotion** (``promote``): the single transactional endpoint
  of ADR-5. One call carries a reducer decision + its new ledger
  entries + its supersession declarations, validated as **one
  closure** (ADR-4) and committed in **one transaction** with an
  ADR-6 audit anchor. There is deliberately no public API that
  writes a ledger entry outside ``promote()`` -- the API surface
  itself makes silent writes impossible. ``reject`` decisions
  (quarantines) and ``retract`` / ``contest`` / ``archive``
  decisions (status overrides) flow through the same single
  endpoint, so every change to trusted memory is a decision.

The closure-load algorithm (ADR-4), per write W:

1. Seed set = W's records (the decision + the new entries +
   both sides of every declared supersession).
2. Add every record W references by id: candidates, spans,
   sources, episodes, ledger entries (incl. supersession
   targets).
3. For each ledger entry added, add its authorizing decision and
   that decision's referenced candidates / spans / target
   entries (the hop needed for authorization and reciprocity
   checks).
4. For each supersession target, add its existing supersession
   edges (materialized).
5. Validate the closure with the library
   (``validate_ledger_bundle``). Reject with the validator
   errors **verbatim** (mapped to 422 by an HTTP runtime).

Steps 2-4 run to a fixpoint over the connected component --
bounded, deterministic, testable. Closure entries are
materialized **hypothetically**: the declared (not yet committed)
edges and overrides are merged via the same
:func:`~agent_memory_contracts.runtime.store.materialize_entry`
the read path uses, so validation sees exactly what readers will
see after commit.

Concurrency (ADR-5): the whole of ``promote()`` runs inside one
``BEGIN IMMEDIATE`` transaction (sqlite's writer lock standing in
for ``SELECT ... FOR SHARE``); the supersessions primary key and
the ``uq_single_promotion`` partial unique index are the database-
level backstops. Concurrent conflicting promotions: first commit
wins, the second gets a structured :class:`ConflictError` carrying
the winning decision id.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from ..candidate_contracts import (
    candidate_from_dict,
    validate_candidate_bundle,
)
from ..ledger_contracts import (
    MemoryReducerDecision,
    ledger_entry_from_dict,
    reducer_decision_from_dict,
    validate_ledger_bundle,
)
from ..evidence_contracts import EpisodeRecord, EvidenceSpan, SourceRecord
from .anchors import AnchorReceipt, append_anchor, full_scope, make_scope
from .store import (
    _STATUS_BY_OVERRIDE_DECISION as STATUS_BY_OVERRIDE_DECISION,
    ConflictError,
    IdMismatchError,
    MemoryStore,
    StoreError,
    ValidationRejectedError,
    canonical_json,
    materialize_entry,
)

#: Every contracts ``validate()`` reports a recomputed-id failure
#: with this marker; the gate maps it to the structured ADR-1
#: ``id_mismatch`` error. Parsing the library's message keeps the
#: id derivation in exactly one place (the library).
_ID_MISMATCH_MARKER = " id mismatch: expected "

_GATE_ACTOR_DEFAULT = "runtime-gate"


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _raise_structured(client_id: str, exc: ValueError) -> None:
    """Map a contracts ``ValueError`` to the structured gate error:
    ``IdMismatchError`` for recomputed-id failures, otherwise
    ``ValidationRejectedError`` with the message verbatim."""
    text = str(exc)
    if _ID_MISMATCH_MARKER in text:
        expected = text.split(_ID_MISMATCH_MARKER, 1)[1].strip()
        raise IdMismatchError(expected=expected, got=client_id) from exc
    raise ValidationRejectedError((text,)) from exc


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IngestReceipt:
    """One idempotent ingestion write (ADR-1).

    Attributes:
        record_id: The server-verified content-derived id.
        created: False when the identical payload was already
            stored (the idempotent no-op).
        anchor_seq: The ADR-6 anchor covering this write; None
            for no-ops (nothing was written, nothing to anchor).
    """

    record_id: str
    created: bool
    anchor_seq: int | None


@dataclass(frozen=True)
class PromoteReceipt:
    """One committed (or idempotently replayed) promotion batch.

    Attributes:
        decision_id: The authorizing reducer decision.
        entry_ids: Ledger entries carried by the batch.
        supersessions: ``(superseded_id, superseding_id)`` edges
            declared by the batch.
        status_overrides: ``(entry_id, status)`` overrides applied
            by the batch (retract / contest / archive).
        created: False when the identical batch was already
            committed (the idempotent replay).
        anchor_seq: The ADR-6 anchor covering this batch; None
            for replays.
        closure_size: How many records the ADR-4 validation
            closure loaded (visibility into the bounded-closure
            property).
    """

    decision_id: str
    entry_ids: tuple[str, ...]
    supersessions: tuple[tuple[str, str], ...]
    status_overrides: tuple[tuple[str, str], ...]
    created: bool
    anchor_seq: int | None
    closure_size: int


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------


class MemoryGate:
    """The validated write path for a :class:`MemoryStore`.

    Args:
        store: The store to write through.
        actor: Recorded on every audit anchor this gate appends.
    """

    def __init__(self, store: MemoryStore, *,
                 actor: str = _GATE_ACTOR_DEFAULT) -> None:
        self.store = store
        self.actor = actor

    # -- ingestion (ADR-1: idempotent, server-verified ids) ------------------

    def submit_source(self, payload: Mapping[str, Any], *,
                      created_at: str | None = None) -> IngestReceipt:
        """Ingest a ``SourceRecord``. Idempotent."""
        data = dict(payload)
        client_id = str(data.get("id") or "")
        try:
            record = SourceRecord.from_dict(data)
        except ValueError as exc:
            _raise_structured(client_id, exc)
        return self._ingest(asdict(record), "_insert_source", "sources",
                            created_at)

    def submit_episode(self, payload: Mapping[str, Any], *,
                       created_at: str | None = None) -> IngestReceipt:
        """Ingest an ``EpisodeRecord``. Idempotent. Rejects when
        the referenced source is not in the store."""
        data = dict(payload)
        client_id = str(data.get("id") or "")
        try:
            record = EpisodeRecord.from_dict(data)
        except ValueError as exc:
            _raise_structured(client_id, exc)
        if self.store.get_source(record.source_id) is None:
            raise ValidationRejectedError(
                (f"dangling source_id: {record.source_id}",))
        return self._ingest(asdict(record), "_insert_episode", "episodes",
                            created_at)

    def submit_span(self, payload: Mapping[str, Any], *,
                    created_at: str | None = None) -> IngestReceipt:
        """Ingest an ``EvidenceSpan``. Idempotent. Rejects when the
        referenced source (or episode) is not in the store."""
        data = dict(payload)
        client_id = str(data.get("id") or "")
        try:
            record = EvidenceSpan.from_dict(data)
        except ValueError as exc:
            _raise_structured(client_id, exc)
        if self.store.get_source(record.source_id) is None:
            raise ValidationRejectedError(
                (f"dangling source_id: {record.source_id}",))
        if (record.episode_id is not None
                and self.store.get_episode(record.episode_id) is None):
            raise ValidationRejectedError(
                (f"dangling episode_id: {record.episode_id}",))
        return self._ingest(asdict(record), "_insert_span", "spans",
                            created_at)

    def submit_candidate(self, payload: Mapping[str, Any], *,
                         created_at: str | None = None) -> IngestReceipt:
        """Ingest a candidate (any of the five candidate types).
        Idempotent. The candidate plus its referenced evidence is
        validated with the library's ``validate_candidate_bundle``;
        dangling references reject with the validator message
        verbatim."""
        data = dict(payload)
        client_id = str(data.get("id") or "")
        try:
            record = candidate_from_dict(data)
        except ValueError as exc:
            _raise_structured(client_id, exc)
        canonical_form = asdict(record)
        sources = [s for s in
                   (self.store.get_source(sid)
                    for sid in record.source_record_ids) if s is not None]
        episodes = [e for e in
                    (self.store.get_episode(eid)
                     for eid in record.episode_record_ids) if e is not None]
        span_ids: list[str] = list(record.evidence_span_ids)
        for key in ("example_span_ids", "contrast_span_ids",
                    "counterevidence_span_ids"):
            span_ids.extend(str(s) for s in canonical_form.get(key) or [])
        spans = [s for s in
                 (self.store.get_span(sid) for sid in dict.fromkeys(span_ids))
                 if s is not None]
        try:
            validate_candidate_bundle(
                source_records=sources,
                episode_records=episodes,
                evidence_spans=spans,
                candidate_records=[canonical_form],
            )
        except ValueError as exc:
            raise ValidationRejectedError((str(exc),)) from exc
        return self._ingest(canonical_form, "_insert_candidate",
                            "candidates", created_at)

    def _ingest(self, canonical_form: dict[str, Any], inserter_name: str,
                table: str, created_at: str | None) -> IngestReceipt:
        record_id = str(canonical_form["id"])
        existing = self.store.stored_payload(record_id)
        if existing is not None:
            if canonical_json(existing) != canonical_json(canonical_form):
                raise ConflictError(
                    conflict_kind="payload_mismatch", record_id=record_id,
                    message=f"{table} row {record_id} already exists with "
                            f"different content")
            return IngestReceipt(record_id=record_id, created=False,
                                 anchor_seq=None)
        stamp = created_at or _utc_now()
        with self.store._txn() as conn:
            self.store._arm_guard(conn)
            inserter = getattr(self.store, inserter_name)
            inserter(conn, canonical_form, stamp)
            anchor = append_anchor(
                self.store, conn, scope=make_scope([record_id]),
                kind="write", actor=self.actor, created_at=stamp)
            self.store._disarm_guard(conn)
        return IngestReceipt(record_id=record_id, created=True,
                             anchor_seq=anchor.seq)

    # -- promotion (ADR-4 closure + ADR-5 single transaction) ----------------

    def promote(
        self,
        decision: Mapping[str, Any],
        entries: Iterable[Mapping[str, Any]] = (),
        supersessions: Iterable[tuple[str, str]] = (),
        *,
        created_at: str | None = None,
    ) -> PromoteReceipt:
        """Commit one decision + its entries + its supersessions as
        one validated unit. The ONLY way trusted memory changes.

        Args:
            decision: The ``MemoryReducerDecision`` payload
                (any decision_type: promote / supersede / reject /
                retract / contest / archive).
            entries: New ledger-entry payloads carried by the
                decision (promote / supersede only).
            supersessions: ``(superseded_id, superseding_id)``
                declarations (supersede only). Successors must be
                carried by this batch; targets may be existing
                entries or batch entries (the both-at-once case).
            created_at: Row/anchor bookkeeping timestamp (defaults
                to wall clock; pass a fixed value for reproducible
                pipelines).

        Raises:
            IdMismatchError: a content-derived id does not match
                the server's recomputation (ADR-1).
            ValidationRejectedError: the closure failed a library
                validator (errors verbatim) or the batch shape is
                inconsistent with the decision type.
            ConflictError: structured 409 -- double promotion,
                double supersession, double override, or an
                id reused with different content. First commit
                wins; the error names the winning decision.
        """
        decision_data = dict(decision)
        client_id = str(decision_data.get("id") or "")
        try:
            decision_rec = reducer_decision_from_dict(decision_data)
        except ValueError as exc:
            _raise_structured(client_id, exc)
        parsed_entries: list[dict[str, Any]] = []
        for raw in entries:
            data = dict(raw)
            entry_client_id = str(data.get("id") or "")
            try:
                entry_rec = ledger_entry_from_dict(data)
            except ValueError as exc:
                _raise_structured(entry_client_id, exc)
            parsed_entries.append(asdict(entry_rec))
        edges = [(str(old), str(new)) for old, new in supersessions]
        self._check_batch_shape(decision_rec, parsed_entries, edges)
        overrides = [
            (target, STATUS_BY_OVERRIDE_DECISION[decision_rec.decision_type])
            for target in decision_rec.target_ledger_entry_ids
        ] if decision_rec.decision_type in STATUS_BY_OVERRIDE_DECISION else []
        stamp = created_at or _utc_now()
        try:
            with self.store._txn() as conn:
                self.store._arm_guard(conn)
                receipt = self._promote_in_txn(
                    conn, decision_rec, parsed_entries, edges, overrides,
                    stamp)
                self.store._disarm_guard(conn)
            return receipt
        except sqlite3.IntegrityError as exc:
            # A true cross-process race slipped past the in-txn
            # checks and hit a uniqueness backstop. The transaction
            # rolled back; re-running resolves deterministically
            # against the winner's committed state (idempotent
            # replay or a structured conflict).
            raise self._conflict_after_race(decision_rec, edges,
                                            overrides, exc)

    def _check_batch_shape(self, decision_rec: MemoryReducerDecision,
                           parsed_entries: Sequence[Mapping[str, Any]],
                           edges: Sequence[tuple[str, str]]) -> None:
        """Decision-type / batch coherence (cheap, structural;
        everything semantic is the library validator's job)."""
        errors: list[str] = []
        decision_type = decision_rec.decision_type
        entry_ids = {str(e["id"]) for e in parsed_entries}
        if decision_type == "promote":
            if not parsed_entries:
                errors.append("promote batches must carry at least one "
                              "ledger entry")
            if edges:
                errors.append("promote batches must not declare "
                              "supersessions")
        elif decision_type == "supersede":
            if not edges:
                errors.append("supersede batches must declare at least one "
                              "supersession")
        else:
            if parsed_entries:
                errors.append(f"{decision_type} batches must not carry "
                              f"ledger entries")
            if edges:
                errors.append(f"{decision_type} batches must not declare "
                              f"supersessions")
        if decision_type in STATUS_BY_OVERRIDE_DECISION and (
                not decision_rec.target_ledger_entry_ids):
            errors.append(f"{decision_type} decisions must target at least "
                          f"one ledger entry")
        seen_old: set[str] = set()
        for old_id, new_id in edges:
            if old_id == new_id:
                errors.append(f"supersession of {old_id} by itself")
            if old_id in seen_old:
                errors.append(f"duplicate supersession target: {old_id}")
            seen_old.add(old_id)
            if new_id not in entry_ids and not self._is_replay_successor(
                    new_id, decision_rec.id):
                errors.append(
                    f"supersession successor {new_id} is not carried by "
                    f"this batch")
        for entry in parsed_entries:
            if str(entry["id"]) not in set(
                    decision_rec.target_ledger_entry_ids):
                errors.append(
                    f"batch entry {entry['id']} is not targeted by the "
                    f"decision")
        if errors:
            raise ValidationRejectedError(tuple(errors))

    def _is_replay_successor(self, entry_id: str, decision_id: str) -> bool:
        stored = self.store.stored_payload(entry_id) if (
            entry_id.startswith(("fact_", "pref_", "dec_"))) else None
        return (stored is not None
                and str(stored.get("reducer_decision_id")) == decision_id)

    @staticmethod
    def _normalized_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
        """Stored form: relationships live in edge tables (ADR-2),
        so supersession lists are persisted empty and rebuilt at
        read from the edges."""
        stored = dict(entry)
        stored["supersedes"] = []
        stored["superseded_by"] = []
        return stored

    def _promote_in_txn(
        self,
        conn: sqlite3.Connection,
        decision_rec: MemoryReducerDecision,
        parsed_entries: Sequence[dict[str, Any]],
        edges: Sequence[tuple[str, str]],
        overrides: Sequence[tuple[str, str]],
        stamp: str,
    ) -> PromoteReceipt:
        store = self.store
        decision_payload = asdict(decision_rec)
        decision_id = decision_rec.id

        # Idempotent replay (ADR-1): the same batch is a no-op,
        # the same decision id with different content is a 409.
        existing_decision = store.get_decision(decision_id)
        if existing_decision is not None:
            if (canonical_json(existing_decision)
                    != canonical_json(decision_payload)):
                raise ConflictError(
                    conflict_kind="decision_id_exists",
                    record_id=decision_id,
                    message=f"decision {decision_id} already exists with "
                            f"different content")
            self._verify_replay(parsed_entries, edges, overrides,
                                decision_id)
            return PromoteReceipt(
                decision_id=decision_id,
                entry_ids=tuple(str(e["id"]) for e in parsed_entries),
                supersessions=tuple(edges),
                status_overrides=tuple(overrides),
                created=False, anchor_seq=None, closure_size=0)

        # Structured conflicts (ADR-5: first commit wins).
        if decision_rec.decision_type == "promote":
            for candidate_id in decision_rec.target_candidate_ids:
                winner = store.promoting_decision_for(candidate_id)
                if winner is not None and winner != decision_id:
                    raise ConflictError(
                        conflict_kind="candidate_already_promoted",
                        record_id=candidate_id,
                        winning_decision_id=winner)
        existing_super = {old: (new, dec) for old, new, dec
                          in store.supersession_edges()}
        existing_over = {entry: (status, dec) for entry, status, dec
                         in store.status_override_edges()}
        for old_id, _new_id in edges:
            if old_id in existing_super:
                raise ConflictError(
                    conflict_kind="entry_already_superseded",
                    record_id=old_id,
                    winning_decision_id=existing_super[old_id][1])
            if old_id in existing_over:
                raise ConflictError(
                    conflict_kind="entry_status_overridden",
                    record_id=old_id,
                    winning_decision_id=existing_over[old_id][1])
        for target_id, _status in overrides:
            if target_id in existing_super:
                raise ConflictError(
                    conflict_kind="entry_already_superseded",
                    record_id=target_id,
                    winning_decision_id=existing_super[target_id][1])
            if target_id in existing_over:
                raise ConflictError(
                    conflict_kind="entry_status_overridden",
                    record_id=target_id,
                    winning_decision_id=existing_over[target_id][1])

        new_entries: list[dict[str, Any]] = []
        for entry in parsed_entries:
            entry_id = str(entry["id"])
            stored = store.stored_payload(entry_id)
            normalized = self._normalized_entry(entry)
            if stored is not None:
                if canonical_json(stored) != canonical_json(normalized):
                    raise ConflictError(
                        conflict_kind="payload_mismatch",
                        record_id=entry_id,
                        message=f"ledger entry {entry_id} already exists "
                                f"with different content")
                continue  # identical row already present; no re-insert
            new_entries.append(normalized)

        # ADR-4: load + validate the closure against the
        # hypothetical post-commit state.
        closure_size = self._validate_closure(
            decision_payload, parsed_entries, edges, overrides)

        # Commit the batch.
        store._insert_decision(conn, decision_payload, stamp)
        for entry in new_entries:
            store._insert_entry(conn, entry, stamp)
        edge_rows = [(old, new, decision_id) for old, new in edges]
        for old_id, new_id, dec in edge_rows:
            store._insert_supersession(conn, old_id, new_id, dec, stamp)
        override_rows = [(target, status, decision_id)
                         for target, status in overrides]
        for target_id, status, dec in override_rows:
            store._insert_status_override(conn, target_id, status, dec,
                                          stamp)
        anchor = append_anchor(
            self.store, conn,
            scope=make_scope(
                [decision_id] + [str(e["id"]) for e in new_entries],
                supersession_edges=edge_rows,
                status_overrides=override_rows),
            kind="write", actor=self.actor, created_at=stamp)
        return PromoteReceipt(
            decision_id=decision_id,
            entry_ids=tuple(str(e["id"]) for e in parsed_entries),
            supersessions=tuple(edges),
            status_overrides=tuple(overrides),
            created=True, anchor_seq=anchor.seq,
            closure_size=closure_size)

    def _verify_replay(self, parsed_entries: Sequence[dict[str, Any]],
                       edges: Sequence[tuple[str, str]],
                       overrides: Sequence[tuple[str, str]],
                       decision_id: str) -> None:
        """A replayed batch must match what the original committed."""
        for entry in parsed_entries:
            entry_id = str(entry["id"])
            stored = self.store.stored_payload(entry_id)
            normalized = self._normalized_entry(entry)
            if stored is None or (canonical_json(stored)
                                  != canonical_json(normalized)):
                raise ConflictError(
                    conflict_kind="payload_mismatch", record_id=entry_id,
                    message=f"replay of decision {decision_id} carries "
                            f"entry {entry_id} that does not match the "
                            f"committed batch")
        committed_edges = {(old, new) for old, new, dec
                           in self.store.supersession_edges()
                           if dec == decision_id}
        if set(edges) != committed_edges:
            raise ConflictError(
                conflict_kind="payload_mismatch", record_id=decision_id,
                message=f"replay of decision {decision_id} declares "
                        f"different supersessions than the committed batch")
        committed_overrides = {(entry, status) for entry, status, dec
                               in self.store.status_override_edges()
                               if dec == decision_id}
        if set(overrides) != committed_overrides:
            raise ConflictError(
                conflict_kind="payload_mismatch", record_id=decision_id,
                message=f"replay of decision {decision_id} declares "
                        f"different status overrides than the committed "
                        f"batch")

    # -- the ADR-4 closure ----------------------------------------------------

    def _validate_closure(
        self,
        decision_payload: dict[str, Any],
        parsed_entries: Sequence[dict[str, Any]],
        edges: Sequence[tuple[str, str]],
        overrides: Sequence[tuple[str, str]],
    ) -> int:
        store = self.store
        decision_id = str(decision_payload["id"])

        # The hypothetical post-commit graph.
        batch_entries = {str(e["id"]): self._normalized_entry(e)
                         for e in parsed_entries}
        combined_super: dict[str, tuple[str, str]] = {
            old: (new, dec) for old, new, dec in store.supersession_edges()}
        for old_id, new_id in edges:
            combined_super[old_id] = (new_id, decision_id)
        combined_super_rev: dict[str, list[str]] = {}
        for old_id, (new_id, _dec) in combined_super.items():
            combined_super_rev.setdefault(new_id, []).append(old_id)
        combined_over: dict[str, tuple[str, str]] = {
            entry: (status, dec) for entry, status, dec
            in store.status_override_edges()}
        for target_id, status in overrides:
            combined_over[target_id] = (status, decision_id)

        def stored_entry(entry_id: str) -> dict[str, Any] | None:
            if entry_id in batch_entries:
                return batch_entries[entry_id]
            return store._stored_payload("ledger_entries", entry_id)

        def materialize_hypothetical(
                payload: dict[str, Any]) -> dict[str, Any]:
            entry_id = str(payload.get("id") or "")
            superseded_edge = combined_super.get(entry_id)
            successor_valid_from: str | None = None
            if superseded_edge is not None and (
                    payload.get("valid_until") is None):
                successor = stored_entry(superseded_edge[0])
                if successor is not None:
                    value = successor.get("valid_from")
                    successor_valid_from = (str(value)
                                            if value is not None else None)
            return materialize_entry(
                payload,
                supersedes_edges=sorted(
                    combined_super_rev.get(entry_id, [])),
                superseded_edge=superseded_edge,
                override=combined_over.get(entry_id),
                successor_valid_from=successor_valid_from)

        sources: dict[str, dict[str, Any]] = {}
        episodes: dict[str, dict[str, Any]] = {}
        spans: dict[str, dict[str, Any]] = {}
        candidates: dict[str, dict[str, Any]] = {}
        decisions: dict[str, dict[str, Any]] = {}
        closure_entries: dict[str, dict[str, Any]] = {}

        worklist: list[tuple[str, str]] = [("decision", decision_id)]
        for entry in parsed_entries:
            worklist.append(("entry", str(entry["id"])))
        for old_id, new_id in edges:
            worklist.append(("entry", old_id))
            worklist.append(("entry", new_id))

        def push_refs(kind: str, ids: Any) -> None:
            if not isinstance(ids, (list, tuple)):
                ids = [ids] if ids else []
            for value in ids:
                if value:
                    worklist.append((kind, str(value)))

        while worklist:
            kind, record_id = worklist.pop()
            if kind == "entry":
                if record_id in closure_entries:
                    continue
                payload = stored_entry(record_id)
                if payload is None:
                    continue  # validator reports the dangling reference
                materialized = materialize_hypothetical(payload)
                closure_entries[record_id] = materialized
                push_refs("decision",
                          [materialized.get("reducer_decision_id")])
                push_refs("candidate", materialized.get("candidate_ids"))
                push_refs("span", materialized.get("evidence_span_ids"))
                push_refs("source", materialized.get("source_record_ids"))
                push_refs("episode", materialized.get("episode_record_ids"))
                push_refs("entry", materialized.get("supersedes"))
                push_refs("entry", materialized.get("superseded_by"))
            elif kind == "decision":
                if not record_id or record_id in decisions:
                    continue
                payload = (decision_payload if record_id == decision_id
                           else store.get_decision(record_id))
                if payload is None:
                    continue
                decisions[record_id] = payload
                push_refs("candidate", payload.get("target_candidate_ids"))
                push_refs("entry", payload.get("target_ledger_entry_ids"))
                push_refs("span", payload.get("evidence_span_ids"))
            elif kind == "candidate":
                if record_id in candidates:
                    continue
                payload = store.get_candidate(record_id)
                if payload is None:
                    continue
                candidates[record_id] = payload
                push_refs("span", payload.get("evidence_span_ids"))
                push_refs("source", payload.get("source_record_ids"))
                push_refs("episode", payload.get("episode_record_ids"))
            elif kind == "span":
                if record_id in spans:
                    continue
                payload = store.get_span(record_id)
                if payload is None:
                    continue
                spans[record_id] = payload
                push_refs("source", [payload.get("source_id")])
                if payload.get("episode_id"):
                    push_refs("episode", [payload.get("episode_id")])
            elif kind == "source":
                if record_id in sources:
                    continue
                payload = store.get_source(record_id)
                if payload is not None:
                    sources[record_id] = payload
            elif kind == "episode":
                if record_id in episodes:
                    continue
                payload = store.get_episode(record_id)
                if payload is not None:
                    episodes[record_id] = payload

        try:
            validate_ledger_bundle(
                source_records=[sources[k] for k in sorted(sources)],
                episode_records=[episodes[k] for k in sorted(episodes)],
                evidence_spans=[spans[k] for k in sorted(spans)],
                candidate_records=[candidates[k]
                                   for k in sorted(candidates)],
                reducer_decisions=[decisions[k] for k in sorted(decisions)],
                ledger_entries=[closure_entries[k]
                                for k in sorted(closure_entries)],
            )
        except ValueError as exc:
            raise ValidationRejectedError((str(exc),)) from exc
        return (len(sources) + len(episodes) + len(spans) + len(candidates)
                + len(decisions) + len(closure_entries))

    def _conflict_after_race(
        self,
        decision_rec: MemoryReducerDecision,
        edges: Sequence[tuple[str, str]],
        overrides: Sequence[tuple[str, str]],
        exc: sqlite3.IntegrityError,
    ) -> StoreError:
        """Build the structured 409 after a uniqueness backstop
        fired (cross-process race): re-query the committed winner."""
        existing_super = {old: dec for old, _new, dec
                          in self.store.supersession_edges()}
        for old_id, _new_id in edges:
            winner = existing_super.get(old_id)
            if winner is not None and winner != decision_rec.id:
                return ConflictError(
                    conflict_kind="entry_already_superseded",
                    record_id=old_id, winning_decision_id=winner)
        existing_over = {entry: dec for entry, _status, dec
                         in self.store.status_override_edges()}
        for target_id, _status in overrides:
            winner = existing_over.get(target_id)
            if winner is not None and winner != decision_rec.id:
                return ConflictError(
                    conflict_kind="entry_status_overridden",
                    record_id=target_id, winning_decision_id=winner)
        for candidate_id in decision_rec.target_candidate_ids:
            winner = self.store.promoting_decision_for(candidate_id)
            if winner is not None and winner != decision_rec.id:
                return ConflictError(
                    conflict_kind="candidate_already_promoted",
                    record_id=candidate_id, winning_decision_id=winner)
        return StoreError(f"write race lost without an identifiable "
                          f"winner: {exc}")

    # -- the global net (ADR-4 nightly full revalidation) ---------------------

    def full_revalidation(self, *,
                          created_at: str | None = None) -> AnchorReceipt:
        """Re-run the library validators over the entire tenant
        bundle and append a ``verified`` anchor over the full
        scope (ADR-4's global net; ADR-6's nightly anchor).
        Raises ``ValidationRejectedError`` if the bundle no longer
        validates -- per-write closures catch bad writes, this
        catches drift, tampering, and bugs."""
        records = self.store.all_records()
        try:
            validate_candidate_bundle(
                source_records=records["sources"],
                episode_records=records["episodes"],
                evidence_spans=records["spans"],
                candidate_records=records["candidates"],
            )
            validate_ledger_bundle(
                source_records=records["sources"],
                episode_records=records["episodes"],
                evidence_spans=records["spans"],
                candidate_records=records["candidates"],
                reducer_decisions=records["decisions"],
                ledger_entries=records["entries"],
            )
        except ValueError as exc:
            raise ValidationRejectedError((str(exc),)) from exc
        stamp = created_at or _utc_now()
        scope = full_scope(self.store)
        with self.store._txn() as conn:
            self.store._arm_guard(conn)
            anchor = append_anchor(self.store, conn, scope=scope,
                                   kind="verified", actor=self.actor,
                                   created_at=stamp)
            self.store._disarm_guard(conn)
        return anchor


__all__ = [
    "IngestReceipt",
    "PromoteReceipt",
    "MemoryGate",
]
