"""Tests for the citation graph + provenance traversal primitives.

Coverage targets (per docs/specs/sprint_22_citation_graph.md):

1. CitationNode / CitationEdge / CitationPath / DanglingRef dataclasses
   are frozen and round-trip cleanly.
2. CitationGraph.from_bundle classifies records into source /
   evidence / claim kinds, builds edges between them, records
   dangling refs, and raises on cycles.
3. traverse / descendants / predecessors / shortest_path
   implement BFS over the graph with deterministic ordering.
4. find_unsupported_claims returns claims with no path to a
   source.
5. find_unused_sources returns sources with no path from a
   claim.
6. find_dangling_refs surfaces the same dangling refs the
   graph builder recorded, sorted deterministically.
7. The default predicates classify records correctly.
8. The public API is exported from agent_memory_contracts.
"""

from __future__ import annotations

import unittest
from typing import Any

from agent_memory_contracts import (
    CitationEdge,
    CitationGraph,
    CitationNode,
    CitationPath,
    DanglingRef,
    EpisodeRecord,
    EvidenceSpan,
    SourceRecord,
    default_claim_predicate,
    default_source_predicate,
    find_dangling_refs,
    find_unused_sources,
    find_unsupported_claims,
    make_episode_id,
    make_ledger_entry_id,
    make_reducer_decision_id,
    make_source_id,
    make_span_id,
)

from .fixtures import T_CAPTURED, T_DECIDED, T_EXTRACTED, build_source_and_span


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_another_source_and_span(
    suffix: str = "x",
    content_hash: str | None = None,
    span_hash: str | None = None,
) -> tuple[SourceRecord, EvidenceSpan]:
    """Build a second SourceRecord + EvidenceSpan with distinct content.

    Used to construct multi-node graphs in the test fixtures.
    """
    content_hash = content_hash or ("c" * 64)
    span_hash = span_hash or ("d" * 64)
    source_id = make_source_id("web_article", f"https://example.com/{suffix}", content_hash)
    source = SourceRecord.from_dict({
        "id": source_id,
        "schema_version": "1.0.0",
        "source_type": "web_article",
        "title": f"Article {suffix}",
        "origin_uri": f"https://example.com/{suffix}",
        "raw_ref": {"kind": "external_uri", "value": f"https://example.com/{suffix}"},
        "content_hash_sha256": content_hash,
        "captured_at": T_CAPTURED,
        "observed_at": T_CAPTURED,
        "author_or_sender": None,
        "participants": [],
        "privacy_class": "public",
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
        "privacy_class": "public",
        "metadata": {},
    })
    return source, span


def _build_episode(source_id: str) -> EpisodeRecord:
    # The make_episode_id function has signature
    # (source_id, episode_type, locator_kind, locator_value).
    episode_id = make_episode_id(
        source_id,
        "conversation_segment",
        "ordinal",
        "1",
    )
    return EpisodeRecord.from_dict({
        "id": episode_id,
        "schema_version": "1.0.0",
        "source_id": source_id,
        "episode_type": "conversation_segment",
        "episode_locator": {"kind": "ordinal", "value": "1"},
        "title": "Episode",
        "summary": "An episode of conversation.",
        "event_time_start": T_CAPTURED,
        "event_time_end": None,
        "actors": ["user"],
        "topics": ["memory"],
        "project_refs": [],
        "evidence_span_ids": [],
        "metadata": {},
    })


