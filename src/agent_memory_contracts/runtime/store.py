"""SQLite-backed reference runtime store for the six memory planes.

This module implements the storage model of the Brainiac runtime
ADRs on the standard library's :mod:`sqlite3`, as the **reference
runtime** the product later ports to Postgres. Same semantics,
zero dependencies (the :class:`StorageBackend` protocol at the
bottom of this module is the seam a Postgres implementation plugs
into):

- **ADR-2 -- immutable payloads + relational edges.** One table per
  plane (evidence: ``sources`` / ``episodes`` / ``spans``;
  candidate: ``candidates``; ledger: ``reducer_decisions`` /
  ``ledger_entries``; taste: ``taste_cards``; state:
  ``state_snapshots``; contextpack: ``context_packs``; plus the
  ADR-8 ``answers`` table). Each row stores the canonical
  contracts dict as an immutable JSON ``payload`` column, plus
  extracted, indexed columns (``record_type``, ``status``,
  ``subject_key``, ``valid_from``, ``valid_to``,
  ``privacy_tier``, ...) populated at write time. The payload
  remains the source of truth;
  :meth:`MemoryStore.check_extracted_columns` is the drift check.
  Relationships live in insert-only edge tables
  (``supersessions``, ``status_overrides``,
  ``decision_authorizations``, ``candidate_evidence``,
  ``entry_evidence``), never in mutable payload fields.
- **ADR-3 -- supersession against an append-only ledger.** Writing
  entry B that supersedes entry A inserts a row in the
  ``supersessions`` edge table; A's stored payload is never
  touched. The read path **materializes**: every API that returns
  a ledger entry merges ``superseded_by`` / ``supersedes`` (and
  the implied ``status`` / ``valid_until`` / authorizing-decision
  handoff) from the edge table into the returned dict, so the
  library's bundle validators pass exactly as written. The
  ``active_ledger`` SQL view (entries with no outgoing
  supersession edge, no status override, and ``status='active'``)
  is defined once and used everywhere.
- **Status overrides (deliberate adaptation).** The library's
  ledger contracts support ``retract`` / ``contest`` / ``archive``
  decisions that change an entry's status without writing a new
  entry. The product ADRs do not enumerate a mechanism for this
  against append-only rows, so the reference runtime extends the
  ADR-3 edge pattern: an insert-only ``status_overrides`` table
  (at most one terminal override per entry), materialized at read
  exactly like supersession. Corrections remain new decisions;
  nothing is ever updated in place.
- **Append-only enforcement.** SQLite triggers raise on UPDATE or
  DELETE of every plane and edge table, and on INSERT into any of
  them unless the validation gate has armed the in-transaction
  write guard. There is deliberately no code path -- including raw
  SQL on a fresh connection -- that writes a trusted fact without
  going through :meth:`agent_memory_contracts.runtime.gate.
  MemoryGate.promote`. (A hostile DBA can drop the triggers or
  arm the guard by hand; that class of tampering is what the
  ADR-6 anchor chain in :mod:`.anchors` detects --
  ``verify_chain`` checks the committed guard value and
  ``verify_coverage`` flags any trusted row no anchor accounts
  for.)
- **ADR-13 -- tenancy.** ``tenant_id`` is on every table from day
  one. The store runs single-tenant by default (``"default"``);
  every read and write is tenant-scoped.

The store exposes the **read** API (gets, ``active_entries``,
``search_entries``, bundle exports). The **write** API lives in
:mod:`.gate` (ingestion writes and the single transactional
``promote``) and :mod:`.grounding` (receipted pack and answer
persistence) -- the only modules that arm the write guard.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Iterator, Protocol, Sequence

from ..access import PRIVACY_CLASS_ORDER
from ..ledger_contracts import parse_iso8601

DEFAULT_TENANT = "default"

#: Ledger-entry id prefixes (the trusted plane).
_LEDGER_ID_PREFIXES = ("fact_", "pref_", "dec_")

#: Decision types that flip an entry's status via the
#: ``status_overrides`` edge table (no new entry is written).
_STATUS_BY_OVERRIDE_DECISION = {
    "retract": "retracted",
    "contest": "contested",
    "archive": "archived",
}


# ---------------------------------------------------------------------------
# Structured errors (shared by store, gate, and grounding)
# ---------------------------------------------------------------------------


class StoreError(Exception):
    """Base class for runtime store errors."""


class NotFoundError(StoreError):
    """A referenced record does not exist in the store."""

    def __init__(self, record_id: str) -> None:
        super().__init__(f"record not found: {record_id}")
        self.record_id = record_id


class IdMismatchError(StoreError):
    """ADR-1: the server recomputed a content-derived id and the
    client-supplied id does not match."""

    def __init__(self, *, expected: str, got: str) -> None:
        super().__init__(
            f"id_mismatch: server recomputed {expected!r}, client sent {got!r}")
        self.expected = expected
        self.got = got


class ValidationRejectedError(StoreError):
    """ADR-4: the validation closure failed a library validator.

    ``errors`` carries the validator messages verbatim (the
    HTTP-shaped runtime maps this to 422).
    """

    def __init__(self, errors: tuple[str, ...]) -> None:
        super().__init__("validation rejected: " + "; ".join(errors))
        self.errors = errors


class ConflictError(StoreError):
    """ADR-5: a structured write conflict (the 409 of the runtime).

    Attributes:
        conflict_kind: machine-readable kind --
            ``candidate_already_promoted`` |
            ``entry_already_superseded`` |
            ``entry_status_overridden`` |
            ``decision_id_exists`` | ``payload_mismatch``.
        record_id: the contested record id.
        winning_decision_id: for promotion/supersession races, the
            decision that won (first commit wins).
    """

    def __init__(
        self,
        *,
        conflict_kind: str,
        record_id: str,
        winning_decision_id: str | None = None,
        message: str | None = None,
    ) -> None:
        text = message or (
            f"conflict ({conflict_kind}) on {record_id}"
            + (f"; winning decision: {winning_decision_id}"
               if winning_decision_id else ""))
        super().__init__(text)
        self.conflict_kind = conflict_kind
        self.record_id = record_id
        self.winning_decision_id = winning_decision_id


# ---------------------------------------------------------------------------
# Canonical JSON helpers
# ---------------------------------------------------------------------------


def canonical_json(value: Any) -> str:
    """Canonical JSON exactly as the library's id helpers produce it
    (sorted keys, tight separators, non-ASCII preserved). Never
    reimplement canonicalization elsewhere (ADR-1)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False)


