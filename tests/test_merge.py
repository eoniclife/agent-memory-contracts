"""Tests for the merge_bundles primitive.

The merge is many-to-one and set-semantic, so the tests cover the
falsification properties of the contract:

1. Empty inputs produce an empty merge.
2. A single bundle round-trips: the merged records are the same set
   as the input records.
3. Disjoint inputs union cleanly: no conflicts, no duplicates.
4. Overlapping inputs with identical content do not surface
   conflicts.
5. Overlapping inputs with divergent content always surface the
   conflict, regardless of the ``prefer`` policy.
6. ``prefer="last"`` resolves to the last bundle's version.
7. ``prefer="first"`` resolves to the first bundle's version.
8. ``prefer="raise"`` raises ``ValueError`` on the first conflict.
9. Duplicate ids within a single input bundle are deduplicated
   silently (last-write-wins) and reported in ``duplicate_ids``.
10. ``id_field`` works for any identifier (e.g. ``"slug"``).
11. Mixed dict and dataclass inputs merge correctly.
12. End-to-end with a real ``SourceRecord`` from the library.
13. The merged records list is sorted by id (deterministic).
14. The conflicts list always contains the (id, [(idx, rec), ...])
    structure with full record dicts, even when ``prefer`` resolved
    them.
"""

from __future__ import annotations

import unittest
from dataclasses import asdict, dataclass
from typing import Any

from agent_memory_contracts import (
    BundleMerge,
    SourceRecord,
    make_source_id,
    merge_bundles,
)
from agent_memory_contracts.merge import BundleMerge as BundleMergeDirect


def _rec(i: int) -> dict:
    """Build a small synthetic record dict with a stable id."""
    return {
        "id": f"rec_{i:08x}",
        "schema_version": "1.0.0",
        "value": i,
        "name": f"record {i}",
    }


def _dataclass_rec(i: int):
    """Build an equivalent frozen dataclass with the same id."""

    @dataclass(frozen=True)
    class _R:
        id: str
        schema_version: str
        value: int
        name: str

    return _R(
        id=f"rec_{i:08x}",
        schema_version="1.0.0",
        value=i,
        name=f"record {i}",
    )


class EmptyInputsTests(unittest.TestCase):
    def test_no_bundles_returns_empty_merge(self):
        m = merge_bundles()
        self.assertEqual(m.records, [])
        self.assertEqual(m.conflicts, [])
        self.assertEqual(m.duplicate_ids, [])

    def test_single_empty_bundle_returns_empty_merge(self):
        m = merge_bundles([])
        self.assertEqual(m.records, [])
        self.assertEqual(m.conflicts, [])
        self.assertEqual(m.duplicate_ids, [])

    def test_multiple_empty_bundles_returns_empty_merge(self):
        m = merge_bundles([], [], [])
        self.assertEqual(m.records, [])
        self.assertEqual(m.conflicts, [])
        self.assertEqual(m.duplicate_ids, [])


class SingleBundleTests(unittest.TestCase):
    def test_single_bundle_returns_equivalent_records(self):
        bundle = [_rec(i) for i in range(5)]
        m = merge_bundles(bundle)
        # Records should equal the input set, sorted by id.
        self.assertEqual([r["id"] for r in m.records], sorted(r["id"] for r in bundle))
        self.assertEqual(len(m.records), 5)
        self.assertEqual(m.conflicts, [])
        self.assertEqual(m.duplicate_ids, [])

    def test_single_bundle_with_one_record(self):
        m = merge_bundles([_rec(42)])
        self.assertEqual(len(m.records), 1)
        self.assertEqual(m.records[0]["id"], "rec_0000002a")
        self.assertEqual(m.records[0]["value"], 42)