def _build_fact_ledger_entry(
    source_id: str,
    span_ids: list[str],
    subject: str = "s",
    predicate: str = "p",
    object_: str = "o",
    fact_text: str = "the fact",
) -> Any:
    """Build a FactLedgerEntry citing the given evidence span ids."""
    candidate_ids: list[str] = []
    reducer_id = make_reducer_decision_id(
        "fact", [], [], list(span_ids), "ok"
    )
    entry_id = make_ledger_entry_id(
        "fact",
        span_ids,
        {
            "ledger_type": "fact",
            "subject": subject,
            "predicate": predicate,
            "object": object_,
            "scope": "global",
            "valid_from": T_DECIDED,
            "evidence_span_ids": span_ids,
        },
    )
    from agent_memory_contracts import FactLedgerEntry
    return FactLedgerEntry.from_dict({
        "id": entry_id,
        "schema_version": "1.0.0",
        "ledger_type": "fact",
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "source_record_ids": [source_id],
        "episode_record_ids": [],
        "evidence_span_ids": span_ids,
        "candidate_ids": candidate_ids,
        "reducer_decision_id": reducer_id,
        "observed_at": None,
        "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED,
        "valid_until": None,
        "stale_after": None,
        "created_at": T_DECIDED,
        "updated_at": T_DECIDED,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {},
        "subject": subject,
        "predicate": predicate,
        "object": object_,
        "fact_text": fact_text,
    })


# ---------------------------------------------------------------------------
# Graph fixtures (per the spec test plan)
# ---------------------------------------------------------------------------


def _linear_chain_fixture() -> list[Any]:
    """One source, one evidence, one claim. Fully supported chain."""
    src, span = build_source_and_span()
    fact = _build_fact_ledger_entry(src.id, [span.id], subject="linear")
    return [src, span, fact]


def _diamond_fixture() -> list[Any]:
    """One source, two evidence spans, one claim citing both."""
    src, span_a = build_source_and_span()
    # Build a second evidence span that points to the SAME source.
    span_b_id = make_span_id(src.id, "line_range", "20-25")
    span_b = EvidenceSpan.from_dict({
        "id": span_b_id,
        "schema_version": "1.0.0",
        "source_id": src.id,
        "episode_id": None,
        "locator": {"kind": "line_range", "value": "20-25"},
        "text_excerpt": None,
        "excerpt_policy": "none",
        "span_hash_sha256": "d" * 64,
        "privacy_class": "public",
        "metadata": {},
    })
    fact = _build_fact_ledger_entry(src.id, [span_a.id, span_b.id], subject="diamond")
    return [src, span_a, span_b, fact]


def _disconnected_fixture() -> list[Any]:
    """An unsupported claim (no source for its span) and an unused source."""
    src_used, span_used = build_source_and_span()
    fact_used = _build_fact_ledger_entry(src_used.id, [span_used.id], subject="used")
    # Unsupported claim: a span with no source, and a fact citing it.
    dangling_span_id = make_span_id(
        "src_does_not_exist", "synthetic_locator", "0-1"
    )
    fact_unsupported = _build_fact_ledger_entry(
        "src_does_not_exist", [dangling_span_id], subject="unsupported"
    )
    # Unused source: a source with no claims citing it.
    src_unused, _ = _build_another_source_and_span("unused", content_hash="e" * 64, span_hash="f" * 64)
    return [src_used, span_used, fact_used, fact_unsupported, src_unused]


def _mixed_plane_fixture() -> list[Any]:
    """Two sources, two evidence spans, two claims of different types."""
    src1, span1 = build_source_and_span()
    src2, span2 = _build_another_source_and_span("two", content_hash="1" * 64, span_hash="2" * 64)
    fact = _build_fact_ledger_entry(src1.id, [span1.id], subject="fact")
    # A TasteCard from the second source/span.
    from agent_memory_contracts import TasteCard
    tc_id = make_ledger_entry_id(
        "taste",
        [span2.id],
        {
            "ledger_type": "taste",
            "subject": "taste",
            "taste_text": "use the spec",
            "domain": "architecture",
            "scope": "global",
            "valid_from": T_DECIDED,
            "evidence_span_ids": [span2.id],
        },
    )
    taste = TasteCard.from_dict({
        "id": tc_id,
        "schema_version": "1.0.0",
        "ledger_type": "taste",
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "source_record_ids": [src2.id],
        "episode_record_ids": [],
        "evidence_span_ids": [span2.id],
        "candidate_ids": [],
        "reducer_decision_id": make_reducer_decision_id("taste", [], [], [span2.id], "ok"),
        "observed_at": None,
        "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED,
        "valid_until": None,
        "stale_after": None,
        "created_at": T_DECIDED,
        "updated_at": T_DECIDED,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {},
        "subject": "taste",
        "taste_text": "use the spec",
        "domain": "architecture",
        "taste_type": "principle",
        "applicability": "universal",
    })
    return [src1, span1, fact, src2, span2, taste]


