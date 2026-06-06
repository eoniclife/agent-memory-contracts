"""End-to-end migration: synthetic SQLite memory store -> contracts JSONL.

The worked example referenced from ``docs/migration.md``. Run from the
repo root with::

    PYTHONPATH=src python3 docs/migration_example.py

The script:

1. Builds an in-memory SQLite ``memory`` table and seeds it with
   three rows (two duplicates of the same preference, one decision).
2. For each row, constructs the equivalent contracts records:
   a SourceRecord, an EvidenceSpan, a Candidate, a
   MemoryReducerDecision (fabricated, since SQLite has no reducer),
   and a LedgerEntry. The source URI is derived from the **payload**
   so duplicate rows collapse to identical ids.
3. Deduplicates the per-plane lists (last-write-wins, matching the
   library's own convention), then validates the bundle with
   ``validate_ledger_bundle``.
4. Writes one JSONL file per plane to ``/tmp/migrated_contracts/``
   (override with ``MIGRATION_OUT_DIR``).
5. Computes a ``bundle_fingerprint`` of the whole bundle and prints
   a small report.

Stdlib only on top of the contracts library (which is itself
stdlib-only). No network, no LLM call, no randomness.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_memory_contracts import (
    DecisionLedgerEntry,
    EvidenceSpan,
    MemoryReducerDecision,
    PreferenceLedgerEntry,
    SourceRecord,
    bundle_fingerprint,
    make_candidate_id,
    make_ledger_entry_id,
    make_reducer_decision_id,
    make_source_id,
    make_span_id,
    validate_ledger_bundle,
)

OUT_DIR = Path(os.environ.get("MIGRATION_OUT_DIR", "/tmp/migrated_contracts"))
OUT_DIR.mkdir(parents=True, exist_ok=True)

T_OBSERVED = "2026-05-01T09:00:00Z"
T_DECIDED = "2026-05-01T10:00:00Z"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _payload_hash(payload: dict) -> str:
    """64-char hex placeholder, derived from the payload. A real
    migration would use a SHA over the *original* source content.
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# 1. The "before" stack: a synthetic in-memory SQLite store
# ---------------------------------------------------------------------------

SEED_ROWS: list[tuple[str, str, str, str]] = [
    # kind, payload (JSON), created_at, source
    (
        "preference",
        json.dumps({"subject": "code review",
                    "text": "Always read the test before reading the implementation.",
                    "domain": "writing"}),
        "2026-05-01T09:30:00Z",
        "user@example.com",
    ),
    (
        # Same payload as row 1 -- the dedup story depends on this.
        "preference",
        json.dumps({"subject": "code review",
                    "text": "Always read the test before reading the implementation.",
                    "domain": "writing"}),
        "2026-05-01T09:31:00Z",
        "user@example.com",
    ),
    (
        "decision",
        json.dumps({"subject": "memory architecture",
                    "text": "Use content-derived ids for trusted memory entries.",
                    "rationale": "Deduplication falls out for free."}),
        "2026-05-01T09:45:00Z",
        "user@example.com",
    ),
]


def seed_sqlite_store() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE memory ("
        "id INTEGER PRIMARY KEY, kind TEXT NOT NULL, "
        "payload TEXT NOT NULL, created_at TEXT NOT NULL, "
        "source TEXT NOT NULL);"
    )
    conn.executemany(
        "INSERT INTO memory (kind, payload, created_at, source) "
        "VALUES (?, ?, ?, ?)", SEED_ROWS)
    conn.commit()
    return conn


def list_memory(conn: sqlite3.Connection) -> list[dict]:
    cur = conn.execute(
        "SELECT id, kind, payload, created_at, source "
        "FROM memory ORDER BY id"
    )
    return [{
        "id": rowid, "kind": k, "payload": json.loads(p),
        "created_at": c, "source": s,
    } for rowid, k, p, c, s in cur.fetchall()]


# ---------------------------------------------------------------------------
# 2. Per-row migration: SQLite row -> contracts records
# ---------------------------------------------------------------------------

def _build_source(payload: dict) -> SourceRecord:
    # URI is content-derived: rows with the same payload share a URI.
    uri = f"sqlite://memory/{_payload_hash(payload)}"
    sid = make_source_id("manual_note", uri, _payload_hash(payload))
    return SourceRecord.from_dict({
        "id": sid, "schema_version": "1.0.0",
        "source_type": "manual_note", "title": f"Migrated entry ({uri})",
        "origin_uri": uri,
        "raw_ref": {"kind": "external_uri", "value": uri},
        "content_hash_sha256": _payload_hash(payload),
        "captured_at": T_OBSERVED, "observed_at": T_OBSERVED,
        "author_or_sender": "user@example.com",
        "participants": ["user@example.com"],
        "privacy_class": "internal",
        "custody_status": "external_pointer",
        "parser_version": "v1.0",
        "metadata": {"migrated_from": "sqlite", "migration_at": _now()},
    })