class DisjointBundlesTests(unittest.TestCase):
    def test_two_disjoint_bundles_union_without_conflicts(self):
        a = [_rec(i) for i in range(5)]
        b = [_rec(i) for i in range(5, 10)]
        m = merge_bundles(a, b)
        self.assertEqual(len(m.records), 10)
        self.assertEqual(m.conflicts, [])
        self.assertEqual(m.duplicate_ids, [])
        # The merged ids are the union, sorted.
        expected_ids = sorted([r["id"] for r in a] + [r["id"] for r in b])
        self.assertEqual([r["id"] for r in m.records], expected_ids)

    def test_three_disjoint_bundles_union_cleanly(self):
        a = [_rec(i) for i in range(0, 3)]
        b = [_rec(i) for i in range(3, 6)]
        c = [_rec(i) for i in range(6, 9)]
        m = merge_bundles(a, b, c)
        self.assertEqual(len(m.records), 9)
        self.assertEqual(m.conflicts, [])
        self.assertEqual(m.duplicate_ids, [])


class OverlappingSameContentTests(unittest.TestCase):
    def test_overlap_with_identical_content_no_conflicts(self):
        a = [_rec(i) for i in range(5)]
        b = [_rec(i) for i in range(2, 7)]  # 2..4 overlap with a, same content
        m = merge_bundles(a, b)
        # Unique ids: 0..6 = 7 records.
        self.assertEqual(len(m.records), 7)
        # No conflicts because the overlap is identical.
        self.assertEqual(m.conflicts, [])
        self.assertEqual(m.duplicate_ids, [])

    def test_three_way_overlap_with_identical_content(self):
        a = [_rec(i) for i in range(3)]
        b = [_rec(i) for i in range(3)]
        c = [_rec(i) for i in range(3)]
        m = merge_bundles(a, b, c)
        self.assertEqual(len(m.records), 3)
        self.assertEqual(m.conflicts, [])


class OverlappingDifferentContentTests(unittest.TestCase):
    def test_overlap_with_different_content_surfaces_conflict(self):
        a = [_rec(i) for i in range(3)]
        b = [_rec(i) for i in range(3)]
        # Tamper with b[1] so the id "rec_00000001" has different content.
        b[1] = dict(b[1], value=999)
        m = merge_bundles(a, b)
        # The conflict must be surfaced.
        self.assertEqual(len(m.conflicts), 1)
        conflict_id, entries = m.conflicts[0]
        self.assertEqual(conflict_id, "rec_00000001")
        # Two entries: bundle 0 with value=1, bundle 1 with value=999.
        self.assertEqual(len(entries), 2)
        # Entries are sorted by bundle index.
        self.assertEqual(entries[0][0], 0)
        self.assertEqual(entries[0][1]["value"], 1)
        self.assertEqual(entries[1][0], 1)
        self.assertEqual(entries[1][1]["value"], 999)

    def test_conflict_includes_full_record_dicts(self):
        a = [{"id": "x", "alpha": 1, "beta": 2}]
        b = [{"id": "x", "alpha": 1, "beta": 99}]
        m = merge_bundles(a, b)
        self.assertEqual(len(m.conflicts), 1)
        _id, entries = m.conflicts[0]
        # Each entry is a full record dict, not a partial diff.
        self.assertEqual(entries[0][1], {"id": "x", "alpha": 1, "beta": 2})
        self.assertEqual(entries[1][1], {"id": "x", "alpha": 1, "beta": 99})

    def test_conflicts_list_always_populated_with_prefer_resolved(self):
        # Even with prefer="last" silently resolving the conflict,
        # the conflicts list must still report the disagreement.
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 2}]
        m = merge_bundles(a, b, prefer="last")
        self.assertEqual(len(m.conflicts), 1)
        # And the winning record is the "last" one.
        self.assertEqual(m.records[0]["v"], 2)

    def test_conflicts_list_populated_with_prefer_first(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 2}]
        m = merge_bundles(a, b, prefer="first")
        self.assertEqual(len(m.conflicts), 1)
        # Winner is the "first" one.
        self.assertEqual(m.records[0]["v"], 1)