# ---------------------------------------------------------------------------
# Test classes
# ---------------------------------------------------------------------------


class TestCitationDataclasses(unittest.TestCase):
    """The frozen-dataclass surface area."""

    def test_citation_node_is_frozen(self) -> None:
        node = CitationNode(
            record_id="src_abc",
            node_kind="source",
            record_type="source_record",
            record={"id": "src_abc"},
        )
        with self.assertRaises(Exception):
            node.node_kind = "evidence"  # type: ignore[misc]

    def test_citation_edge_is_frozen(self) -> None:
        edge = CitationEdge(from_id="c1", to_id="s1", relation="cites")
        with self.assertRaises(Exception):
            edge.relation = "derives_from"  # type: ignore[misc]

    def test_citation_path_length_and_supported(self) -> None:
        a = CitationNode("a", "claim", "fact_ledger", {"id": "a"})
        b = CitationNode("b", "evidence", "evidence_span", {"id": "b"})
        c = CitationNode("c", "source", "source_record", {"id": "c"})
        path = CitationPath(
            start_id="a",
            end_id="c",
            nodes=(a, b, c),
            edges=(
                CitationEdge("a", "b", "cites"),
                CitationEdge("b", "c", "derives_from"),
            ),
        )
        self.assertEqual(path.length, 2)
        self.assertTrue(path.is_supported())

    def test_citation_path_unsupported(self) -> None:
        a = CitationNode("a", "claim", "fact_ledger", {"id": "a"})
        b = CitationNode("b", "evidence", "evidence_span", {"id": "b"})
        path = CitationPath(start_id="a", end_id="b", nodes=(a, b), edges=(CitationEdge("a", "b", "cites"),))
        self.assertFalse(path.is_supported())

    def test_dangling_ref_repr(self) -> None:
        d = DanglingRef(from_id="c1", missing_id="span_missing", relation="cites")
        self.assertIn("span_missing", repr(d))