def _build_span(source_id: str, payload: dict) -> EvidenceSpan:
    sid = make_span_id(source_id, "line_range", "payload-body")
    return EvidenceSpan.from_dict({
        "id": sid, "schema_version": "1.0.0",
        "source_id": source_id, "episode_id": None,
        "locator": {"kind": "line_range", "value": "payload-body"},
        "text_excerpt": None, "excerpt_policy": "none",
        "span_hash_sha256": "0" * 64,  # synthetic placeholder
        "privacy_class": "internal",
        "metadata": {"payload": json.dumps(payload)},
    })


def _build_candidate(span_id: str, source_id: str, kind: str, payload: dict) -> dict:
    """Build an untrusted Candidate dict. Returns a dict (not a typed
    instance) because the from_dict path requires the id to match
    the expected id, and we want a single builder per kind.
    """
    if kind == "preference":
        cid = make_candidate_id("preference", [span_id], {
            "subject": payload["subject"],
            "preference_text": payload["text"],
            "domain": payload["domain"],
            "scope": "global",
            "strength_hint": "strong",
            "counterevidence_span_ids": [],
        })
        return {
            "id": cid, "schema_version": "1.0.0",
            "candidate_type": "preference",
            "source_record_ids": [source_id], "episode_record_ids": [],
            "evidence_span_ids": [span_id],
            "natural_language_summary": f"User stated: {payload['text']}",
            "extracted_by": {"agent": "sqlite-migrator",
                             "model": "synthetic-v1",
                             "tool": None, "prompt_ref": None},
            "extracted_at": T_OBSERVED,
            "confidence": "high", "risk_class": "low",
            "status": "candidate",
            "review": {"reviewed_by": None, "reviewed_at": None,
                       "review_notes": None},
            "metadata": {"migrated_from": "sqlite"},
            "subject": payload["subject"],
            "preference_text": payload["text"],
            "domain": payload["domain"],
            "scope": "global", "strength_hint": "strong",
            "counterevidence_span_ids": [],
        }
    if kind == "decision":
        cid = make_candidate_id("decision", [span_id], {
            "decision_text": payload["text"],
            "decision_scope": "architecture",
            "alternatives_mentioned": [],
            "rationale_text": payload.get("rationale"),
            "decision_time_hint": None,
            "owner_hint": None, "reversibility": "medium",
        })
        return {
            "id": cid, "schema_version": "1.0.0",
            "candidate_type": "decision",
            "source_record_ids": [source_id], "episode_record_ids": [],
            "evidence_span_ids": [span_id],
            "natural_language_summary": f"User recorded: {payload['text']}",
            "extracted_by": {"agent": "sqlite-migrator",
                             "model": "synthetic-v1",
                             "tool": None, "prompt_ref": None},
            "extracted_at": T_OBSERVED,
            "confidence": "high", "risk_class": "low",
            "status": "candidate",
            "review": {"reviewed_by": None, "reviewed_at": None,
                       "review_notes": None},
            "metadata": {"migrated_from": "sqlite"},
            "decision_text": payload["text"],
            "decision_scope": "architecture",
            "alternatives_mentioned": [],
            "rationale_text": payload.get("rationale"),
            "decision_time_hint": None,
            "owner_hint": None, "reversibility": "medium",
        }
    raise ValueError(f"unsupported kind: {kind!r}")


