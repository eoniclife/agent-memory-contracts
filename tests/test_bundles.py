"""Tests for the bundle fingerprint primitive.

The fingerprint is content-derived and order-insensitive, so the
tests focus on three falsification properties:

1. Determinism (same input -> same hash).
2. Order-insensitivity (same records in any order -> same hash).
3. Content-sensitivity (any byte change in any record -> different
   hash).

Plus the round-trip property: a bundle of dataclass instances and
a bundle of equivalent dicts must produce the same hash, because
the library publishes both representations.
"""

from __future__ import annotations

import unittest
from dataclasses import asdict, dataclass, field
from typing import Any

from agent_memory_contracts import (
    PreferenceLedgerEntry,
    SourceRecord,
    bundle_fingerprint,
    make_ledger_entry_id,
    make_source_id,
)


def _rec(i: int) -> dict:
    """Build a small synthetic record dict with a stable id."""
    return {
        "id": f"rec_{i:08x}",
        "schema_version": "1.0.0",
        "value": i,
        "name": f"record {i}",
    }


def _dataclass_rec(i: int):
    """Build an equivalent dataclass with the same id."""

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


class DeterminismTests(unittest.TestCase):
    def test_same_input_same_hash(self):
        a = bundle_fingerprint([_rec(i) for i in range(5)])
        b = bundle_fingerprint([_rec(i) for i in range(5)])
        self.assertEqual(a, b)

    def test_empty_bundle_is_deterministic(self):
        self.assertEqual(bundle_fingerprint([]), bundle_fingerprint([]))

    def test_format_is_64_lowercase_hex(self):
        h = bundle_fingerprint([_rec(0)])
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))


class OrderInsensitivityTests(unittest.TestCase):
    def test_reversed_order_same_hash(self):
        forward = bundle_fingerprint([_rec(i) for i in range(10)])
        reversed_ = bundle_fingerprint([_rec(i) for i in range(9, -1, -1)])
        self.assertEqual(forward, reversed_)

    def test_shuffled_order_same_hash(self):
        forward = bundle_fingerprint([_rec(i) for i in range(10)])
        # A different deterministic shuffle.
        order = [3, 7, 1, 9, 0, 4, 6, 2, 8, 5]
        shuffled = bundle_fingerprint([_rec(i) for i in order])
        self.assertEqual(forward, shuffled)

    def test_single_element_is_trivially_order_insensitive(self):
        a = bundle_fingerprint([_rec(42)])
        b = bundle_fingerprint([_rec(42)])
        self.assertEqual(a, b)


class ContentSensitivityTests(unittest.TestCase):
    def test_value_change_changes_hash(self):
        baseline = bundle_fingerprint([_rec(i) for i in range(5)])
        tampered = bundle_fingerprint(
            [_rec(i) if i != 2 else dict(_rec(2), value=999) for i in range(5)]
        )
        self.assertNotEqual(baseline, tampered)

    def test_adding_record_changes_hash(self):
        baseline = bundle_fingerprint([_rec(i) for i in range(5)])
        extended = bundle_fingerprint([_rec(i) for i in range(6)])
        self.assertNotEqual(baseline, extended)

    def test_removing_record_changes_hash(self):
        baseline = bundle_fingerprint([_rec(i) for i in range(5)])
        shortened = bundle_fingerprint([_rec(i) for i in range(4)])
        self.assertNotEqual(baseline, shortened)

    def test_renaming_id_changes_hash(self):
        # If the id changes, the bundle is logically a different set.
        a = bundle_fingerprint([{"id": "rec_a", "value": 1}])
        b = bundle_fingerprint([{"id": "rec_b", "value": 1}])
        self.assertNotEqual(a, b)

    def test_key_reorder_same_hash(self):
        # JSON key order should not affect the hash; canonical JSON
        # sorts keys, so this is the falsification check that the
        # canonical serializer is actually being used.
        a = bundle_fingerprint([{"id": "x", "alpha": 1, "beta": 2}])
        b = bundle_fingerprint([{"id": "x", "beta": 2, "alpha": 1}])
        self.assertEqual(a, b)


class DedupByIdTests(unittest.TestCase):
    def test_duplicate_id_same_content_same_hash(self):
        once = bundle_fingerprint([_rec(0)])
        twice = bundle_fingerprint([_rec(0), _rec(0)])
        self.assertEqual(once, twice)

    def test_duplicate_id_different_content_last_wins(self):
        # The bundle is set-semantic: when the same id appears
        # with different content, the last occurrence wins.
        # The hash is deterministic for a given input order.
        first_wins = bundle_fingerprint(
            [_rec(0), dict(_rec(0), value=99)]
        )
        second_wins = bundle_fingerprint(
            [dict(_rec(0), value=99), _rec(0)]
        )
        self.assertNotEqual(first_wins, second_wins)
        # And the "last wins" is consistent with the canonical form
        # of the second occurrence alone.
        alone = bundle_fingerprint([_rec(0)])
        self.assertEqual(second_wins, alone)