class PreferLastTests(unittest.TestCase):
    def test_prefer_last_winner_is_from_last_bundle(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 2}]
        c = [{"id": "x", "v": 3}]
        m = merge_bundles(a, b, c, prefer="last")
        self.assertEqual(len(m.records), 1)
        self.assertEqual(m.records[0]["v"], 3)
        # Conflict still reported.
        self.assertEqual(len(m.conflicts), 1)

    def test_prefer_last_default(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 99}]
        m_default = merge_bundles(a, b)
        m_explicit = merge_bundles(a, b, prefer="last")
        self.assertEqual(m_default.records, m_explicit.records)
        self.assertEqual(m_default.conflicts, m_explicit.conflicts)

    def test_prefer_last_disjoint_records_unaffected(self):
        a = [_rec(0), _rec(1)]
        b = [_rec(2), _rec(3)]
        c = [_rec(4), _rec(5)]
        m = merge_bundles(a, b, c, prefer="last")
        self.assertEqual(len(m.records), 6)
        self.assertEqual(m.conflicts, [])
        self.assertEqual(m.duplicate_ids, [])


class PreferFirstTests(unittest.TestCase):
    def test_prefer_first_winner_is_from_first_bundle(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 2}]
        c = [{"id": "x", "v": 3}]
        m = merge_bundles(a, b, c, prefer="first")
        self.assertEqual(len(m.records), 1)
        self.assertEqual(m.records[0]["v"], 1)
        # Conflict still reported.
        self.assertEqual(len(m.conflicts), 1)

    def test_prefer_first_with_three_way_conflict(self):
        a = [{"id": "x", "v": "A"}]
        b = [{"id": "x", "v": "B"}]
        c = [{"id": "x", "v": "C"}]
        m = merge_bundles(a, b, c, prefer="first")
        self.assertEqual(m.records[0]["v"], "A")
        # All three bundles contributed to the conflict entry.
        self.assertEqual(len(m.conflicts[0][1]), 3)


class PreferRaiseTests(unittest.TestCase):
    def test_prefer_raise_on_conflict_raises_valueerror(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 2}]
        with self.assertRaises(ValueError) as ctx:
            merge_bundles(a, b, prefer="raise")
        # Error message references the conflicting id and the policy.
        msg = str(ctx.exception)
        self.assertIn("x", msg)
        self.assertIn("raise", msg)

    def test_prefer_raise_no_conflict_returns_merge(self):
        a = [_rec(i) for i in range(3)]
        b = [_rec(i) for i in range(3, 6)]
        m = merge_bundles(a, b, prefer="raise")
        self.assertEqual(len(m.records), 6)
        self.assertEqual(m.conflicts, [])

    def test_prefer_raise_three_way_conflict_still_raises(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 2}]
        c = [{"id": "x", "v": 3}]
        with self.assertRaises(ValueError):
            merge_bundles(a, b, c, prefer="raise")

    def test_prefer_invalid_value_raises(self):
        with self.assertRaises(ValueError):
            merge_bundles([_rec(0)], [_rec(1)], prefer="bogus")


class DuplicateIdsInSingleBundleTests(unittest.TestCase):
    def test_duplicate_same_content_silently_resolved(self):
        bundle = [_rec(0), _rec(0)]
        m = merge_bundles(bundle)
        self.assertEqual(len(m.records), 1)
        self.assertEqual(m.records[0]["id"], "rec_00000000")
        # Last write wins, no other side-effect to report.
        self.assertEqual(m.conflicts, [])
        # The id was duplicated within this bundle.
        self.assertEqual(m.duplicate_ids, ["rec_00000000"])

    def test_duplicate_different_content_last_wins(self):
        bundle = [_rec(0), dict(_rec(0), value=99)]
        m = merge_bundles(bundle)
        # Last write wins, so value=99 is the merged record.
        self.assertEqual(len(m.records), 1)
        self.assertEqual(m.records[0]["value"], 99)
        # No cross-bundle conflict (only one bundle).
        self.assertEqual(m.conflicts, [])
        # The id was duplicated.
        self.assertEqual(m.duplicate_ids, ["rec_00000000"])

    def test_duplicate_ids_reported_across_bundles(self):
        a = [_rec(0), _rec(0), _rec(1)]
        b = [_rec(0)]
        m = merge_bundles(a, b)
        # The duplicate was in bundle 0.
        self.assertEqual(m.duplicate_ids, ["rec_00000000"])
        # No cross-bundle conflict (same content everywhere).
        self.assertEqual(m.conflicts, [])
        # Two unique records.
        self.assertEqual(len(m.records), 2)

    def test_duplicate_ids_reported_in_first_seen_order(self):
        a = [_rec(0), _rec(1), _rec(0)]   # rec_0 duplicated in bundle 0
        b = [_rec(2), _rec(1), _rec(2)]   # rec_2 duplicated in bundle 1
        m = merge_bundles(a, b)
        # rec_0 was first seen duplicated in bundle 0; rec_2 in bundle 1.
        self.assertEqual(m.duplicate_ids, ["rec_00000000", "rec_00000002"])


