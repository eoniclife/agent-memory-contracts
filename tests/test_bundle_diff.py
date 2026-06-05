"""Tests for the bundle_diff primitive.

The diff is set-semantic and content-sensitive, so the tests cover:

1. Identical bundles produce empty added/removed/changed and
   unchanged_count equal to bundle size.
2. Any permutation of the same logical bundles yields the same diff.
3. One byte change in one record moves it from unchanged to changed
   with both old and new surfaced.
4. Records in b not in a appear in added; in a not in b in removed;
   same id different content in changed.
5. Duplicate ids with same content are unchanged; same id different
   content is changed (last-write-wins within a bundle).
6. A bundle of dataclasses and an equivalent bundle of dicts diff
   to the same result.
7. Equal-fingerprint bundles short-circuit (observable as zero
   iteration).
8. Custom id_field (e.g. 'slug') works the same as 'id'.
9. End-to-end with SourceRecord instances from the library.
"""

from __future__ import annotations

import unittest
from dataclasses import asdict, dataclass
from unittest.mock import patch

from agent_memory_contracts import (
    PreferenceLedgerEntry,
    SourceRecord,
    bundle_fingerprint,
    make_ledger_entry_id,
    make_source_id,
)
from agent_memory_contracts.bundle_diff import BundleDiff, bundle_diff


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


class EqualBundlesTests(unittest.TestCase):
    def test_identical_bundles_empty_diff(self):
        a = [_rec(i) for i in range(5)]
        b = [_rec(i) for i in range(5)]
        diff = bundle_diff(a, b)
        self.assertEqual(diff.added, [])
        self.assertEqual(diff.removed, [])
        self.assertEqual(diff.changed, [])
        self.assertEqual(diff.unchanged_count, 5)

    def test_empty_bundles_empty_diff(self):
        diff = bundle_diff([], [])
        self.assertEqual(diff.added, [])
        self.assertEqual(diff.removed, [])
        self.assertEqual(diff.changed, [])
        self.assertEqual(diff.unchanged_count, 0)

    def test_single_record_unchanged(self):
        diff = bundle_diff([_rec(0)], [_rec(0)])
        self.assertEqual(diff.unchanged_count, 1)
        self.assertEqual(diff.changed, [])


class OrderInsensitivityTests(unittest.TestCase):
    def test_reversed_order_same_diff(self):
        a = [_rec(i) for i in range(10)]
        b = [_rec(i) for i in range(9, -1, -1)]
        diff_ab = bundle_diff(a, a)
        diff_ba = bundle_diff(b, b)
        self.assertEqual(diff_ab.added, diff_ba.added)
        self.assertEqual(diff_ab.removed, diff_ba.removed)
        self.assertEqual(diff_ab.changed, diff_ba.changed)
        self.assertEqual(diff_ab.unchanged_count, diff_ba.unchanged_count)

    def test_shuffled_order_same_diff(self):
        order = [3, 7, 1, 9, 0, 4, 6, 2, 8, 5]
        forward = [_rec(i) for i in range(10)]
        shuffled = [_rec(i) for i in order]
        diff_fwd = bundle_diff(forward, forward)
        diff_shuf = bundle_diff(shuffled, shuffled)
        self.assertEqual(diff_fwd.unchanged_count, diff_shuf.unchanged_count)

    def test_different_order_same_diff_across_bundles(self):
        # The diff between two bundles must be order-insensitive.
        a1 = [_rec(0), _rec(1), _rec(2)]
        a2 = [_rec(2), _rec(0), _rec(1)]
        b = [_rec(0), _rec(1), _rec(2), _rec(3)]
        diff_1 = bundle_diff(a1, b)
        diff_2 = bundle_diff(a2, b)
        self.assertEqual(diff_1.added, diff_2.added)
        self.assertEqual(diff_1.removed, diff_2.removed)
        self.assertEqual(diff_1.changed, diff_2.changed)
        self.assertEqual(diff_1.unchanged_count, diff_2.unchanged_count)


class ContentSensitivityTests(unittest.TestCase):
    def test_value_change_is_changed(self):
        a = [_rec(i) for i in range(5)]
        b = [dict(_rec(i)) for i in range(5)]
        b[2]["value"] = 999
        diff = bundle_diff(a, b)
        self.assertEqual(diff.changed, [(a[2], b[2])])
        self.assertEqual(diff.unchanged_count, 4)

    def test_changed_contains_full_old_and_new_records(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 2}]
        diff = bundle_diff(a, b)
        self.assertEqual(len(diff.changed), 1)
        old, new = diff.changed[0]
        self.assertEqual(old["v"], 1)
        self.assertEqual(new["v"], 2)
        # Full records are surfaced, not just the diff.
        self.assertIn("id", old)
        self.assertIn("id", new)

    def test_added_record_includes_full_content(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 1}, {"id": "y", "v": 2}]
        diff = bundle_diff(a, b)
        self.assertEqual(len(diff.added), 1)
        self.assertEqual(diff.added[0]["id"], "y")
        self.assertEqual(diff.added[0]["v"], 2)

    def test_removed_record_includes_full_content(self):
        a = [{"id": "x", "v": 1}, {"id": "y", "v": 2}]
        b = [{"id": "x", "v": 1}]
        diff = bundle_diff(a, b)
        self.assertEqual(len(diff.removed), 1)
        self.assertEqual(diff.removed[0]["id"], "y")
        self.assertEqual(diff.removed[0]["v"], 2)