def _loads_dict(payload: str) -> dict[str, Any]:
    data = json.loads(payload)
    if not isinstance(data, dict):  # pragma: no cover - defensive
        raise StoreError("stored payload is not a JSON object")
    return dict(data)


def _epoch_or_none(value: Any) -> float | None:
    """Parse an ISO-8601 string to epoch seconds; ``None`` for
    null/invalid. Timezone-aware (string comparison would break
    across UTC offsets, e.g. ``+05:30`` corpus timestamps)."""
    if isinstance(value, str) and value:
        try:
            parsed = parse_iso8601(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            from datetime import timezone
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


# ---------------------------------------------------------------------------
# Schema (DDL). One table per plane; tenant_id everywhere (ADR-13).
# ---------------------------------------------------------------------------

_PLANE_TABLES = ("sources", "episodes", "spans", "candidates",
                 "reducer_decisions", "ledger_entries", "taste_cards",
                 "state_snapshots", "context_packs", "answers")
_EDGE_TABLES = ("supersessions", "status_overrides",
                "decision_authorizations", "candidate_evidence",
                "entry_evidence")

#: Every plane, edge, and anchor table accepts INSERTs only while
#: the validation gate has armed the in-transaction write guard.
#: Guarding the ingestion planes too keeps the model uniform:
#: every committed write batch is anchored (ADR-6), so
#: ``verify_coverage`` can flag any row no anchor accounts for.
_GUARDED_INSERT_TABLES = _PLANE_TABLES + _EDGE_TABLES + ("audit_anchors",)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id           TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    record_type  TEXT NOT NULL,
    title        TEXT,
    captured_at  TEXT,
    privacy_tier TEXT,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS episodes (
    id           TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    record_type  TEXT NOT NULL,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_episodes_source
    ON episodes (tenant_id, source_id);

CREATE TABLE IF NOT EXISTS spans (
    id           TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    source_id    TEXT NOT NULL,
    privacy_tier TEXT,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_spans_source ON spans (tenant_id, source_id);

CREATE TABLE IF NOT EXISTS candidates (
    id           TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    record_type  TEXT NOT NULL,
    status       TEXT NOT NULL,
    subject_key  TEXT,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_candidates_subject
    ON candidates (tenant_id, subject_key);

CREATE TABLE IF NOT EXISTS reducer_decisions (
    id            TEXT NOT NULL,
    tenant_id     TEXT NOT NULL,
    decision_type TEXT NOT NULL,
    decided_at    TEXT,
    payload       TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_decisions_type
    ON reducer_decisions (tenant_id, decision_type);

CREATE TABLE IF NOT EXISTS ledger_entries (
    id           TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    record_type  TEXT NOT NULL,
    status       TEXT NOT NULL,
    subject_key  TEXT,
    valid_from   TEXT,
    valid_to     TEXT,
    privacy_tier TEXT,
    decision_id  TEXT NOT NULL,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);
CREATE INDEX IF NOT EXISTS ix_ledger_subject
    ON ledger_entries (tenant_id, subject_key);
CREATE INDEX IF NOT EXISTS ix_ledger_status
    ON ledger_entries (tenant_id, status);

CREATE TABLE IF NOT EXISTS taste_cards (
    id           TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    record_type  TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE IF NOT EXISTS state_snapshots (
    id           TEXT NOT NULL,
    tenant_id    TEXT NOT NULL,
    record_type  TEXT NOT NULL,
    status       TEXT NOT NULL,
    as_of        TEXT,
    payload      TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

-- ADR-7: packs are content-addressed records; every build is
-- persisted with its receipts so "what did the AI know when"
-- (ADR-8) is answerable by fingerprint forever.
CREATE TABLE IF NOT EXISTS context_packs (
    id          TEXT NOT NULL,
    tenant_id   TEXT NOT NULL,
    task        TEXT NOT NULL,
    as_of       TEXT,
    fingerprint TEXT NOT NULL,
    payload     TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

-- ADR-8: every answer (and every refusal) is persisted with the
-- pack fingerprint it was grounded on.
CREATE TABLE IF NOT EXISTS answers (
    id               TEXT NOT NULL,
    tenant_id        TEXT NOT NULL,
    status           TEXT NOT NULL,
    question         TEXT NOT NULL,
    pack_id          TEXT,
    pack_fingerprint TEXT,
    payload          TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    PRIMARY KEY (tenant_id, id)
);

-- ADR-3: supersession is a relational edge. The PRIMARY KEY on
-- (tenant_id, superseded_id) is the single-successor rule: a
-- concurrent double-supersede race resolves to exactly one winner
-- (first commit wins; the loser's INSERT violates this key).
CREATE TABLE IF NOT EXISTS supersessions (
    tenant_id      TEXT NOT NULL,
    superseded_id  TEXT NOT NULL,
    superseding_id TEXT NOT NULL,
    decision_id    TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, superseded_id)
);
CREATE INDEX IF NOT EXISTS ix_supersessions_new
    ON supersessions (tenant_id, superseding_id);

-- Status overrides: retract / contest / archive, materialized at
-- read like supersession. PRIMARY KEY = at most one terminal
-- override per entry; races resolve to exactly one winner.
CREATE TABLE IF NOT EXISTS status_overrides (
    tenant_id   TEXT NOT NULL,
    entry_id    TEXT NOT NULL,
    status      TEXT NOT NULL,
    decision_id TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (tenant_id, entry_id)
);

CREATE TABLE IF NOT EXISTS decision_authorizations (
    tenant_id       TEXT NOT NULL,
    decision_id     TEXT NOT NULL,
    authorized_kind TEXT NOT NULL,
    authorized_id   TEXT NOT NULL,
    decision_type   TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, decision_id, authorized_kind, authorized_id)
);
-- ADR-5: double-promotion of the same candidate is blocked by a
-- partial unique index. First commit wins; the second promotion
-- surfaces as a structured conflict with the winning decision id.
CREATE UNIQUE INDEX IF NOT EXISTS uq_single_promotion
    ON decision_authorizations (tenant_id, authorized_id)
    WHERE authorized_kind = 'candidate' AND decision_type = 'promote';

CREATE TABLE IF NOT EXISTS candidate_evidence (
    tenant_id    TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    span_id      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, candidate_id, span_id)
);

CREATE TABLE IF NOT EXISTS entry_evidence (
    tenant_id TEXT NOT NULL,
    entry_id  TEXT NOT NULL,
    span_id   TEXT NOT NULL,
    PRIMARY KEY (tenant_id, entry_id, span_id)
);

-- ADR-6: the audit anchor hash chain. scope is canonical JSON of
-- the record ids + edge keys the fingerprint covers, so
-- verify_chain can recompute every anchor at any later time.
CREATE TABLE IF NOT EXISTS audit_anchors (
    seq              INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id        TEXT NOT NULL,
    scope            TEXT NOT NULL,
    fingerprint      TEXT NOT NULL,
    prev_fingerprint TEXT NOT NULL,
    actor            TEXT NOT NULL,
    kind             TEXT NOT NULL,
    created_at       TEXT NOT NULL
);

-- The in-transaction write guard for the governed tables. The
-- gate arms it (sets armed=1) inside its transaction and disarms
-- before commit, so the committed value is always 0: any INSERT
-- from a connection that has not armed the guard hits the
-- gate-only triggers below. (A raw connection that arms the
-- guard by hand commits armed=1 -- a tripwire the anchor
-- verifier checks.)
CREATE TABLE IF NOT EXISTS _write_guard (
    id    INTEGER PRIMARY KEY CHECK (id = 1),
    armed INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO _write_guard (id, armed) VALUES (1, 0);

-- ADR-3: one SQL view for the structurally active ledger, used
-- everywhere. Temporal validity (as_of) is applied in
-- MemoryStore.active_entries (timezone-aware, so not in SQL).
CREATE VIEW IF NOT EXISTS active_ledger AS
    SELECT le.*
    FROM ledger_entries le
    WHERE le.status = 'active'
      AND NOT EXISTS (
          SELECT 1 FROM supersessions s
          WHERE s.tenant_id = le.tenant_id
            AND s.superseded_id = le.id
      )
      AND NOT EXISTS (
          SELECT 1 FROM status_overrides o
          WHERE o.tenant_id = le.tenant_id
            AND o.entry_id = le.id
      );
"""


def _trigger_ddl() -> str:
    """Build the append-only + gate-only trigger DDL."""
    statements: list[str] = []
    for table in _PLANE_TABLES + _EDGE_TABLES + ("audit_anchors",):
        for verb in ("UPDATE", "DELETE"):
            statements.append(
                f"CREATE TRIGGER IF NOT EXISTS trg_{table}_no_{verb.lower()}\n"
                f"BEFORE {verb} ON {table}\n"
                f"BEGIN\n"
                f"    SELECT RAISE(ABORT, 'append-only: {verb} forbidden "
                f"on {table}');\n"
                f"END;")
    for table in _GUARDED_INSERT_TABLES:
        statements.append(
            f"CREATE TRIGGER IF NOT EXISTS trg_{table}_gate_only\n"
            f"BEFORE INSERT ON {table}\n"
            f"WHEN (SELECT armed FROM _write_guard WHERE id = 1) IS NOT 1\n"
            f"BEGIN\n"
            f"    SELECT RAISE(ABORT, 'no silent writes: {table} accepts "
            f"inserts only through the validation gate');\n"
            f"END;")
    return "\n".join(statements)


# ---------------------------------------------------------------------------
# Materialization (pure; shared by the store read path and the
# gate's hypothetical-closure validation so there is exactly one
# definition of the ADR-3 merge semantics)
# ---------------------------------------------------------------------------


def materialize_entry(
    payload: dict[str, Any],
    *,
    supersedes_edges: Sequence[str],
    superseded_edge: tuple[str, str] | None,
    override: tuple[str, str] | None,
    successor_valid_from: str | None,
) -> dict[str, Any]:
    """Merge supersession + override edges into a ledger-entry
    payload (ADR-3 read path).

    The returned record satisfies the library validators exactly
    as a statically built bundle would: reciprocity, the
    superseded/overridden status, the temporal handoff
    (``valid_until`` filled from the successor's ``valid_from``
    when unset), and the governing-decision handoff (a
    superseded or overridden entry's ``reducer_decision_id``
    becomes the decision that flipped it).

    Args:
        payload: the stored (immutable) entry payload.
        supersedes_edges: ids this entry supersedes (incoming
            ``superseding_id == entry`` edges).
        superseded_edge: ``(superseding_id, decision_id)`` when
            this entry has been superseded.
        override: ``(status, decision_id)`` when a status
            override (retract/contest/archive) applies.
        successor_valid_from: the successor's ``valid_from``, for
            the temporal handoff.
    """
    entry = dict(payload)
    stored_supersedes = [str(v) for v in entry.get("supersedes") or []]
    entry["supersedes"] = sorted(set(stored_supersedes)
                                 | set(supersedes_edges))
    stored_superseded_by = [str(v) for v in entry.get("superseded_by") or []]
    if superseded_edge is not None:
        superseding_id, decision_id = superseded_edge
        entry["superseded_by"] = sorted(set(stored_superseded_by)
                                        | {superseding_id})
        entry["status"] = "superseded"
        entry["reducer_decision_id"] = decision_id
        if entry.get("valid_until") is None:
            entry["valid_until"] = successor_valid_from
    else:
        entry["superseded_by"] = sorted(set(stored_superseded_by))
        if override is not None:
            status, decision_id = override
            entry["status"] = status
            entry["reducer_decision_id"] = decision_id
    return entry


# ---------------------------------------------------------------------------
# Search hit
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LedgerSearchHit:
    """One ``search_ledger`` result (ADR-11 output shape).

    Attributes:
        entry_id: The ledger entry id.
        content: The entry's human statement (fact_text /
            preference_text / decision_text plus subject triple).
        decision_id: The governing reducer decision (after
            supersession/override materialization, the latest
            governing decision).
        evidence_span_ids: The spans the entry cites.
        superseded: True if the entry has been superseded.
        score: Deterministic relevance score in [0, 1].
    """

    entry_id: str
    content: str
    decision_id: str
    evidence_span_ids: tuple[str, ...]
    superseded: bool
    score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.entry_id,
            "content": self.content,
            "decision_id": self.decision_id,
            "evidence_span_ids": list(self.evidence_span_ids),
            "superseded": self.superseded,
            "score": self.score,
        }


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------


class MemoryStore:
    """SQLite-backed, tenant-scoped store for the six memory planes.

    Reference implementation of ADR-2 / ADR-3 / ADR-13 on stdlib
    :mod:`sqlite3`. Thread-safe via an internal lock; for
    multi-process or true write-race scenarios, open one store per
    thread/process on the same database *file* (the tests for the
    ADR-5 concurrency rules do exactly that).

    Args:
        db_path: SQLite path, or ``":memory:"`` (default) for an
            in-process store.
        tenant_id: Tenant scope for every read and write. Defaults
            to single-tenant ``"default"`` (ADR-13).
    """

    def __init__(self, db_path: str | Path = ":memory:", *,
                 tenant_id: str = DEFAULT_TENANT) -> None:  # noqa: ERA001
        if not tenant_id:
            raise ValueError("tenant_id must be non-empty")
        self.tenant_id = tenant_id
        self.db_path = str(db_path)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.isolation_level = None  # explicit transactions only
        self._conn.execute("PRAGMA busy_timeout = 10000")
        if self.db_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.executescript(_trigger_ddl())

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, exc_type: type[BaseException] | None,
                 exc: BaseException | None,
                 tb: TracebackType | None) -> None:
        self.close()

    # -- transactions (gate-internal) ---------------------------------------

    @contextmanager
    def _txn(self) -> Iterator[sqlite3.Connection]:
        """One serialized write transaction. Gate-internal."""
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _arm_guard(self, conn: sqlite3.Connection) -> None:
        """Arm the governed-table write guard (gate-internal; only
        valid inside :meth:`_txn`, so the armed flag is never
        committed)."""
        conn.execute("UPDATE _write_guard SET armed = 1 WHERE id = 1")

    def _disarm_guard(self, conn: sqlite3.Connection) -> None:
        conn.execute("UPDATE _write_guard SET armed = 0 WHERE id = 1")

    def guard_committed_value(self) -> int:
        """The committed value of the write-guard flag. Always 0
        unless someone armed the guard outside a gate transaction
        (the :func:`~agent_memory_contracts.runtime.anchors.
        verify_chain` tripwire)."""
        row = self._conn.execute(
            "SELECT armed FROM _write_guard WHERE id = 1").fetchone()
        return int(row[0]) if row is not None else 0

    # -- low-level inserts (gate-internal; payload immutable forever) -------

    def _insert_source(self, conn: sqlite3.Connection,
                       payload: dict[str, Any], created_at: str) -> None:
        conn.execute(
            "INSERT INTO sources (id, tenant_id, record_type, title,"
            " captured_at, privacy_tier, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (str(payload["id"]), self.tenant_id,
             str(payload.get("source_type") or ""),
             str(payload.get("title") or ""),
             payload.get("captured_at"),
             payload.get("privacy_class"),
             canonical_json(payload), created_at))

    def _insert_episode(self, conn: sqlite3.Connection,
                        payload: dict[str, Any], created_at: str) -> None:
        conn.execute(
            "INSERT INTO episodes (id, tenant_id, source_id, record_type,"
            " payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(payload["id"]), self.tenant_id,
             str(payload.get("source_id") or ""),
             str(payload.get("episode_type") or ""),
             canonical_json(payload), created_at))

    def _insert_span(self, conn: sqlite3.Connection,
                     payload: dict[str, Any], created_at: str) -> None:
        conn.execute(
            "INSERT INTO spans (id, tenant_id, source_id, privacy_tier,"
            " payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(payload["id"]), self.tenant_id,
             str(payload.get("source_id") or ""),
             payload.get("privacy_class"),
             canonical_json(payload), created_at))

    def _insert_candidate(self, conn: sqlite3.Connection,
                          payload: dict[str, Any], created_at: str) -> None:
        conn.execute(
            "INSERT INTO candidates (id, tenant_id, record_type, status,"
            " subject_key, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(payload["id"]), self.tenant_id,
             str(payload.get("candidate_type") or ""),
             str(payload.get("status") or ""),
             payload.get("subject"),
             canonical_json(payload), created_at))
        for span_id in payload.get("evidence_span_ids") or []:
            conn.execute(
                "INSERT OR IGNORE INTO candidate_evidence"
                " (tenant_id, candidate_id, span_id) VALUES (?, ?, ?)",
                (self.tenant_id, str(payload["id"]), str(span_id)))

    def _insert_decision(self, conn: sqlite3.Connection,
                         payload: dict[str, Any], created_at: str) -> None:
        conn.execute(
            "INSERT INTO reducer_decisions (id, tenant_id, decision_type,"
            " decided_at, payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(payload["id"]), self.tenant_id,
             str(payload.get("decision_type") or ""),
             payload.get("decided_at"),
             canonical_json(payload), created_at))
        decision_type = str(payload.get("decision_type") or "")
        for candidate_id in payload.get("target_candidate_ids") or []:
            conn.execute(
                "INSERT INTO decision_authorizations (tenant_id, decision_id,"
                " authorized_kind, authorized_id, decision_type, created_at)"
                " VALUES (?, ?, 'candidate', ?, ?, ?)",
                (self.tenant_id, str(payload["id"]), str(candidate_id),
                 decision_type, created_at))
        for entry_id in payload.get("target_ledger_entry_ids") or []:
            conn.execute(
                "INSERT INTO decision_authorizations (tenant_id, decision_id,"
                " authorized_kind, authorized_id, decision_type, created_at)"
                " VALUES (?, ?, 'ledger_entry', ?, ?, ?)",
                (self.tenant_id, str(payload["id"]), str(entry_id),
                 decision_type, created_at))

    def _insert_entry(self, conn: sqlite3.Connection,
                      payload: dict[str, Any], created_at: str) -> None:
        """Insert a ledger entry in **stored form**: relationships
        live in edge tables (ADR-2), so ``supersedes`` /
        ``superseded_by`` are persisted empty and reconstructed at
        read. The caller passes the already-normalized payload."""
        conn.execute(
            "INSERT INTO ledger_entries (id, tenant_id, record_type, status,"
            " subject_key, valid_from, valid_to, privacy_tier, decision_id,"
            " payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (str(payload["id"]), self.tenant_id,
             str(payload.get("ledger_type") or ""),
             str(payload.get("status") or ""),
             payload.get("subject"),
             payload.get("valid_from"),
             payload.get("valid_until"),
             payload.get("privacy_class"),
             str(payload.get("reducer_decision_id") or ""),
             canonical_json(payload), created_at))
        for span_id in payload.get("evidence_span_ids") or []:
            conn.execute(
                "INSERT OR IGNORE INTO entry_evidence"
                " (tenant_id, entry_id, span_id) VALUES (?, ?, ?)",
                (self.tenant_id, str(payload["id"]), str(span_id)))

    def _insert_supersession(self, conn: sqlite3.Connection,
                             superseded_id: str, superseding_id: str,
                             decision_id: str, created_at: str) -> None:
        conn.execute(
            "INSERT INTO supersessions (tenant_id, superseded_id,"
            " superseding_id, decision_id, created_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (self.tenant_id, superseded_id, superseding_id, decision_id,
             created_at))

    def _insert_status_override(self, conn: sqlite3.Connection,
                                entry_id: str, status: str,
                                decision_id: str, created_at: str) -> None:
        conn.execute(
            "INSERT INTO status_overrides (tenant_id, entry_id, status,"
            " decision_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (self.tenant_id, entry_id, status, decision_id, created_at))

    def _insert_state_snapshot(self, conn: sqlite3.Connection,
                               payload: dict[str, Any],
                               created_at: str) -> None:
        conn.execute(
            "INSERT INTO state_snapshots (id, tenant_id, record_type,"
            " status, as_of, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (str(payload["id"]), self.tenant_id,
             str(payload.get("state_type") or ""),
             str(payload.get("status") or ""),
             payload.get("as_of"),
             canonical_json(payload), created_at))

    def _insert_taste_card(self, conn: sqlite3.Connection,
                           payload: dict[str, Any], created_at: str) -> None:
        conn.execute(
            "INSERT INTO taste_cards (id, tenant_id, record_type, status,"
            " payload, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (str(payload["id"]), self.tenant_id,
             str(payload.get("card_type") or ""),
             str(payload.get("status") or ""),
             canonical_json(payload), created_at))

    def _insert_context_pack(self, conn: sqlite3.Connection,
                             pack_id: str, task: str, as_of: str | None,
                             fingerprint: str, payload: dict[str, Any],
                             created_at: str) -> None:
        conn.execute(
            "INSERT INTO context_packs (id, tenant_id, task, as_of,"
            " fingerprint, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            (pack_id, self.tenant_id, task, as_of, fingerprint,
             canonical_json(payload), created_at))

    def _insert_answer(self, conn: sqlite3.Connection, answer_id: str,
                       status: str, question: str, pack_id: str | None,
                       pack_fingerprint: str | None,
                       payload: dict[str, Any], created_at: str) -> None:
        conn.execute(
            "INSERT INTO answers (id, tenant_id, status, question, pack_id,"
            " pack_fingerprint, payload, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (answer_id, self.tenant_id, status, question, pack_id,
             pack_fingerprint, canonical_json(payload), created_at))

    # -- raw payload reads (stored form; never materialized) ----------------

    def _stored_payload(self, table: str,
                        record_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            f"SELECT payload FROM {table} WHERE tenant_id = ? AND id = ?",
            (self.tenant_id, record_id)).fetchone()
        if row is None:
            return None
        return _loads_dict(str(row[0]))

    def stored_payload(self, record_id: str) -> dict[str, Any] | None:
        """The immutable stored payload for any record id (no
        supersession materialization). This is the form the audit
        anchors fingerprint, so verification is replayable at any
        later time."""
        return self._stored_payload(self._table_for(record_id), record_id)

    @staticmethod
    def _table_for(record_id: str) -> str:
        if record_id.startswith("src_"):
            return "sources"
        if record_id.startswith("ep_"):
            return "episodes"
        if record_id.startswith("span_"):
            return "spans"
        if record_id.startswith("cand_"):
            return "candidates"
        if record_id.startswith("redmem_"):
            return "reducer_decisions"
        if record_id.startswith(_LEDGER_ID_PREFIXES):
            return "ledger_entries"
        if record_id.startswith("taste_"):
            return "taste_cards"
        if record_id.startswith(("projstate_", "corestate_")):
            return "state_snapshots"
        if record_id.startswith("ctx_"):
            return "context_packs"
        if record_id.startswith("ans_"):
            return "answers"
        raise NotFoundError(record_id)

    # -- edge reads -----------------------------------------------------------

    def supersession_edges(self) -> list[tuple[str, str, str]]:
        """All ``(superseded_id, superseding_id, decision_id)`` edges
        for the tenant, deterministic order."""
        rows = self._conn.execute(
            "SELECT superseded_id, superseding_id, decision_id"
            " FROM supersessions WHERE tenant_id = ?"
            " ORDER BY superseded_id, superseding_id",
            (self.tenant_id,)).fetchall()
        return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]

    def status_override_edges(self) -> list[tuple[str, str, str]]:
        """All ``(entry_id, status, decision_id)`` overrides for the
        tenant, deterministic order."""
        rows = self._conn.execute(
            "SELECT entry_id, status, decision_id FROM status_overrides"
            " WHERE tenant_id = ? ORDER BY entry_id",
            (self.tenant_id,)).fetchall()
        return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]

    def _edges_for_entry(self, entry_id: str) -> tuple[
            list[str], tuple[str, str] | None, tuple[str, str] | None]:
        """``(supersedes_targets, superseded_by_edge, override)``
        for one entry, where ``superseded_by_edge`` is
        ``(superseding_id, decision_id) | None`` and ``override``
        is ``(status, decision_id) | None``."""
        old_rows = self._conn.execute(
            "SELECT superseded_id FROM supersessions"
            " WHERE tenant_id = ? AND superseding_id = ?"
            " ORDER BY superseded_id",
            (self.tenant_id, entry_id)).fetchall()
        new_row = self._conn.execute(
            "SELECT superseding_id, decision_id FROM supersessions"
            " WHERE tenant_id = ? AND superseded_id = ?",
            (self.tenant_id, entry_id)).fetchone()
        override_row = self._conn.execute(
            "SELECT status, decision_id FROM status_overrides"
            " WHERE tenant_id = ? AND entry_id = ?",
            (self.tenant_id, entry_id)).fetchone()
        supersedes = [str(r[0]) for r in old_rows]
        superseded_by = ((str(new_row[0]), str(new_row[1]))
                         if new_row is not None else None)
        override = ((str(override_row[0]), str(override_row[1]))
                    if override_row is not None else None)
        return supersedes, superseded_by, override

    # -- materialization (ADR-3 read path) -----------------------------------

    def _materialize(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Merge edges into a ledger-entry payload via the shared
        :func:`materialize_entry` semantics (ADR-3 read path)."""
        entry_id = str(payload.get("id") or "")
        supersedes_edges, superseded_edge, override = (
            self._edges_for_entry(entry_id))
        successor_valid_from: str | None = None
        if superseded_edge is not None and payload.get("valid_until") is None:
            successor_row = self._conn.execute(
                "SELECT valid_from FROM ledger_entries"
                " WHERE tenant_id = ? AND id = ?",
                (self.tenant_id, superseded_edge[0])).fetchone()
            if successor_row is not None and successor_row[0] is not None:
                successor_valid_from = str(successor_row[0])
        return materialize_entry(
            payload,
            supersedes_edges=supersedes_edges,
            superseded_edge=superseded_edge,
            override=override,
            successor_valid_from=successor_valid_from,
        )

    # -- public reads ---------------------------------------------------------

    def get_source(self, source_id: str) -> dict[str, Any] | None:
        return self._stored_payload("sources", source_id)

    def get_episode(self, episode_id: str) -> dict[str, Any] | None:
        return self._stored_payload("episodes", episode_id)

    def get_span(self, span_id: str) -> dict[str, Any] | None:
        return self._stored_payload("spans", span_id)

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        return self._stored_payload("candidates", candidate_id)

    def get_decision(self, decision_id: str) -> dict[str, Any] | None:
        return self._stored_payload("reducer_decisions", decision_id)

    def get_ledger_entry(self, entry_id: str) -> dict[str, Any] | None:
        """A ledger entry, **materialized** (ADR-3 read path)."""
        payload = self._stored_payload("ledger_entries", entry_id)
        if payload is None:
            return None
        return self._materialize(payload)

    def get_state_snapshot(self, snapshot_id: str) -> dict[str, Any] | None:
        return self._stored_payload("state_snapshots", snapshot_id)

    def get_taste_card(self, card_id: str) -> dict[str, Any] | None:
        return self._stored_payload("taste_cards", card_id)

    def get_context_pack(self, pack_id: str) -> dict[str, Any] | None:
        """The persisted pack envelope (pack + receipts +
        fingerprint), by content-derived pack id (ADR-7)."""
        return self._stored_payload("context_packs", pack_id)

    def get_answer(self, answer_id: str) -> dict[str, Any] | None:
        """A persisted answer/refusal envelope (ADR-8)."""
        return self._stored_payload("answers", answer_id)

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        """Any record by id; ledger entries come back materialized."""
        table = self._table_for(record_id)
        if table == "ledger_entries":
            return self.get_ledger_entry(record_id)
        return self._stored_payload(table, record_id)

    def list_sources(self) -> list[dict[str, Any]]:
        return self._list_payloads("sources")

    def list_episodes(self) -> list[dict[str, Any]]:
        return self._list_payloads("episodes")

    def list_spans(self) -> list[dict[str, Any]]:
        return self._list_payloads("spans")

    def list_candidates(self, *, status: str | None = None,
                        subject: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM candidates WHERE tenant_id = ?"
        params: list[Any] = [self.tenant_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if subject is not None:
            sql += " AND subject_key = ?"
            params.append(subject)
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, params).fetchall()
        return [_loads_dict(str(r[0])) for r in rows]

    def list_decisions(self, *, decision_type: str | None = None,
                       ) -> list[dict[str, Any]]:
        sql = "SELECT payload FROM reducer_decisions WHERE tenant_id = ?"
        params: list[Any] = [self.tenant_id]
        if decision_type is not None:
            sql += " AND decision_type = ?"
            params.append(decision_type)
        sql += " ORDER BY id"
        rows = self._conn.execute(sql, params).fetchall()
        return [_loads_dict(str(r[0])) for r in rows]

    def list_ledger_entries(self) -> list[dict[str, Any]]:
        """Every ledger entry for the tenant, materialized,
        deterministic id order."""
        rows = self._conn.execute(
            "SELECT payload FROM ledger_entries WHERE tenant_id = ?"
            " ORDER BY id", (self.tenant_id,)).fetchall()
        return [self._materialize(_loads_dict(str(r[0]))) for r in rows]

    def list_state_snapshots(self) -> list[dict[str, Any]]:
        return self._list_payloads("state_snapshots")

    def list_taste_cards(self) -> list[dict[str, Any]]:
        return self._list_payloads("taste_cards")

    def list_context_packs(self) -> list[dict[str, Any]]:
        return self._list_payloads("context_packs")

    def list_answers(self) -> list[dict[str, Any]]:
        return self._list_payloads("answers")

    def _list_payloads(self, table: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            f"SELECT payload FROM {table} WHERE tenant_id = ?"
            f" ORDER BY id", (self.tenant_id,)).fetchall()
        return [_loads_dict(str(r[0])) for r in rows]

    def rejected_candidate_ids(self) -> list[str]:
        """Candidate ids quarantined by a ``reject`` decision (the
        candidate row itself stays immutable in the untrusted
        plane; the rejection is the decision record)."""
        rows = self._conn.execute(
            "SELECT DISTINCT authorized_id FROM decision_authorizations"
            " WHERE tenant_id = ? AND authorized_kind = 'candidate'"
            " AND decision_type = 'reject' ORDER BY authorized_id",
            (self.tenant_id,)).fetchall()
        return [str(r[0]) for r in rows]

    def promoting_decision_for(self, candidate_id: str) -> str | None:
        """The decision id that promoted ``candidate_id``, if any
        (the ADR-5 single-promotion winner)."""
        row = self._conn.execute(
            "SELECT decision_id FROM decision_authorizations"
            " WHERE tenant_id = ? AND authorized_kind = 'candidate'"
            " AND decision_type = 'promote' AND authorized_id = ?",
            (self.tenant_id, candidate_id)).fetchone()
        return str(row[0]) if row is not None else None

    # -- the active ledger (ADR-3 view + ADR-7/8 time travel) ----------------

    def active_entries(self, *, as_of: str | None = None,
                       max_privacy: str | None = None,
                       ) -> list[dict[str, Any]]:
        """The active ledger, materialized.

        With ``as_of=None``: the ``active_ledger`` view semantics
        -- entries with no outgoing supersession edge, no status
        override, and ``status='active'``.

        With ``as_of`` set: **time travel** (the ADR-8 "what did
        the AI know on May 3rd" read). An entry is active at
        ``as_of`` iff its materialized validity window covers
        ``as_of`` (``valid_from <= as_of < valid_until``, null
        bounds open) and it carries no status override --
        retracted/contested/archived entries are treated as
        withdrawn at every point in time, while a since-superseded
        entry IS returned for the window in which it was the
        truth.

        ``max_privacy`` filters by privacy tier (ADR-13): entries
        above the requester's clearance are never returned.
        """
        if as_of is None:
            rows = self._conn.execute(
                "SELECT payload FROM active_ledger WHERE tenant_id = ?"
                " ORDER BY id", (self.tenant_id,)).fetchall()
            entries = [self._materialize(_loads_dict(str(r[0])))
                       for r in rows]
        else:
            as_of_epoch = _epoch_or_none(as_of)
            if as_of_epoch is None:
                raise ValueError(f"as_of must be ISO-8601, got {as_of!r}")
            entries = []
            for entry in self.list_ledger_entries():
                if entry.get("status") in ("retracted", "contested",
                                           "archived"):
                    continue
                valid_from = _epoch_or_none(entry.get("valid_from"))
                valid_until = _epoch_or_none(entry.get("valid_until"))
                if valid_from is not None and valid_from > as_of_epoch:
                    continue
                if valid_until is not None and valid_until <= as_of_epoch:
                    continue
                entries.append(entry)
        if max_privacy is not None:
            ceiling = PRIVACY_CLASS_ORDER.index(max_privacy)
            entries = [e for e in entries
                       if self._effective_privacy_index(e) <= ceiling]
        return entries

    def _effective_privacy_index(self, entry: dict[str, Any]) -> int:
        """An entry's effective privacy tier (ADR-13).

        The ledger contracts do not carry ``privacy_class`` on
        entries, so the tier derives from the evidence: a fact is
        as sensitive as the most sensitive span it cites. An
        explicit ``privacy_class`` on the entry payload (forward
        compatibility) overrides. Unknown values fail **closed**
        (treated as the most restricted tier)."""
        highest = len(PRIVACY_CLASS_ORDER) - 1

        def index_of(value: Any) -> int:
            name = str(value or "internal")
            try:
                return PRIVACY_CLASS_ORDER.index(name)
            except ValueError:
                return highest
        explicit = entry.get("privacy_class")
        if explicit:
            return index_of(explicit)
        levels: list[int] = []
        for span_id in entry.get("evidence_span_ids") or []:
            span = self.get_span(str(span_id))
            if span is None:
                levels.append(highest)  # unresolvable evidence: closed
            else:
                levels.append(index_of(span.get("privacy_class")))
        return max(levels, default=PRIVACY_CLASS_ORDER.index("internal"))

    # -- deterministic search (ADR-11 search_ledger) --------------------------

    @staticmethod
    def _tokens(text: str) -> list[str]:
        out: list[str] = []
        word: list[str] = []
        for ch in text.lower():
            if ch.isalnum():
                word.append(ch)
            elif word:
                out.append("".join(word))
                word = []
        if word:
            out.append("".join(word))
        return out

    @staticmethod
    def _entry_text(entry: dict[str, Any]) -> str:
        parts: list[str] = []
        for key in ("fact_text", "preference_text", "decision_text",
                    "subject", "predicate", "object"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                parts.append(value)
        return " ".join(parts)

    def search_entries(self, query: str, *, as_of: str | None = None,
                       limit: int = 20,
                       include_superseded: bool = True,
                       ) -> list[LedgerSearchHit]:
        """Deterministic token-overlap search over the ledger.

        The reference runtime deliberately avoids FTS5 (not
        guaranteed in every stdlib sqlite3 build); the product
        substitutes Postgres FTS behind the same call shape
        (ADR-11 ``search_ledger``). Scoring is the fraction of
        query tokens present in the entry text; ties break on id.
        """
        query_tokens = self._tokens(query)
        if not query_tokens:
            return []
        if include_superseded:
            entries = self.list_ledger_entries()
            as_of_epoch = _epoch_or_none(as_of) if as_of is not None else None
            if as_of_epoch is not None:
                kept: list[dict[str, Any]] = []
                for entry in entries:
                    valid_from = _epoch_or_none(entry.get("valid_from"))
                    if valid_from is None or valid_from <= as_of_epoch:
                        kept.append(entry)
                entries = kept
        else:
            entries = self.active_entries(as_of=as_of)
        hits: list[LedgerSearchHit] = []
        for entry in entries:
            text_tokens = set(self._tokens(self._entry_text(entry)))
            matched = sum(1 for t in query_tokens if t in text_tokens)
            if matched == 0:
                continue
            score = matched / len(query_tokens)
            hits.append(LedgerSearchHit(
                entry_id=str(entry.get("id") or ""),
                content=self._entry_text(entry),
                decision_id=str(entry.get("reducer_decision_id") or ""),
                evidence_span_ids=tuple(
                    str(s) for s in entry.get("evidence_span_ids") or []),
                superseded=bool(entry.get("superseded_by")),
                score=score,
            ))
        hits.sort(key=lambda h: (-h.score, h.entry_id))
        return hits[:max(limit, 0)]

    # -- bundle exports --------------------------------------------------------

    def all_records(self) -> dict[str, list[dict[str, Any]]]:
        """The whole tenant bundle (ledger entries materialized),
        keyed by plane. Used by full revalidation, packs, and the
        audit pack export."""
        return {
            "sources": self.list_sources(),
            "episodes": self.list_episodes(),
            "spans": self.list_spans(),
            "candidates": self.list_candidates(),
            "decisions": self.list_decisions(),
            "entries": self.list_ledger_entries(),
        }

    def check_extracted_columns(self) -> list[str]:
        """ADR-2 drift check: assert extracted columns == payload.

        Returns a list of human-readable drift findings (empty =
        clean). The product runs this nightly; the reference
        runtime exposes it for tests."""
        findings: list[str] = []
        checks: list[tuple[str, list[tuple[str, str]]]] = [
            ("sources", [("record_type", "source_type"),
                         ("title", "title"),
                         ("privacy_tier", "privacy_class")]),
            ("episodes", [("source_id", "source_id"),
                          ("record_type", "episode_type")]),
            ("spans", [("source_id", "source_id")]),
            ("candidates", [("record_type", "candidate_type"),
                            ("status", "status")]),
            ("reducer_decisions", [("decision_type", "decision_type"),
                                   ("decided_at", "decided_at")]),
            ("ledger_entries", [("record_type", "ledger_type"),
                                ("status", "status"),
                                ("valid_from", "valid_from"),
                                ("valid_to", "valid_until"),
                                ("decision_id", "reducer_decision_id")]),
            ("taste_cards", [("record_type", "card_type"),
                             ("status", "status")]),
            ("state_snapshots", [("record_type", "state_type"),
                                 ("status", "status"),
                                 ("as_of", "as_of")]),
        ]
        for table, pairs in checks:
            columns = ", ".join(col for col, _ in pairs)
            rows = self._conn.execute(
                f"SELECT id, payload, {columns} FROM {table}"
                f" WHERE tenant_id = ? ORDER BY id",
                (self.tenant_id,)).fetchall()
            for row in rows:
                payload = _loads_dict(str(row[1]))
                for i, (col, key) in enumerate(pairs):
                    extracted = row[2 + i]
                    expected = payload.get(key)
                    if (extracted or None) != (expected or None):
                        findings.append(
                            f"{table}.{col} drift on {row[0]}: "
                            f"extracted {extracted!r} != payload {expected!r}")
        return findings

    # -- anchor support (ADR-6) ------------------------------------------------

    def list_anchors(self) -> list[dict[str, Any]]:
        """Every audit anchor for the tenant, in chain (seq) order.
        Raw rows; interpretation lives in :mod:`.anchors`."""
        rows = self._conn.execute(
            "SELECT seq, tenant_id, scope, fingerprint, prev_fingerprint,"
            " actor, kind, created_at FROM audit_anchors"
            " WHERE tenant_id = ? ORDER BY seq",
            (self.tenant_id,)).fetchall()
        return [
            {"seq": int(r[0]), "tenant_id": str(r[1]), "scope": str(r[2]),
             "fingerprint": str(r[3]), "prev_fingerprint": str(r[4]),
             "actor": str(r[5]), "kind": str(r[6]),
             "created_at": str(r[7])}
            for r in rows]

    def governed_row_ids(self) -> dict[str, list[str]]:
        """Every plane-table record id for the tenant, keyed by
        table, deterministic order. Coverage-check support
        (:func:`~agent_memory_contracts.runtime.anchors.
        verify_coverage`)."""
        out: dict[str, list[str]] = {}
        for table in _PLANE_TABLES:
            rows = self._conn.execute(
                f"SELECT id FROM {table} WHERE tenant_id = ? ORDER BY id",
                (self.tenant_id,)).fetchall()
            out[table] = [str(r[0]) for r in rows]
        return out

    def derived_edge_rows(self, table: str) -> list[tuple[str, ...]]:
        """Raw rows of a derived edge index
        (``decision_authorizations`` | ``candidate_evidence`` |
        ``entry_evidence``), deterministic order. These tables are
        reconstructible projections of anchored payloads; the
        anchor verifier recomputes and compares them."""
        if table == "decision_authorizations":
            rows = self._conn.execute(
                "SELECT decision_id, authorized_kind, authorized_id,"
                " decision_type FROM decision_authorizations"
                " WHERE tenant_id = ? ORDER BY decision_id,"
                " authorized_kind, authorized_id",
                (self.tenant_id,)).fetchall()
        elif table == "candidate_evidence":
            rows = self._conn.execute(
                "SELECT candidate_id, span_id FROM candidate_evidence"
                " WHERE tenant_id = ? ORDER BY candidate_id, span_id",
                (self.tenant_id,)).fetchall()
        elif table == "entry_evidence":
            rows = self._conn.execute(
                "SELECT entry_id, span_id FROM entry_evidence"
                " WHERE tenant_id = ? ORDER BY entry_id, span_id",
                (self.tenant_id,)).fetchall()
        else:
            raise ValueError(f"not a derived edge table: {table}")
        return [tuple(str(v) for v in row) for row in rows]

    # -- counts (test/console support) ----------------------------------------

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for table in _PLANE_TABLES + ("supersessions", "status_overrides",
                                      "audit_anchors"):
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE tenant_id = ?",
                (self.tenant_id,)).fetchone()
            out[table] = int(row[0])
        return out


# ---------------------------------------------------------------------------
# StorageBackend protocol (the Postgres seam)
# ---------------------------------------------------------------------------


class StorageBackend(Protocol):
    """The storage surface :mod:`.gate`, :mod:`.anchors`, and
    :mod:`.grounding` actually consume.

    Deliberate adaptation: the product ADRs assume Postgres; this
    repo is stdlib-only, so :class:`MemoryStore` implements the
    semantics on sqlite3 and the product repo adds a Postgres
    backend satisfying this protocol (plus the gate-internal
    seams: ``_txn`` / ``_arm_guard`` / ``_insert_*`` -- kept
    private here because their *callers* are this package; a
    Postgres port reimplements gate+store together against the
    same tests).
    """

    tenant_id: str

    def stored_payload(self, record_id: str) -> dict[str, Any] | None: ...

    def get_ledger_entry(self, entry_id: str) -> dict[str, Any] | None: ...

    def active_entries(self, *, as_of: str | None = ...,
                       max_privacy: str | None = ...,
                       ) -> list[dict[str, Any]]: ...

    def supersession_edges(self) -> list[tuple[str, str, str]]: ...

    def status_override_edges(self) -> list[tuple[str, str, str]]: ...

    def all_records(self) -> dict[str, list[dict[str, Any]]]: ...


__all__ = [
    "materialize_entry",
    "StoreError",
    "NotFoundError",
    "IdMismatchError",
    "ValidationRejectedError",
    "ConflictError",
    "canonical_json",
    "LedgerSearchHit",
    "MemoryStore",
    "StorageBackend",
]
