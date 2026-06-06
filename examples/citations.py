"""Worked example for the citation graph + provenance traversal.

Run from the repository root::

    PYTHONPATH=src python examples/citations.py

This example builds three small synthetic bundles (linear chain,
diamond, disconnected), constructs a citation graph from each,
and demonstrates the four headline audit queries:

- :func:`find_unsupported_claims` (claims with no path to a source)
- :func:`find_unused_sources` (sources not cited by any claim)
- :func:`find_dangling_refs` (citations to records not in the bundle)
- :meth:`CitationGraph.traverse` (forward / backward / both directions)

The point of the example is to make the public API concrete for
human readers. The unit tests in ``tests/test_citations.py`` are
the rigorous coverage; this file is the narrative.

.. versionadded:: 0.8.0
"""

from __future__ import annotations

import sys
from dataclasses import asdict
from typing import Any

from agent_memory_contracts import (
    CitationGraph,
    EvidenceSpan,
    FactLedgerEntry,
    SourceRecord,
    find_dangling_refs,
    find_unused_sources,
    find_unsupported_claims,
    make_ledger_entry_id,
    make_reducer_decision_id,
    make_source_id,
    make_span_id,
)

T_CAPTURED = "2026-06-06T12:00:00Z"
T_DECIDED = "2026-06-06T13:00:00Z"


def build_source_and_span(suffix: str) -> tuple[SourceRecord, EvidenceSpan]:
    """Build one source + one evidence span with a stable id."""
    content_hash = "a" * 64 if suffix == "a" else "b" * 64
    span_hash = "c" * 64 if suffix == "a" else "d" * 64
    source_id = make_source_id("chatgpt_conversation", f"https://example.com/{suffix}", content_hash)
    source = SourceRecord.from_dict({
        "id": source_id,
        "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": f"Conversation {suffix}",
        "origin_uri": f"https://example.com/{suffix}",
        "raw_ref": {"kind": "external_uri", "value": f"https://example.com/{suffix}"},
        "content_hash_sha256": content_hash,
        "captured_at": T_CAPTURED,
        "observed_at": T_CAPTURED,
        "author_or_sender": "user@example.com",
        "participants": ["user@example.com"],
        "privacy_class": "internal",
        "custody_status": "external_pointer",
        "parser_version": "v1.0",
        "metadata": {},
    })
    span_id = make_span_id(source_id, "line_range", f"1-{suffix}")
    span = EvidenceSpan.from_dict({
        "id": span_id,
        "schema_version": "1.0.0",
        "source_id": source_id,
        "episode_id": None,
        "locator": {"kind": "line_range", "value": f"1-{suffix}"},
        "text_excerpt": None,
        "excerpt_policy": "none",
        "span_hash_sha256": span_hash,
        "privacy_class": "internal",
        "metadata": {},
    })
    return source, span


def build_fact(source_id: str, span_ids: list[str], subject: str) -> FactLedgerEntry:
    """Build a FactLedgerEntry citing the given evidence span ids."""
    reducer_id = make_reducer_decision_id("fact", [], [], list(span_ids), "ok")
    entry_id = make_ledger_entry_id(
        "fact", span_ids,
        {
            "ledger_type": "fact",
            "subject": subject, "predicate": "p", "object": "o",
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
        "subject": subject, "predicate": "p", "object": "o", "fact_text": f"Fact about {subject}.",
    })


def section(title: str) -> None:
    """Print a section banner."""
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def demo_linear_chain() -> list[Any]:
    """A linear chain: claim -> evidence -> source. Fully supported."""
    section("1. Linear chain (one source, one evidence, one claim)")

    src, span = build_source_and_span("a")
    fact = build_fact(src.id, [span.id], subject="linear")
    bundle = [src, span, fact]

    print(f"bundle size: {len(bundle)} records")
    print(f"  - SourceRecord({src.id[:24]}...)")
    print(f"  - EvidenceSpan({span.id[:24]}...)")
    print(f"  - FactLedgerEntry({fact.id[:24]}...)")

    graph = CitationGraph.from_bundle(bundle)
    print(f"\ngraph size: {graph.size()} nodes, {graph.node_count_by_kind()}")
    print(f"dangling refs: {len(graph.dangling_refs)}")

    paths = graph.traverse(fact.id, direction="forward")
    print(f"\ntraverse(claim, forward): {len(paths)} path(s)")
    for p in paths:
        kinds = " -> ".join(n.node_kind for n in p.nodes)
        print(f"  {kinds} (length={p.length}, supported={p.is_supported()})")

    paths_back = graph.traverse(src.id, direction="backward")
    print(f"\ntraverse(source, backward): {len(paths_back)} path(s)")
    for p in paths_back:
        kinds = " -> ".join(n.node_kind for n in p.nodes)
        print(f"  {kinds}")

    print(f"\nunsupported claims: {len(find_unsupported_claims(bundle))}")
    print(f"unused sources: {len(find_unused_sources(bundle))}")
    return bundle