class CustomIdFieldTests(unittest.TestCase):
    def test_slug_field_merges_correctly(self):
        a = [{"slug": "x", "v": 1}, {"slug": "y", "v": 2}]
        b = [{"slug": "y", "v": 2}, {"slug": "z", "v": 3}]
        m = merge_bundles(a, b, id_field="slug")
        self.assertEqual(len(m.records), 3)
        self.assertEqual({r["slug"] for r in m.records}, {"x", "y", "z"})
        self.assertEqual(m.conflicts, [])

    def test_slug_field_surfaces_conflict(self):
        a = [{"slug": "x", "v": 1}]
        b = [{"slug": "x", "v": 2}]
        m = merge_bundles(a, b, id_field="slug")
        self.assertEqual(len(m.conflicts), 1)
        self.assertEqual(m.conflicts[0][0], "x")

    def test_slug_field_dedup_within_bundle(self):
        a = [{"slug": "x", "v": 1}, {"slug": "x", "v": 1}]
        m = merge_bundles(a, id_field="slug")
        self.assertEqual(len(m.records), 1)
        self.assertEqual(m.duplicate_ids, ["x"])


class MixedInputTypesTests(unittest.TestCase):
    def test_dicts_and_dataclasses_merge_correctly(self):
        dict_a = [_rec(i) for i in range(3)]
        dc_b = [_dataclass_rec(i) for i in range(2, 5)]
        m = merge_bundles(dict_a, dc_b)
        # 5 unique ids: 0..4
        self.assertEqual(len(m.records), 5)
        self.assertEqual(m.conflicts, [])

    def test_mixed_dict_and_dataclass_with_conflict(self):
        dict_a = [{"id": "x", "v": 1}]
        dc_b = [_dataclass_rec(0) if False else None]  # placeholder; rebuild
        # Build a dataclass that has the same id "x" but different content.
        @dataclass(frozen=True)
        class _X:
            id: str
            v: int

        dc_b = [_X(id="x", v=2)]
        m = merge_bundles(dict_a, dc_b)
        self.assertEqual(len(m.conflicts), 1)
        self.assertEqual(m.conflicts[0][0], "x")
        # Winner is from the last bundle (dc_b), so v=2.
        self.assertEqual(m.records[0]["v"], 2)

    def test_three_bundles_three_types(self):
        # dict bundle, dataclass bundle, and a third bundle as another dict.
        a = [_rec(i) for i in range(0, 2)]
        b = [_dataclass_rec(i) for i in range(2, 4)]
        c = [_rec(i) for i in range(4, 6)]
        m = merge_bundles(a, b, c)
        self.assertEqual(len(m.records), 6)
        self.assertEqual(m.conflicts, [])