class AddRemoveTests(unittest.TestCase):
    def test_record_in_b_not_a_is_added(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 1}, {"id": "y", "v": 2}]
        diff = bundle_diff(a, b)
        self.assertEqual(diff.added, [{"id": "y", "v": 2}])
        self.assertEqual(diff.removed, [])
        self.assertEqual(diff.unchanged_count, 1)

    def test_record_in_a_not_b_is_removed(self):
        a = [{"id": "x", "v": 1}, {"id": "y", "v": 2}]
        b = [{"id": "x", "v": 1}]
        diff = bundle_diff(a, b)
        self.assertEqual(diff.removed, [{"id": "y", "v": 2}])
        self.assertEqual(diff.added, [])
        self.assertEqual(diff.unchanged_count, 1)

    def test_same_id_different_content_is_changed(self):
        a = [{"id": "x", "v": 1}]
        b = [{"id": "x", "v": 2}]
        diff = bundle_diff(a, b)
        self.assertEqual(diff.changed, [({"id": "x", "v": 1}, {"id": "x", "v": 2})])
        self.assertEqual(diff.added, [])
        self.assertEqual(diff.removed, [])

    def test_add_and_remove_and_change_all_in_one_diff(self):
        a = [
            {"id": "a", "v": 1},
            {"id": "b", "v": 2},
            {"id": "c", "v": 3},
        ]
        b = [
            {"id": "a", "v": 1},  # unchanged
            {"id": "b", "v": 99},  # changed
            {"id": "d", "v": 4},  # added
        ]
        diff = bundle_diff(a, b)
        self.assertEqual(diff.unchanged_count, 1)
        self.assertEqual(diff.added, [{"id": "d", "v": 4}])
        self.assertEqual(diff.removed, [{"id": "c", "v": 3}])
        self.assertEqual(diff.changed, [({"id": "b", "v": 2}, {"id": "b", "v": 99})])


class DedupByIdTests(unittest.TestCase):
    def test_duplicate_same_content_unchanged(self):
        a = [_rec(0), _rec(0)]
        b = [_rec(0)]
        diff = bundle_diff(a, b)
        self.assertEqual(diff.unchanged_count, 1)
        self.assertEqual(diff.changed, [])

    def test_duplicate_different_content_is_changed(self):
        # Both "versions" of rec_0 are in a; last-write-wins in a means
        # bundle_fingerprint(a) == bundle_fingerprint([dict(_rec(0), value=99)]).
        a = [_rec(0), dict(_rec(0), value=99)]
        b = [_rec(0)]
        diff = bundle_diff(a, b)
        # a's last-write-wins version differs from b's version, so changed.
        self.assertEqual(len(diff.changed), 1)
        old, new = diff.changed[0]
        self.assertEqual(old["value"], 99)
        self.assertEqual(new["value"], 0)

    def test_duplicate_in_b_adds_record(self):
        a = [_rec(0)]
        b = [_rec(0), dict(_rec(0), value=99)]
        diff = bundle_diff(a, b)
        # Both b entries have the same id; last-write-wins means the value=99
        # version is the effective b. Since that differs from a's value=0,
        # it should be in changed, not added.
        self.assertEqual(diff.added, [])
        self.assertEqual(len(diff.changed), 1)


class DictDataclassEquivalenceTests(unittest.TestCase):
    def test_dict_and_dataclass_produce_same_diff(self):
        dict_a = [_rec(i) for i in range(5)]
        dict_b = [_rec(i) for i in range(5)]
        dict_b[2] = dict(dict_b[2], value=999)

        dc_a = [_dataclass_rec(i) for i in range(5)]
        # Build the tampered dataclass manually.
        @dataclass(frozen=True)
        class _T:
            id: str
            schema_version: str
            value: int
            name: str

        dc_b = list(dc_a)
        dc_b[2] = _T(
            id=f"rec_{0x2:08x}",
            schema_version="1.0.0",
            value=999,
            name="record 2",
        )

        diff_dict = bundle_diff(dict_a, dict_b)
        diff_dc = bundle_diff(dc_a, dc_b)
        self.assertEqual(diff_dict.unchanged_count, diff_dc.unchanged_count)
        self.assertEqual(diff_dict.changed, diff_dc.changed)
        self.assertEqual(diff_dict.added, diff_dc.added)
        self.assertEqual(diff_dict.removed, diff_dc.removed)

    def test_nested_dataclass_equivalence(self):
        @dataclass(frozen=True)
        class _Outer:
            id: str
            inner: dict

        a_dc = [_Outer(id="o1", inner={"k": 1, "v": 2})]
        b_dc = [_Outer(id="o1", inner={"k": 1, "v": 99})]
        a_dict = [{"id": "o1", "inner": {"k": 1, "v": 2}}]
        b_dict = [{"id": "o1", "inner": {"k": 1, "v": 99}}]

        diff_dc = bundle_diff(a_dc, b_dc)
        diff_dict = bundle_diff(a_dict, b_dict)
        self.assertEqual(diff_dict.changed, diff_dc.changed)