def demo_diamond() -> list[Any]:
    """A diamond: one claim citing two evidence spans, both from the same source."""
    section("2. Diamond (one source, two evidence spans, one claim citing both)")

    src, span_a = build_source_and_span("a")
    # Build a second evidence span that points to the SAME source.
    span_b_id = make_span_id(src.id, "line_range", "20-25")
    span_b = EvidenceSpan.from_dict({
        "id": span_b_id, "schema_version": "1.0.0",
        "source_id": src.id, "episode_id": None,
        "locator": {"kind": "line_range", "value": "20-25"},
        "text_excerpt": None, "excerpt_policy": "none",
        "span_hash_sha256": "d" * 64, "privacy_class": "internal", "metadata": {},
    })
    fact = build_fact(src.id, [span_a.id, span_b.id], subject="diamond")
    bundle = [src, span_a, span_b, fact]

    print(f"bundle size: {len(bundle)} records")
    graph = CitationGraph.from_bundle(bundle)
    print(f"graph: {graph.size()} nodes, {graph.node_count_by_kind()}")

    paths = graph.traverse(fact.id, direction="forward")
    print(f"\ntraverse(claim, forward): {len(paths)} paths (one per evidence span)")
    for p in paths:
        node_ids = " -> ".join(n.record_id[:24] + "..." for n in p.nodes)
        print(f"  {node_ids}")

    print(f"\nunsupported claims: {len(find_unsupported_claims(bundle))}")
    print(f"unused sources: {len(find_unused_sources(bundle))}")
    return bundle


def demo_disconnected() -> None:
    """A bundle with an unsupported claim and an unused source."""
    section("3. Disconnected (one unsupported claim, one unused source)")

    src_used, span_used = build_source_and_span("a")
    fact_used = build_fact(src_used.id, [span_used.id], subject="used")

    # An unsupported claim: a fact citing a span with no source.
    dangling_span_id = make_span_id("src_does_not_exist", "synthetic_locator", "0-1")
    fact_unsupported = build_fact("src_does_not_exist", [dangling_span_id], subject="unsupported")

    # An unused source: ingested but never cited.
    src_unused, _ = build_source_and_span("b")

    bundle = [src_used, span_used, fact_used, fact_unsupported, src_unused]

    print(f"bundle size: {len(bundle)} records")
    print(f"  - fact_used: supported")
    print(f"  - fact_unsupported: cites a span whose source is missing from the bundle")
    print(f"  - src_unused: never cited by any claim")

    graph = CitationGraph.from_bundle(bundle)
    print(f"\ngraph: {graph.size()} nodes, {graph.node_count_by_kind()}")
    print(f"dangling refs: {len(graph.dangling_refs)}")
    for d in graph.dangling_refs:
        print(f"  {d.from_id[:24]}... --{d.relation}--> {d.missing_id[:24]}... (missing)")

    unsupported = find_unsupported_claims(bundle)
    print(f"\nunsupported claims: {len(unsupported)}")
    for r in unsupported:
        print(f"  - {r.__class__.__name__} subject={getattr(r, 'subject', '?')!r}")

    unused = find_unused_sources(bundle)
    print(f"\nunused sources: {len(unused)}")
    for r in unused:
        print(f"  - SourceRecord({r.id[:24]}...) title={r.title!r}")

    print(f"\nfind_dangling_refs(): {[type(d).__name__ for d in find_dangling_refs(bundle)]}")


def demo_dict_records() -> None:
    """Dict-form records are also accepted by the graph builder."""
    section("4. Dict-form records (no dataclass needed)")

    src_dict = {
        "id": make_source_id("manual_note", "mem://x", "f" * 64),
        "schema_version": "1.0.0", "source_type": "manual_note",
        "title": "t", "origin_uri": None,
        "raw_ref": {"kind": "synthetic_fixture", "value": "mem://x"},
        "content_hash_sha256": "f" * 64,
        "captured_at": T_CAPTURED, "observed_at": None,
        "author_or_sender": None, "participants": [],
        "privacy_class": "public", "custody_status": "synthetic",
        "parser_version": "v1", "metadata": {},
    }
    graph = CitationGraph.from_bundle([src_dict])
    print(f"graph size: {graph.size()}, kinds: {graph.node_count_by_kind()}")
    print(f"node: {graph.get_node(src_dict['id'])}")


def main(argv: list[str] | None = None) -> int:
    demo_linear_chain()
    demo_diamond()
    demo_disconnected()
    demo_dict_records()
    print()
    print("=" * 70)
    print("Citation graph example complete.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
