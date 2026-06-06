"""Worked example for access control + bundle scope.

Run from the repository root::

    PYTHONPATH=src python examples/access.py

Builds a small synthetic bundle with records at each of the 5
privacy classes (public, internal, private, sensitive,
highly_sensitive) and demonstrates the four headline primitives:

- :func:`check_access` — per-record scope check
- :func:`scope_bundle` — whole-bundle filter
- :func:`summarize_access` — aggregate counts
- The four scope factories (:func:`public_scope`,
  :func:`team_scope`, :func:`customer_scope`,
  :func:`private_scope`).

.. versionadded:: 0.9.0
"""

from __future__ import annotations

import sys
from typing import Any

from agent_memory_contracts import (
    AccessDecision,
    AccessSummary,
    BundleScope,
    SourceRecord,
    check_access,
    customer_scope,
    make_source_id,
    private_scope,
    public_scope,
    scope_bundle,
    summarize_access,
    team_scope,
)

T_CAPTURED = "2026-06-06T12:00:00Z"


def _build_source(privacy_class: str, suffix_hex: str) -> SourceRecord:
    """Build a SourceRecord at a given privacy class.

    The content hash is a 64-char hex string for id stability.
    """
    content_hash = (suffix_hex * 64)[:64]
    source_id = make_source_id("chatgpt_conversation", f"https://example.com/{suffix_hex}", content_hash)
    return SourceRecord.from_dict({
        "id": source_id, "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": f"Source {privacy_class}", "origin_uri": f"https://example.com/{suffix_hex}",
        "raw_ref": {"kind": "external_uri", "value": f"https://example.com/{suffix_hex}"},
        "content_hash_sha256": content_hash,
        "captured_at": T_CAPTURED, "observed_at": None,
        "author_or_sender": None, "participants": [],
        "privacy_class": privacy_class, "custody_status": "synthetic",
        "parser_version": "v1", "metadata": {},
    })


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def demo_private_scope() -> None:
    """The 'private' scope allows everything."""
    section("1. private_scope() — owner-only, all 5 classes allowed")
    bundle = [
        _build_source("public", "1"),
        _build_source("internal", "2"),
        _build_source("private", "3"),
        _build_source("sensitive", "4"),
        _build_source("highly_sensitive", "5"),
    ]
    scope = private_scope()
    print(f"scope: {scope}")
    filtered, decisions = scope_bundle(bundle, scope)
    print(f"filtered: {len(filtered)} of {len(bundle)}")
    for d in decisions:
        print(f"  {d}")


def demo_team_scope() -> None:
    """The 'team' scope allows public + internal only."""
    section("2. team_scope() — share with team, public+internal only")
    bundle = [
        _build_source("public", "1"),
        _build_source("internal", "2"),
        _build_source("private", "3"),
        _build_source("sensitive", "4"),
        _build_source("highly_sensitive", "5"),
    ]
    scope = team_scope()
    print(f"scope: {scope}")
    filtered, decisions = scope_bundle(bundle, scope)
    print(f"filtered: {len(filtered)} of {len(bundle)}")
    for d in decisions:
        print(f"  {d}")
    summary = summarize_access(decisions)
    print(f"\nsummary: {summary}")


def demo_customer_scope() -> None:
    """The 'customer' scope allows up to private."""
    section("3. customer_scope() — share with paying customer, up to private")
    bundle = [
        _build_source("public", "1"),
        _build_source("internal", "2"),
        _build_source("private", "3"),
        _build_source("sensitive", "4"),
        _build_source("highly_sensitive", "5"),
    ]
    scope = customer_scope()
    print(f"scope: {scope}")
    filtered, decisions = scope_bundle(bundle, scope)
    print(f"filtered: {len(filtered)} of {len(bundle)}")
    for d in decisions:
        print(f"  {d}")
    summary = summarize_access(decisions)
    print(f"\nsummary: {summary}")