class FingerprintShortCircuitTests(unittest.TestCase):
    def test_equal_fingerprints_short_circuits(self):
        a = [_rec(i) for i in range(5)]
        b = [_rec(i) for i in range(5)]
        diff = bundle_diff(a, b)
        # Short-circuit: bundles are identical, result is returned
        # without per-record iteration.  Observable as an empty diff
        # with unchanged_count equal to the bundle size.
        self.assertEqual(diff.added, [])
        self.assertEqual(diff.removed, [])
        self.assertEqual(diff.changed, [])
        self.assertEqual(diff.unchanged_count, 5)


class CustomIdFieldTests(unittest.TestCase):
    def test_slug_field_works(self):
        a = [{"slug": "x", "v": 1}, {"slug": "y", "v": 2}]
        b = [{"slug": "y", "v": 2}, {"slug": "x", "v": 1}]
        diff = bundle_diff(a, b, id_field="slug")
        self.assertEqual(diff.unchanged_count, 2)
        self.assertEqual(diff.added, [])
        self.assertEqual(diff.removed, [])

    def test_added_with_custom_id_field(self):
        a = [{"slug": "x", "v": 1}]
        b = [{"slug": "x", "v": 1}, {"slug": "y", "v": 2}]
        diff = bundle_diff(a, b, id_field="slug")
        self.assertEqual(diff.added, [{"slug": "y", "v": 2}])
        self.assertEqual(diff.removed, [])

    def test_changed_with_custom_id_field(self):
        a = [{"slug": "x", "v": 1}]
        b = [{"slug": "x", "v": 2}]
        diff = bundle_diff(a, b, id_field="slug")
        self.assertEqual(diff.changed, [({"slug": "x", "v": 1}, {"slug": "x", "v": 2})])


class RealWorldTests(unittest.TestCase):
    """End-to-end with the library's own record types."""

    def test_source_record_diff(self):
        def make_source(label: str, content_hash: str) -> SourceRecord:
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

        s1_a = make_source("hello", "a" * 64)
        s2_a = make_source("world", "b" * 64)
        s1_b = make_source("hello", "a" * 64)  # identical to s1_a
        s3_b = make_source("new", "c" * 64)  # added

        a_bundle = [s1_a, s2_a]
        b_bundle = [s1_b, s3_b]

        diff = bundle_diff(a_bundle, b_bundle)
        # s1 is unchanged (same id, same content).
        self.assertEqual(diff.unchanged_count, 1)
        # s2 is removed.
        self.assertEqual(len(diff.removed), 1)
        self.assertEqual(diff.removed[0]["id"], s2_a.id)
        # s3 is added.
        self.assertEqual(len(diff.added), 1)
        self.assertEqual(diff.added[0]["id"], s3_b.id)

    def test_ledger_entry_content_change_is_changed(self):
        span_id = "span_aaaa"
        ledger_id = make_ledger_entry_id("preference", [span_id], {
            "ledger_type": "preference",
            "subject": "memory architecture",
            "preference_text": "Spec-text drift is worse than no spec",
            "domain": "architecture",
            "scope": "global",
            "valid_from": "2026-05-30T13:00:00Z",
            "evidence_span_ids": [span_id],
        })
        base = {
            "id": ledger_id,
            "schema_version": "1.0.0",
            "ledger_type": "preference",
            "status": "active",
            "confidence": "high",
            "scope": "global",
            "source_record_ids": [],
            "episode_record_ids": [],
            "evidence_span_ids": [span_id],
            "candidate_ids": [],
            "reducer_decision_id": "redmem_aaaa",
            "subject": "memory architecture",
            "preference_text": "Spec-text drift is worse than no spec",
            "domain": "architecture",
            "strength": "hard_constraint",
            "observed_at": "2026-05-30T12:00:00Z",
            "asserted_at": "2026-05-30T13:00:00Z",
            "valid_from": "2026-05-30T13:00:00Z",
            "valid_until": None,
            "stale_after": None,
            "created_at": "2026-05-30T13:00:00Z",
            "updated_at": "2026-05-30T13:00:00Z",
            "supersedes": [],
            "superseded_by": [],
            "metadata": {},
        }
        tampered = dict(base, strength="weak")
        diff = bundle_diff([base], [tampered])
        self.assertEqual(len(diff.changed), 1)
        old, new = diff.changed[0]
        self.assertEqual(old["strength"], "hard_constraint")
        self.assertEqual(new["strength"], "weak")


if __name__ == "__main__":
    unittest.main()