class RealWorldTests(unittest.TestCase):
    """End-to-end with the library's own record types."""

    def _make_source(self, label: str, content_hash: str) -> SourceRecord:
        sid = make_source_id("chatgpt_conversation", "uri-x", content_hash)
        return SourceRecord.from_dict({
            "id": sid,
            "schema_version": "1.0.0",
            "source_type": "chatgpt_conversation",
            "title": f"transcript {label}",
            "origin_uri": "uri-x",
            "raw_ref": {"kind": "external_uri", "value": "uri-x"},
            "content_hash_sha256": content_hash,
            "captured_at": "2026-05-30T12:00:00Z",
            "observed_at": None,
            "author_or_sender": None,
            "participants": [],
            "privacy_class": "internal",
            "custody_status": "synthetic",
            "parser_version": "v1",
            "metadata": {"label": label},
        })

    def test_merge_source_records_disjoint(self):
        s1 = self._make_source("hello", "a" * 64)
        s2 = self._make_source("world", "b" * 64)
        bundle_a = [s1]
        bundle_b = [s2]
        m = merge_bundles(bundle_a, bundle_b)
        self.assertEqual(len(m.records), 2)
        self.assertEqual(m.conflicts, [])
        self.assertEqual({r["id"] for r in m.records}, {s1.id, s2.id})

    def test_merge_source_records_dict_and_dataclass_equivalent(self):
        # A bundle of dataclass instances and a bundle of equivalent
        # dicts must merge to the same result.
        s1 = self._make_source("hello", "a" * 64)
        s2 = self._make_source("world", "b" * 64)
        m_dc = merge_bundles([s1], [s2])
        m_dict = merge_bundles([asdict(s1)], [asdict(s2)])
        self.assertEqual(m_dc.records, m_dict.records)
        self.assertEqual(m_dc.conflicts, m_dict.conflicts)
        self.assertEqual(m_dc.duplicate_ids, m_dict.duplicate_ids)

    def test_merge_source_records_with_conflict(self):
        # Two bundles with the same source but different metadata:
        # same id (content-derived), different content.
        s_v1 = self._make_source("hello", "a" * 64)
        # Force a re-ingestion that produced different metadata.
        s_v2 = self._make_source("hello", "a" * 64)
        # Sanity: identical content yields identical id and the merge is clean.
        m_clean = merge_bundles([s_v1], [s_v2])
        self.assertEqual(len(m_clean.records), 1)
        self.assertEqual(m_clean.conflicts, [])

        # Now construct a *real* conflict: a record with the same id
        # but different content. Since the id is content-derived,
        # we have to construct it via from_dict to keep the id
        # fixed while varying other fields.
        sid = make_source_id("chatgpt_conversation", "uri-x", "a" * 64)
        s_base = SourceRecord.from_dict({
            "id": sid,
            "schema_version": "1.0.0",
            "source_type": "chatgpt_conversation",
            "title": "transcript hello",
            "origin_uri": "uri-x",
            "raw_ref": {"kind": "external_uri", "value": "uri-x"},
            "content_hash_sha256": "a" * 64,
            "captured_at": "2026-05-30T12:00:00Z",
            "observed_at": None,
            "author_or_sender": None,
            "participants": [],
            "privacy_class": "internal",
            "custody_status": "synthetic",
            "parser_version": "v1",
            "metadata": {"label": "v1"},
        })
        s_tampered = SourceRecord.from_dict({
            "id": sid,
            "schema_version": "1.0.0",
            "source_type": "chatgpt_conversation",
            "title": "transcript hello",
            "origin_uri": "uri-x",
            "raw_ref": {"kind": "external_uri", "value": "uri-x"},
            "content_hash_sha256": "a" * 64,
            "captured_at": "2026-05-30T12:00:00Z",
            "observed_at": None,
            "author_or_sender": None,
            "participants": [],
            "privacy_class": "internal",
            "custody_status": "synthetic",
            "parser_version": "v1",
            "metadata": {"label": "v2"},
        })
        m_conflict = merge_bundles([s_base], [s_tampered])
        self.assertEqual(len(m_conflict.records), 1)
        self.assertEqual(len(m_conflict.conflicts), 1)
        # The conflict entry references both bundles.
        cid, entries = m_conflict.conflicts[0]
        self.assertEqual(cid, sid)
        self.assertEqual({e[0] for e in entries}, {0, 1})