def _build_ledger_dict(span_id: str, source_id: str, kind: str, payload: dict) -> dict:
    """Build a LedgerEntry as a dict. Same id-derivation path as the
    typed classes; the dict form lets us patch reducer_decision_id
    and candidate_ids after the reducer is built.
    """
    if kind == "preference":
        lid = make_ledger_entry_id("preference", [span_id], {
            "ledger_type": "preference",
            "subject": payload["subject"],
            "preference_text": payload["text"],
            "domain": payload["domain"],
            "scope": "global",
            "valid_from": T_DECIDED,
            "evidence_span_ids": [span_id],
        })
        return {
            "id": lid, "schema_version": "1.0.0",
            "ledger_type": "preference", "status": "active",
            "confidence": "high", "scope": "global",
            "source_record_ids": [source_id], "episode_record_ids": [],
            "evidence_span_ids": [span_id], "candidate_ids": [],
            "reducer_decision_id": "",
            "subject": payload["subject"],
            "preference_text": payload["text"],
            "domain": payload["domain"], "strength": "strong",
            "observed_at": T_OBSERVED, "asserted_at": T_DECIDED,
            "valid_from": T_DECIDED, "valid_until": None,
            "stale_after": None,
            "created_at": T_DECIDED, "updated_at": T_DECIDED,
            "supersedes": [], "superseded_by": [],
            "metadata": {"migrated_from": "sqlite"},
        }
    if kind == "decision":
        lid = make_ledger_entry_id("decision", [span_id], {
            "ledger_type": "decision",
            "decision_text": payload["text"],
            "decision_scope": "architecture",
            "scope": "global",
            "valid_from": T_DECIDED,
            "evidence_span_ids": [span_id],
        })
        return {
            "id": lid, "schema_version": "1.0.0",
            "ledger_type": "decision", "status": "active",
            "confidence": "high", "scope": "global",
            "source_record_ids": [source_id], "episode_record_ids": [],
            "evidence_span_ids": [span_id], "candidate_ids": [],
            "reducer_decision_id": "",
            "decision_text": payload["text"],
            "decision_scope": "architecture",
            "alternatives_considered": [],
            "rationale_text": payload.get("rationale"),
            "owner": None, "reversibility": "medium",
            "observed_at": T_OBSERVED, "asserted_at": T_DECIDED,
            "valid_from": T_DECIDED, "valid_until": None,
            "stale_after": None,
            "created_at": T_DECIDED, "updated_at": T_DECIDED,
            "supersedes": [], "superseded_by": [],
            "metadata": {"migrated_from": "sqlite"},
        }
    raise ValueError(f"unsupported kind: {kind!r}")


def _build_reducer(candidate: dict, ledger: dict, rowid: int) -> MemoryReducerDecision:
    rid = make_reducer_decision_id(
        decision_type="promote",
        target_candidate_ids=[candidate["id"]],
        target_ledger_entry_ids=[ledger["id"]],
        evidence_span_ids=candidate["evidence_span_ids"],
        rationale=(
            f"Synthetic reducer decision: migrated from SQLite row {rowid}. "
            "A real reducer would run provenance, temporal_validity, "
            "contradiction_scan, privacy, and usefulness checks."
        ),
    )
    return MemoryReducerDecision.from_dict({
        "id": rid, "schema_version": "1.0.0",
        "decision_type": "promote",
        "target_candidate_ids": [candidate["id"]],
        "target_ledger_entry_ids": [ledger["id"]],
        "evidence_span_ids": candidate["evidence_span_ids"],
        "rationale": (
            f"Synthetic reducer decision: migrated from SQLite row {rowid}. "
            "A real reducer would run provenance, temporal_validity, "
            "contradiction_scan, privacy, and usefulness checks."
        ),
        "decided_by": {"agent": "sqlite-migrator",
                       "model": "synthetic-v1",
                       "tool": None, "prompt_ref": None},
        "decided_at": T_DECIDED,
        "confidence": "high", "risk_class": "low",
        "checks": {"provenance": "pass", "temporal_validity": "pass",
                   "contradiction_scan": "pass", "privacy": "pass",
                   "usefulness": "pass"},
        "metadata": {"migrated_from": "sqlite"},
    })


def migrate_row(row: dict) -> tuple[
    SourceRecord, EvidenceSpan, dict, MemoryReducerDecision, dict
]:
    """Migrate one SQLite row to (source, span, candidate, reducer, ledger)."""
    payload = row["payload"]
    source = _build_source(payload)
    span = _build_span(source.id, payload)
    candidate = _build_candidate(span.id, source.id, row["kind"], payload)
    ledger = _build_ledger_dict(span.id, source.id, row["kind"], payload)
    # Patch ledger with placeholder, build reducer, then patch ledger
    # with the real reducer id. Ledger id is content-derived from
    # (kind, span_ids, semantic_payload) -- the reducer_decision_id
    # is NOT in the semantic_payload, so the ledger id is stable.
    ledger["reducer_decision_id"] = "redmem_placeholder"
    ledger["candidate_ids"] = [candidate["id"]]
    reducer = _build_reducer(candidate, ledger, row["id"])
    ledger["reducer_decision_id"] = reducer.id
    ledger["metadata"]["migrated_from_rowid"] = row["id"]
    return source, span, candidate, reducer, ledger