class TestCitationGraphBuild(unittest.TestCase):
    """Building the graph from a bundle of records."""

    def test_empty_bundle(self) -> None:
        g = CitationGraph.from_bundle([])
        self.assertEqual(g.size(), 0)
        self.assertEqual(g.node_count_by_kind(), {"source": 0, "evidence": 0, "claim": 0})
        self.assertEqual(len(g.dangling_refs), 0)

    def test_linear_chain_size_and_counts(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        self.assertEqual(g.size(), 3)
        counts = g.node_count_by_kind()
        self.assertEqual(counts["source"], 1)
        self.assertEqual(counts["evidence"], 1)
        self.assertEqual(counts["claim"], 1)
        self.assertEqual(len(g.dangling_refs), 0)

    def test_diamond_size_and_counts(self) -> None:
        records = _diamond_fixture()
        g = CitationGraph.from_bundle(records)
        # 1 source, 2 evidence, 1 claim.
        self.assertEqual(g.size(), 4)
        self.assertEqual(g.node_count_by_kind()["source"], 1)
        self.assertEqual(g.node_count_by_kind()["evidence"], 2)
        self.assertEqual(g.node_count_by_kind()["claim"], 1)

    def test_dangling_ref_recorded(self) -> None:
        # A claim that references a span not in the bundle.
        records = _disconnected_fixture()
        g = CitationGraph.from_bundle(records)
        self.assertGreater(len(g.dangling_refs), 0)
        # The unsupported claim's evidence_span_id should be in the dangling list.
        missing_ids = {d.missing_id for d in g.dangling_refs}
        # The disconnected fixture has a fact with a dangling evidence span.
        self.assertEqual(len(missing_ids), 1)
        # And it should be a span_ id (we generated it via make_span_id).
        for mid in missing_ids:
            self.assertTrue(mid.startswith("span_"))

    def test_diamond_supports_full_chain(self) -> None:
        records = _diamond_fixture()
        g = CitationGraph.from_bundle(records)
        # Find the fact (claim).
        fact = next(r for r in records if hasattr(r, "fact_text"))
        paths = g.traverse(fact.id)
        # Two paths: one per evidence span.
        self.assertEqual(len(paths), 2)
        for p in paths:
            self.assertTrue(p.is_supported())
            self.assertEqual(p.length, 2)

    def test_episode_record_is_source_kind(self) -> None:
        src, _ = build_source_and_span()
        episode = _build_episode(src.id)
        g = CitationGraph.from_bundle([src, episode])
        kinds = [n.node_kind for n in g.nodes.values()]
        self.assertIn("source", kinds)
        self.assertEqual(sum(1 for k in kinds if k == "source"), 2)

    def test_non_graph_record_is_skipped(self) -> None:
        # A MemoryReducerDecision is not part of the citation graph.
        from agent_memory_contracts import MemoryReducerDecision, make_reducer_decision_id
        red_id = make_reducer_decision_id("archive", [], [], ["span_placeholder"], "noop")
        reducer = MemoryReducerDecision.from_dict({
            "id": red_id, "schema_version": "1.0.0", "decision_type": "archive",
            "target_candidate_ids": [], "target_ledger_entry_ids": [],
            "evidence_span_ids": ["span_placeholder"], "rationale": "noop",
            "decided_by": {"agent": "r", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
            "decided_at": T_DECIDED, "confidence": "high", "risk_class": "low",
            "checks": {"provenance": "pass", "temporal_validity": "pass",
                        "contradiction_scan": "pass", "privacy": "pass", "usefulness": "pass"},
            "metadata": {},
        })
        g = CitationGraph.from_bundle([reducer])
        self.assertEqual(g.size(), 0)

    def test_dict_records_classified_correctly(self) -> None:
        # Dict form of records is also accepted.
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
        g = CitationGraph.from_bundle([src_dict])
        self.assertEqual(g.size(), 1)
        node = g.get_node(src_dict["id"])
        self.assertIsNotNone(node)
        self.assertEqual(node.node_kind, "source")  # type: ignore[union-attr]


class TestCitationGraphTraversal(unittest.TestCase):
    """BFS traversal in forward, backward, and both directions."""

    def test_traverse_forward(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        fact = next(r for r in records if hasattr(r, "fact_text"))
        paths = g.traverse(fact.id, direction="forward")
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].nodes[0].record_id, fact.id)
        self.assertEqual(paths[0].nodes[-1].node_kind, "source")
        self.assertEqual(paths[0].length, 2)

    def test_traverse_backward_from_source(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        src = next(r for r in records if r.__class__.__name__ == "SourceRecord")
        paths = g.traverse(src.id, direction="backward")
        # backward from source should find the claim.
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].nodes[0].record_id, src.id)
        self.assertEqual(paths[0].nodes[-1].record_id, [r for r in records if hasattr(r, "fact_text")][0].id)

    def test_traverse_both(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        span = next(r for r in records if r.__class__.__name__ == "EvidenceSpan")
        paths_fwd = g.traverse(span.id, direction="forward")
        paths_bwd = g.traverse(span.id, direction="backward")
        paths_both = g.traverse(span.id, direction="both")
        # both = forward + backward, deduplicated by (start, end).
        self.assertEqual(len(paths_both), len(paths_fwd) + len(paths_bwd))

    def test_traverse_max_depth_zero(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        fact = next(r for r in records if hasattr(r, "fact_text"))
        paths = g.traverse(fact.id, max_depth=0)
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].length, 0)
        self.assertEqual(paths[0].nodes[0].record_id, fact.id)

    def test_traverse_max_depth_one(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        fact = next(r for r in records if hasattr(r, "fact_text"))
        paths = g.traverse(fact.id, max_depth=1)
        # Should stop at evidence; one path of length 1.
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].nodes[-1].node_kind, "evidence")

    def test_traverse_unknown_id_raises_keyerror(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        with self.assertRaises(KeyError):
            g.traverse("src_does_not_exist")

    def test_descendants_yield_terminal_nodes(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        fact = next(r for r in records if hasattr(r, "fact_text"))
        descs = list(g.descendants(fact.id))
        # descendants yields terminal nodes only; for a linear
        # chain (claim -> evidence -> source), the only terminal
        # descendant of the claim is the source.
        kinds = {d.node_kind for d in descs}
        self.assertIn("source", kinds)
        # And the source is reachable (its id matches the
        # SourceRecord's id).
        src = next(r for r in records if r.__class__.__name__ == "SourceRecord")
        self.assertEqual([d.record_id for d in descs], [src.id])

    def test_predecessors_yield_terminal_nodes(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        src = next(r for r in records if r.__class__.__name__ == "SourceRecord")
        preds = list(g.predecessors(src.id))
        kinds = {p.node_kind for p in preds}
        self.assertIn("claim", kinds)

    def test_shortest_path_found(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        src = next(r for r in records if r.__class__.__name__ == "SourceRecord")
        fact = next(r for r in records if hasattr(r, "fact_text"))
        path = g.shortest_path(fact.id, src.id)
        self.assertIsNotNone(path)
        self.assertEqual(path.nodes[0].record_id, fact.id)  # type: ignore[union-attr]
        self.assertEqual(path.nodes[-1].record_id, src.id)  # type: ignore[union-attr]

    def test_shortest_path_unreachable(self) -> None:
        records = _disconnected_fixture()
        g = CitationGraph.from_bundle(records)
        # Find a source that is unreachable from a fact.
        src_unused = [r for r in records if r.__class__.__name__ == "SourceRecord"][-1]
        fact_used = [r for r in records if hasattr(r, "fact_text")][0]
        self.assertIsNone(g.shortest_path(fact_used.id, src_unused.id))

    def test_shortest_path_self(self) -> None:
        records = _linear_chain_fixture()
        g = CitationGraph.from_bundle(records)
        fact = next(r for r in records if hasattr(r, "fact_text"))
        path = g.shortest_path(fact.id, fact.id)
        self.assertIsNotNone(path)
        self.assertEqual(path.length, 0)  # type: ignore[union-attr]


class TestFindUnsupportedClaims(unittest.TestCase):
    """The headline audit query for unsupported claims."""

    def test_linear_chain_all_supported(self) -> None:
        records = _linear_chain_fixture()
        self.assertEqual(find_unsupported_claims(records), [])

    def test_diamond_all_supported(self) -> None:
        records = _diamond_fixture()
        self.assertEqual(find_unsupported_claims(records), [])

    def test_disconnected_has_unsupported(self) -> None:
        records = _disconnected_fixture()
        unsupported = find_unsupported_claims(records)
        # The fixture has one fact whose evidence_span is dangling.
        self.assertEqual(len(unsupported), 1)
        self.assertTrue(hasattr(unsupported[0], "fact_text"))

    def test_custom_predicate(self) -> None:
        records = _linear_chain_fixture()
        # A predicate that matches nothing returns an empty list.
        self.assertEqual(
            find_unsupported_claims(records, claim_predicate=lambda r: False),
            [],
        )
        # A predicate that matches everything treats every record
        # as a claim. The SourceRecord is itself a source, so it
        # has no path to a (different) source -> unsupported.
        # The fact and the span are supported. So the result is
        # 1 unsupported record: the source.
        result = find_unsupported_claims(records, claim_predicate=lambda r: True)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].__class__.__name__, "SourceRecord")

    def test_empty_bundle(self) -> None:
        self.assertEqual(find_unsupported_claims([]), [])

    def test_results_sorted_by_id(self) -> None:
        records = _disconnected_fixture()
        unsupported = find_unsupported_claims(records)
        ids = [r.id for r in unsupported]
        self.assertEqual(ids, sorted(ids))


class TestFindUnusedSources(unittest.TestCase):
    """The inverse query: sources not cited by any claim."""

    def test_linear_chain_no_unused(self) -> None:
        records = _linear_chain_fixture()
        self.assertEqual(find_unused_sources(records), [])

    def test_disconnected_has_unused(self) -> None:
        records = _disconnected_fixture()
        unused = find_unused_sources(records)
        self.assertEqual(len(unused), 1)
        self.assertEqual(unused[0].__class__.__name__, "SourceRecord")

    def test_custom_predicate(self) -> None:
        records = _disconnected_fixture()
        # A predicate that matches nothing returns an empty list.
        self.assertEqual(
            find_unused_sources(records, source_predicate=lambda r: False),
            [],
        )

    def test_episode_record_included_by_default(self) -> None:
        src, _ = build_source_and_span()
        episode = _build_episode(src.id)
        # EpisodeRecord has no claims citing it -> unused.
        unused = find_unused_sources([src, episode])
        # The SourceRecord is cited (by anything? in this fixture nothing
        # cites it, so both are unused).
        self.assertEqual(len(unused), 2)


class TestFindDanglingRefs(unittest.TestCase):
    """The free function wrapper around the graph's dangling_refs."""

    def test_no_dangling_in_linear_chain(self) -> None:
        records = _linear_chain_fixture()
        self.assertEqual(find_dangling_refs(records), [])

    def test_dangling_in_disconnected(self) -> None:
        records = _disconnected_fixture()
        dangling = find_dangling_refs(records)
        self.assertGreater(len(dangling), 0)
        for d in dangling:
            self.assertIsInstance(d, DanglingRef)

    def test_dangling_sorted_deterministically(self) -> None:
        records = _disconnected_fixture()
        d1 = find_dangling_refs(records)
        d2 = find_dangling_refs(records)
        # Identical runs produce identical output.
        self.assertEqual(
            [repr(d) for d in d1],
            [repr(d) for d in d2],
        )


class TestPredicates(unittest.TestCase):
    """The default predicates classify records correctly."""

    def test_default_claim_predicate_with_dict(self) -> None:
        self.assertTrue(default_claim_predicate({"evidence_span_ids": ["span_a"]}))
        self.assertTrue(default_claim_predicate({"evidence_id": "span_a"}))
        self.assertFalse(default_claim_predicate({"foo": "bar"}))
        self.assertFalse(default_claim_predicate(None))

    def test_default_claim_predicate_with_dataclass(self) -> None:
        records = _linear_chain_fixture()
        fact = next(r for r in records if hasattr(r, "fact_text"))
        self.assertTrue(default_claim_predicate(fact))

    def test_default_source_predicate_with_dataclass(self) -> None:
        records = _linear_chain_fixture()
        src = next(r for r in records if r.__class__.__name__ == "SourceRecord")
        episode = _build_episode(src.id)
        self.assertTrue(default_source_predicate(src))
        self.assertTrue(default_source_predicate(episode))

    def test_default_source_predicate_with_dict(self) -> None:
        self.assertTrue(default_source_predicate({"source_type": "manual_note"}))
        self.assertTrue(default_source_predicate({"episode_type": "conversation_segment"}))
        self.assertFalse(default_source_predicate({"foo": "bar"}))


class TestPublicApi(unittest.TestCase):
    """All v0.8.0 names are exported from agent_memory_contracts."""

    def test_v080_exports_present(self) -> None:
        import agent_memory_contracts as a
        for name in (
            "CitationNode",
            "CitationEdge",
            "CitationPath",
            "DanglingRef",
            "CitationGraph",
            "find_unsupported_claims",
            "find_unused_sources",
            "find_dangling_refs",
            "default_claim_predicate",
            "default_source_predicate",
        ):
            self.assertTrue(
                hasattr(a, name),
                f"missing export: {name}",
            )

    def test_version_bumped_to_080(self) -> None:
        import agent_memory_contracts as a
        # Version is at least 0.8.0 (the citation graph release);
        # later sprints may bump it further.
        from packaging.version import Version
        self.assertGreaterEqual(Version(a.__version__), Version("0.8.0"))


if __name__ == "__main__":
    unittest.main()