class DeterminismTests(unittest.TestCase):
    def test_output_sorted_by_id(self):
        # Bundles in reverse-id order. The merged records must be
        # sorted ascending by id, independent of input order.
        a = [_rec(i) for i in range(9, -1, -1)]
        b = [_rec(i) for i in range(19, 9, -1)]
        m = merge_bundles(a, b)
        ids = [r["id"] for r in m.records]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(ids[0], "rec_00000000")
        self.assertEqual(ids[-1], "rec_00000013")

    def test_conflicts_sorted_by_id(self):
        # Create conflicts on multiple ids. The conflicts list must
        # be sorted by id, not by bundle order.
        a = [
            {"id": "z", "v": 1},
            {"id": "a", "v": 1},
            {"id": "m", "v": 1},
        ]
        b = [
            {"id": "a", "v": 2},
            {"id": "m", "v": 2},
            {"id": "z", "v": 2},
        ]
        m = merge_bundles(a, b)
        # Three conflicts, sorted by id: a, m, z.
        self.assertEqual([c[0] for c in m.conflicts], ["a", "m", "z"])

    def test_determinism_with_shuffled_bundles(self):
        a = [_rec(i) for i in range(5)]
        b = [_rec(i) for i in range(3, 8)]
        c = [_rec(i) for i in range(7, 12)]
        # Shuffle the bundle argument order: the result is identical
        # for purely disjoint inputs.
        m1 = merge_bundles(a, b, c)
        m2 = merge_bundles(c, a, b)
        m3 = merge_bundles(b, c, a)
        self.assertEqual(m1.records, m2.records)
        self.assertEqual(m2.records, m3.records)
        self.assertEqual(m1.conflicts, m2.conflicts)
        self.assertEqual(m2.conflicts, m3.conflicts)

    def test_duplicate_ids_sorted_by_first_seen(self):
        # The duplicate_ids list preserves the order in which
        # duplicates were first observed (lowest bundle index,
        # then first appearance within that bundle).
        a = [_rec(0), _rec(1), _rec(0)]
        b = [_rec(2), _rec(1), _rec(1)]
        m = merge_bundles(a, b)
        # rec_0 was first duplicated in bundle a; rec_1 was first
        # duplicated in bundle a too (it appeared once, then again).
        self.assertEqual(m.duplicate_ids, ["rec_00000000", "rec_00000001"])


class PublicAPITests(unittest.TestCase):
    def test_bundle_merge_is_frozen_dataclass(self):
        m = merge_bundles([_rec(0)])
        # Frozen: assigning to a field raises AttributeError.
        with self.assertRaises((AttributeError, Exception)):
            m.records = []  # type: ignore[misc]

    def test_merge_bundles_is_importable_from_package(self):
        # The task brief required the new symbol to be exported
        # from the top-level package. Confirm it is.
        from agent_memory_contracts import merge_bundles as pkg_merge_bundles
        from agent_memory_contracts import BundleMerge as pkg_BundleMerge
        self.assertIs(pkg_merge_bundles, merge_bundles)
        self.assertIs(pkg_BundleMerge, BundleMergeDirect)

    def test_merge_bundles_default_prefer_is_last(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 2}]
        m_default = merge_bundles(a, b)
        m_explicit = merge_bundles(a, b, prefer="last")
        self.assertEqual(m_default.records[0]["v"], m_explicit.records[0]["v"])
        self.assertEqual(m_default.records[0]["v"], 2)


class ReturnTypeTests(unittest.TestCase):
    def test_returns_bundle_merge_instance(self):
        m = merge_bundles([_rec(0)])
        self.assertIsInstance(m, BundleMerge)

    def test_bundle_merge_default_construction(self):
        # The dataclass can be constructed with no arguments; defaults are empty.
        m = BundleMerge()
        self.assertEqual(m.records, [])
        self.assertEqual(m.conflicts, [])
        self.assertEqual(m.duplicate_ids, [])


if __name__ == "__main__":
    unittest.main()