# ---------------------------------------------------------------------------
# 3. Dedup + write helpers
# ---------------------------------------------------------------------------

def _dedupe_by_id(records: list[dict]) -> tuple[list[dict], int]:
    """Deduplicate by ``id``, last-write-wins (matches the library's
    convention). Returns ``(kept, dropped_count)``.
    """
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for r in records:
        rid = r.get("id")
        if rid is None:
            continue
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = r
    return [by_id[k] for k in order], len(records) - len(by_id)


def _write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 4. Main
# ---------------------------------------------------------------------------

def main() -> int:
    print("=" * 70)
    print("Migration: synthetic SQLite memory -> contracts JSONL fileset")
    print("=" * 70)

    conn = seed_sqlite_store()
    rows = list_memory(conn)
    conn.close()
    print(f"\n[1] Read {len(rows)} rows from the synthetic SQLite store")
    for r in rows:
        print(f"    - row {r['id']:>2}  kind={r['kind']:<11}  "
              f"created_at={r['created_at']}")

    sources, spans, candidates, reducers, ledgers = [], [], [], [], []
    rowid_to_ledger: dict[int, str] = {}

    print(f"\n[2] Migrating each row to a contracts record set")
    for row in rows:
        s, sp, c, r, l = migrate_row(row)
        sources.append(asdict(s))
        spans.append(asdict(sp))
        candidates.append(c)
        reducers.append(asdict(r))
        ledgers.append(l)
        rowid_to_ledger[row["id"]] = l["id"]
        print(f"    - row {row['id']:>2} -> "
              f"src={s.id[:20]}..  cand={c['id'][:20]}..  "
              f"led={l['id'][:20]}..")

    print(f"\n[3] Pre-validation dedup: rows with identical payloads "
          f"produce identical ids; the bundle validator rejects "
          f"duplicates, so we collapse by id first (last-write-wins).")
    sources, n = _dedupe_by_id(sources); print(f"    sources:        - {n}")
    spans, n = _dedupe_by_id(spans); print(f"    spans:          - {n}")
    candidates, n = _dedupe_by_id(candidates); print(f"    candidates:     - {n}")
    reducers, n = _dedupe_by_id(reducers); print(f"    reducers:       - {n}")
    ledgers, n = _dedupe_by_id(ledgers); print(f"    ledgers:        - {n}")

    print(f"\n[4] Validating the ledger bundle (cross-plane references, "
          f"reducer authorization, supersession reciprocity)")
    try:
        validate_ledger_bundle(
            source_records=sources, episode_records=[],
            evidence_spans=spans, candidate_records=candidates,
            reducer_decisions=reducers, ledger_entries=ledgers,
        )
    except ValueError as exc:
        print(f"    FAIL: bundle validation error:\n      {exc}")
        return 1
    print("    OK -- all cross-plane references resolve, "
          "reducer decisions authorize their entries")

    print(f"\n[5] Writing JSONL fileset to {OUT_DIR}/")
    for name, recs in (
        ("sources.jsonl", sources), ("spans.jsonl", spans),
        ("candidates.jsonl", candidates),
        ("reducer_decisions.jsonl", reducers), ("ledger.jsonl", ledgers),
    ):
        path = OUT_DIR / name
        _write_jsonl(path, recs)
        print(f"    - {name:<26} {len(recs):>2} records  ({path})")

    print(f"\n[6] Computing bundle fingerprint (set-semantic, order-insensitive)")
    all_records = sources + spans + candidates + reducers + ledgers
    digest = bundle_fingerprint(all_records)
    print(f"    fingerprint = {digest}")

    by_id: dict[str, list[int]] = {}
    for rowid, lid in rowid_to_ledger.items():
        by_id.setdefault(lid, []).append(rowid)
    dupes = {lid: rs for lid, rs in by_id.items() if len(rs) > 1}
    print(f"\n[7] Deduplication report: {len(rows)} SQLite row(s) -> "
          f"{len(ledgers)} distinct ledger id(s)")
    for lid, rs in sorted(dupes.items(), key=lambda kv: min(kv[1])):
        print(f"    ledger {lid[:24]}..  <-  SQLite rows {sorted(rs)}")
    if not dupes:
        print("    (no duplicate payloads detected)")

    print(f"\n[8] CLI sanity check")
    print(f"    Run: PYTHONPATH=src python -m agent_memory_contracts "
          f"fingerprint {OUT_DIR / 'ledger.jsonl'}")
    print("\nMigration complete. The JSONL fileset in "
          f"{OUT_DIR}/ is a valid contracts bundle.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