class DictDataclassEquivalenceTests(unittest.TestCase):
    def test_dict_and_dataclass_produce_same_hash(self):
        # The library publishes both forms. A bundle built from
        # dataclass instances and a bundle built from equivalent
        # dicts must hash to the same value, because the underlying
        # contract is the same record.
        dataclass_bundle = bundle_fingerprint([_dataclass_rec(i) for i in range(5)])
        dict_bundle = bundle_fingerprint([_rec(i) for i in range(5)])
        self.assertEqual(dataclass_bundle, dict_bundle)

    def test_dataclass_with_nested_dict(self):
        @dataclass(frozen=True)
        class _Outer:
            id: str
            inner: dict

        a = bundle_fingerprint([_Outer(id="o1", inner={"k": 1, "v": 2})])
        b = bundle_fingerprint([{"id": "o1", "inner": {"k": 1, "v": 2}}])
        self.assertEqual(a, b)


class IdempotencyTests(unittest.TestCase):
    def test_repeated_runs_same_hash(self):
        # A pipeline that re-processes the same records should
        # always produce the same fingerprint.
        records = [_rec(i) for i in range(20)]
        first = bundle_fingerprint(records)
        for _ in range(5):
            self.assertEqual(bundle_fingerprint(records), first)


class RealWorldTests(unittest.TestCase):
    """End-to-end: build a real bundle from the library's own
    contracts and verify the fingerprint behaves as expected.

    These tests are a falsification check that the fingerprint
    works on the actual record types shipped by the library, not
    just on synthetic dicts.
    """

    def test_source_record_bundle(self):
        # Build two SourceRecord objects with DIFFERENT content
        # hashes (so their content-derived ids differ). This is
        # the realistic case: two distinct sources going into
        # one bundle.
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

        s1 = make_source("hello", "a" * 64)
        s2 = make_source("world", "b" * 64)
        # Sanity: the two sources must have different ids, otherwise
        # last-write-wins dominates and the order-sensitivity check
        # below is meaningless.
        self.assertNotEqual(s1.id, s2.id)

        # Dataclass bundle
        h_dataclass = bundle_fingerprint([s1, s2])
        # Equivalent dict bundle
        h_dict = bundle_fingerprint([asdict(s1), asdict(s2)])
        self.assertEqual(h_dataclass, h_dict)

        # Reordering the bundle should not change the hash
        # because the two records have distinct ids.
        h_reordered = bundle_fingerprint([s2, s1])
        self.assertEqual(h_dataclass, h_reordered)

    def test_ledger_bundle_changes_with_content(self):
        # The fingerprint must change when a ledger entry's
        # content changes. We use a real PreferenceLedgerEntry to
        # verify the integration is real.
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
            "schema_version": "1.1.0",
            "freshness_score": None,
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
        # Baseline
        h_baseline = bundle_fingerprint([base])
        # Tamper with the strength (one byte change)
        tampered = dict(base, strength="weak")
        h_tampered = bundle_fingerprint([tampered])
        self.assertNotEqual(h_baseline, h_tampered)
        # Build via the Python contract and verify the hash matches
        # the hash of the equivalent dict.
        entry = PreferenceLedgerEntry.from_dict(base)
        h_via_contract = bundle_fingerprint([entry])
        self.assertEqual(h_baseline, h_via_contract)


class CustomIdFieldTests(unittest.TestCase):
    def test_custom_id_field(self):
        # A bundle where the id field is named something other
        # than "id" should still work, via the id_field kwarg.
        a = bundle_fingerprint(
            [{"slug": "x", "value": 1}, {"slug": "y", "value": 2}],
            id_field="slug",
        )
        b = bundle_fingerprint(
            [{"slug": "y", "value": 2}, {"slug": "x", "value": 1}],
            id_field="slug",
        )
        self.assertEqual(a, b)

    def test_default_id_field_is_id(self):
        # Sanity check: when no id_field is passed, "id" is used.
        a = bundle_fingerprint([{"id": "a", "v": 1}, {"id": "b", "v": 2}])
        b = bundle_fingerprint(
            [{"id": "a", "v": 1}, {"id": "b", "v": 2}],
            id_field="id",
        )
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