def demo_public_scope() -> None:
    """The 'public' scope allows only public records."""
    section("4. public_scope() — public web, public only")
    bundle = [
        _build_source("public", "1"),
        _build_source("internal", "2"),
        _build_source("private", "3"),
        _build_source("sensitive", "4"),
        _build_source("highly_sensitive", "5"),
    ]
    scope = public_scope()
    print(f"scope: {scope}")
    filtered, decisions = scope_bundle(bundle, scope)
    print(f"filtered: {len(filtered)} of {len(bundle)}")
    for d in decisions:
        print(f"  {d}")


def demo_record_type_filter() -> None:
    """A custom scope that also filters by record type."""
    section("5. Custom scope with allowed_record_types filter")
    from agent_memory_contracts import FactLedgerEntry
    src, span = _src_and_span()
    fact = _build_fact(src.id, [span.id])
    bundle = [src, span, fact]
    scope = BundleScope(
        max_privacy_class="highly_sensitive",
        allowed_record_types=frozenset({"source_record"}),
        name="sources-only",
    )
    print(f"scope: {scope}")
    filtered, decisions = scope_bundle(bundle, scope)
    print(f"filtered: {len(filtered)} of {len(bundle)}")
    for d in decisions:
        print(f"  {d}")


def _src_and_span() -> tuple[Any, Any]:
    """Build a source + span for the record-type demo."""
    from agent_memory_contracts import EvidenceSpan, make_span_id
    content_hash = "f" * 64
    source_id = make_source_id("chatgpt_conversation", "https://example.com/filter", content_hash)
    src = SourceRecord.from_dict({
        "id": source_id, "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": "Source", "origin_uri": "https://example.com/filter",
        "raw_ref": {"kind": "external_uri", "value": "https://example.com/filter"},
        "content_hash_sha256": content_hash,
        "captured_at": T_CAPTURED, "observed_at": None,
        "author_or_sender": None, "participants": [],
        "privacy_class": "internal", "custody_status": "synthetic",
        "parser_version": "v1", "metadata": {},
    })
    span_id = make_span_id(source_id, "line_range", "1-5")
    span = EvidenceSpan.from_dict({
        "id": span_id, "schema_version": "1.0.0",
        "source_id": source_id, "episode_id": None,
        "locator": {"kind": "line_range", "value": "1-5"},
        "text_excerpt": None, "excerpt_policy": "none",
        "span_hash_sha256": "e" * 64,
        "privacy_class": "internal", "metadata": {},
    })
    return src, span


def _build_fact(source_id: str, span_ids: list[str]) -> FactLedgerEntry:
    from agent_memory_contracts import (
        FactLedgerEntry, make_ledger_entry_id, make_reducer_decision_id,
    )
    T_DECIDED = "2026-06-06T13:00:00Z"
    reducer_id = make_reducer_decision_id("fact", [], [], list(span_ids), "ok")
    entry_id = make_ledger_entry_id(
        "fact", span_ids,
        {
            "ledger_type": "fact", "subject": "s", "predicate": "p", "object": "o",
            "scope": "global", "valid_from": T_DECIDED,
            "evidence_span_ids": sorted(span_ids),
        },
    )
    return FactLedgerEntry.from_dict({
        "id": entry_id, "schema_version": "1.0.0",
        "ledger_type": "fact", "status": "active", "confidence": "high", "scope": "global",
        "source_record_ids": [source_id], "episode_record_ids": [],
        "evidence_span_ids": span_ids, "candidate_ids": [],
        "reducer_decision_id": reducer_id,
        "observed_at": None, "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED, "valid_until": None, "stale_after": None,
        "created_at": T_DECIDED, "updated_at": T_DECIDED,
        "supersedes": [], "superseded_by": [], "metadata": {},
        "subject": "s", "predicate": "p", "object": "o", "fact_text": "A fact.",
    })


def main(argv: list[str] | None = None) -> int:
    demo_private_scope()
    demo_team_scope()
    demo_customer_scope()
    demo_public_scope()
    demo_record_type_filter()
    print()
    print("=" * 70)
    print("Access control example complete.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
