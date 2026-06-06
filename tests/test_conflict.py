"""Tests for the conflict resolution primitives (v0.7.0)."""

from __future__ import annotations

import unittest
from dataclasses import asdict

from agent_memory_contracts import (
    merge_bundles,
)
from agent_memory_contracts.bundle_diff import bundle_diff
from agent_memory_contracts.conflict import (
    ConflictResolution,
    apply_resolutions,
    resolve_conflict,
    validate_resolutions,
)


# --- Test fixtures ---

def _pref(s: str) -> dict:
    """A small preference record with a content-derived id shape."""
    return {
        "id": s,
        "schema_version": "1.0.0",
        "ledger_type": "preference",
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "subject": "memory architecture",
        "preference_text": "Use content-derived ids",
        "domain": "architecture",
        "strength": "hard_constraint",
        "valid_from": "2026-06-01T00:00:00Z",
        "metadata": {},
    }


def _conflict_two_variants() -> tuple[str, list[tuple[int, dict]]]:
    """A standard conflict: same id, two bundles, different content."""
    return (
        "pref_aaaaaaaa",
        [
            (0, _pref("pref_aaaaaaaa")),
            (1, {**_pref("pref_aaaaaaaa"), "preference_text":
                 "Use a different storage layer"}),
        ],
    )


def _conflict_three_variants() -> tuple[str, list[tuple[int, dict]]]:
    return (
        "pref_bbbbbbbb",
        [
            (0, _pref("pref_bbbbbbbb")),
            (1, {**_pref("pref_bbbbbbbb"),
                 "preference_text": "Variant B"}),
            (2, {**_pref("pref_bbbbbbbb"),
                 "preference_text": "Variant C"}),
        ],
    )


# --- resolve_conflict: basic ---

class ResolveConflictBasicTests(unittest.TestCase):
    def test_pick_first_variant(self):
        conflict = _conflict_two_variants()
        res = resolve_conflict(
            conflict, 0,
            resolved_by="alice",
            rationale="Design doc has empirical data, Slack thread is a comment.",
        )
        self.assertEqual(res.chosen_version_index, 0)
        self.assertIsNotNone(res.chosen_record)
        self.assertEqual(res.chosen_record["preference_text"],
                         "Use content-derived ids")
        self.assertEqual(res.rejected_record_ids, ["pref_aaaaaaaa"])
        self.assertEqual(res.resolved_by, "alice")
        self.assertTrue(res.id.startswith("confres_"))
        self.assertTrue(res.rationale.startswith("Design doc"))
        self.assertIsNone(res.superseded_at)
        self.assertEqual(res.schema_version, "1.0.0")

    def test_pick_second_variant(self):
        conflict = _conflict_two_variants()
        res = resolve_conflict(
            conflict, 1,
            resolved_by="bob",
            rationale="Picking the second variant explicitly",
        )
        self.assertEqual(res.chosen_version_index, 1)
        self.assertEqual(res.chosen_record["preference_text"],
                         "Use a different storage layer")
        self.assertEqual(res.rejected_record_ids, ["pref_aaaaaaaa"])

    def test_merge_resolution_produces_synthetic_record(self):
        conflict = _conflict_three_variants()
        res = resolve_conflict(
            conflict, "merge",
            resolved_by="team_lead",
            rationale="No single winner; merging fields across all three",
        )
        self.assertEqual(res.chosen_version_index, -1)
        self.assertIsNotNone(res.chosen_record)
        # The synthetic record's id is the conflict_id so it can
        # replace the variants in the bundle.
        self.assertEqual(res.chosen_record["id"], "pref_bbbbbbbb")
        # The preference_text should be the last variant's value
        # (last-write-wins by bundle index).
        self.assertEqual(res.chosen_record["preference_text"],
                         "Variant C")
        # All three variants are rejected.
        self.assertEqual(len(res.rejected_record_ids), 3)

    def test_split_resolution_has_no_chosen_record(self):
        conflict = _conflict_two_variants()
        res = resolve_conflict(
            conflict, "split",
            resolved_by="reducer_review",
            rationale=(
                "Same id but the two records encode different facts; "
                "the correct fix is to deprecate the id and create "
                "two new memories with distinct ids."
            ),
        )
        self.assertEqual(res.chosen_version_index, -2)
        self.assertIsNone(res.chosen_record)
        self.assertEqual(len(res.rejected_record_ids), 2)


# --- resolve_conflict: failure cases (per spec) ---

class ResolveConflictFailureTests(unittest.TestCase):
    def setUp(self):
        self.conflict = _conflict_two_variants()

    def test_chosen_int_out_of_range(self):
        with self.assertRaises(ValueError) as cm:
            resolve_conflict(self.conflict, 99,
                             resolved_by="alice",
                             rationale="picking out of range")
        self.assertIn("chosen index 99 out of range", str(cm.exception))
        self.assertIn("2 variants", str(cm.exception))

    def test_chosen_negative_int(self):
        with self.assertRaises(ValueError) as cm:
            resolve_conflict(self.conflict, -1,
                             resolved_by="alice",
                             rationale="negative index is out of range")
        self.assertIn("chosen index -1 out of range", str(cm.exception))

    def test_chosen_unknown_string(self):
        with self.assertRaises(ValueError) as cm:
            resolve_conflict(self.conflict, "pick_one",
                             resolved_by="alice",
                             rationale="picking by string label")
        self.assertIn("chosen must be an int", str(cm.exception))
        self.assertIn("'merge', or 'split'", str(cm.exception))
        self.assertIn("'pick_one'", str(cm.exception))

    def test_chosen_bool_rejected(self):
        # bool is a subclass of int; reject explicitly.
        with self.assertRaises(ValueError) as cm:
            resolve_conflict(self.conflict, True,
                             resolved_by="alice",
                             rationale="using bool True as index")
        self.assertIn("chosen must be an int", str(cm.exception))

    def test_chosen_type_invalid(self):
        with self.assertRaises(TypeError):
            resolve_conflict(self.conflict, 1.5,
                             resolved_by="alice",
                             rationale="float is not int or str")

    def test_resolved_by_empty(self):
        with self.assertRaises(ValueError) as cm:
            resolve_conflict(self.conflict, 0,
                             resolved_by="",
                             rationale="empty resolved_by rejected")
        self.assertIn("resolved_by is required", str(cm.exception))

    def test_resolved_by_whitespace_only(self):
        with self.assertRaises(ValueError):
            resolve_conflict(self.conflict, 0,
                             resolved_by="   ",
                             rationale="whitespace resolved_by rejected")

    def test_rationale_too_short(self):
        with self.assertRaises(ValueError) as cm:
            resolve_conflict(self.conflict, 0,
                             resolved_by="alice",
                             rationale="fix")
        self.assertIn("rationale must be at least 10 characters", str(cm.exception))
        self.assertIn("got 3", str(cm.exception))

    def test_rationale_empty(self):
        with self.assertRaises(ValueError):
            resolve_conflict(self.conflict, 0,
                             resolved_by="alice",
                             rationale="")

    def test_rationale_whitespace_only(self):
        with self.assertRaises(ValueError):
            resolve_conflict(self.conflict, 0,
                             resolved_by="alice",
                             rationale="          ")

    def test_resolved_at_malformed(self):
        with self.assertRaises(ValueError) as cm:
            resolve_conflict(self.conflict, 0,
                             resolved_by="alice",
                             rationale="malformed resolved_at",
                             resolved_at="not-iso")
        self.assertIn("resolved_at must be ISO 8601", str(cm.exception))
        self.assertIn("'not-iso'", str(cm.exception))

    def test_resolved_at_z_suffix_accepted(self):
        # The library's canonical form uses Z; ensure we accept it.
        res = resolve_conflict(self.conflict, 0,
                               resolved_by="alice",
                               rationale="test Z suffix accepted",
                               resolved_at="2026-06-06T12:00:00Z")
        self.assertEqual(res.resolved_at, "2026-06-06T12:00:00Z")

    def test_merge_with_empty_variants_raises(self):
        empty_conflict: tuple[str, list[tuple[int, dict]]] = (
            "pref_cccccccc", [],
        )
        with self.assertRaises(ValueError) as cm:
            resolve_conflict(empty_conflict, "merge",
                             resolved_by="alice",
                             rationale="merge with no variants raises")
        self.assertIn("cannot merge a conflict with 0 variants",
                      str(cm.exception))

    def test_merge_preserves_conflict_id_on_synthetic_record(self):
        # Even with multiple variants, the synthetic record keeps
        # the conflict_id so it can replace the variants in the
        # bundle.
        conflict = _conflict_two_variants()
        res = resolve_conflict(conflict, "merge",
                               resolved_by="alice",
                               rationale="merging variants for test")
        self.assertEqual(res.chosen_record["id"], "pref_aaaaaaaa")


# --- resolve_conflict: id derivation ---

class ConflictResolutionIdTests(unittest.TestCase):
    def test_id_is_content_derived(self):
        conflict = _conflict_two_variants()
        res1 = resolve_conflict(conflict, 0,
                                resolved_by="alice",
                                rationale="same input -> same id",
                                resolved_at="2026-06-06T12:00:00Z")
        res2 = resolve_conflict(conflict, 0,
                                resolved_by="alice",
                                rationale="same input -> same id",
                                resolved_at="2026-06-06T12:00:00Z")
        self.assertEqual(res1.id, res2.id)

    def test_id_changes_with_resolved_at(self):
        conflict = _conflict_two_variants()
        res1 = resolve_conflict(conflict, 0,
                                resolved_by="alice",
                                rationale="different resolved_at",
                                resolved_at="2026-06-06T12:00:00Z")
        res2 = resolve_conflict(conflict, 0,
                                resolved_by="alice",
                                rationale="different resolved_at",
                                resolved_at="2026-06-06T13:00:00Z")
        self.assertNotEqual(res1.id, res2.id)

    def test_id_changes_with_chosen(self):
        conflict = _conflict_two_variants()
        res1 = resolve_conflict(conflict, 0,
                                resolved_by="alice",
                                rationale="different chosen")
        res2 = resolve_conflict(conflict, 1,
                                resolved_by="alice",
                                rationale="different chosen")
        self.assertNotEqual(res1.id, res2.id)

    def test_id_changes_with_policy(self):
        conflict = _conflict_two_variants()
        res_pick = resolve_conflict(conflict, 0,
                                   resolved_by="alice",
                                   rationale="pick policy")
        res_merge = resolve_conflict(conflict, "merge",
                                    resolved_by="alice",
                                    rationale="merge policy")
        res_split = resolve_conflict(conflict, "split",
                                    resolved_by="alice",
                                    rationale="split policy")
        self.assertEqual(len({res_pick.id, res_merge.id, res_split.id}), 3)

    def test_id_starts_with_confres_prefix(self):
        conflict = _conflict_two_variants()
        res = resolve_conflict(conflict, 0,
                               resolved_by="alice",
                               rationale="test id prefix")
        self.assertTrue(res.id.startswith("confres_"))
        # The hex part is 24 chars (matching the rest of the library).
        hex_part = res.id[len("confres_"):]
        self.assertEqual(len(hex_part), 24)
        # And it's all lowercase hex.
        self.assertTrue(all(c in "0123456789abcdef" for c in hex_part))


# --- apply_resolutions ---

class ApplyResolutionsTests(unittest.TestCase):
    def _bundle_with_conflict(self):
        return [
            _pref("pref_aaaaaaaa"),  # the variant with the original text
            _pref("pref_bbbbbbbb"),
        ]

    def test_apply_pick_one_keeps_both_variants(self):
        # User said: keep the variants. apply_resolutions for a
        # pick-one resolution NEVER replaces the rejected
        # variant; both the chosen (added) and the rejected
        # (flagged) stay in the bundle.
        #
        # Real-world usage: the conflict comes from
        # merge_bundles; we apply the resolution to one of the
        # input bundles (which contains only the rejected
        # variant of the conflict). The chosen is added, the
        # rejected is flagged.
        conflict = _conflict_two_variants()
        # Bundle contains the rejected variant (variant 1) but
        # NOT the chosen (variant 0). This is the typical
        # post-merge scenario: bundle_b is one of the input
        # bundles to merge_bundles, so it has only one variant.
        rejected_record = {**_pref("pref_aaaaaaaa"),
                            "preference_text":
                            "Use a different storage layer"}
        bundle = [rejected_record]

        res = resolve_conflict(conflict, 0,
                               resolved_by="alice",
                               rationale="keeping the first variant")
        new_bundle = apply_resolutions(bundle, [res])

        # The bundle now has 2 records: the chosen (added with
        # resolved_conflict_ids) and the original rejected
        # variant (flagged).
        self.assertEqual(len(new_bundle), 2)
        chosen = next(r for r in new_bundle
                      if r.get("id") == "pref_aaaaaaaa"
                      and r.get("preference_text")
                      == "Use content-derived ids")
        rejected = next(r for r in new_bundle
                        if r.get("id") == "pref_aaaaaaaa"
                        and r.get("preference_text")
                        == "Use a different storage layer")
        # Chosen carries the audit chain.
        self.assertIn("resolved_conflict_ids", chosen)
        self.assertEqual(chosen["resolved_conflict_ids"], [res.id])
        # Rejected carries the supersession link.
        self.assertEqual(
            rejected.get("superseded_by_conflict_resolution"), res.id)
        self.assertIn(res.id, rejected.get("superseded_by", []))

    def test_apply_split_keeps_variants(self):
        # User said: keep the variants, compress later.
        # The user said keep the variants. apply_resolutions for a
        # split does NOT remove the variants; it just flags them.
        conflict = _conflict_two_variants()
        bundle = [
            _pref("pref_aaaaaaaa"),
            {**_pref("pref_aaaaaaaa"),
             "preference_text": "Use a different storage layer"},
        ]
        res = resolve_conflict(
            conflict, "split",
            resolved_by="alice",
            rationale=(
                "Same id but semantically different records; defer "
                "to v0.8.0 for the proper id-deprecation workflow."
            ),
        )
        new_bundle = apply_resolutions(bundle, [res])
        # Both variants stay.
        pref_aaa_records = [r for r in new_bundle
                            if r.get("id") == "pref_aaaaaaaa"]
        self.assertEqual(len(pref_aaa_records), 2)
        # Both are flagged.
        for r in pref_aaa_records:
            self.assertEqual(
                r.get("superseded_by_conflict_resolution"), res.id)
        # No new chosen record.
        self.assertIsNone(res.chosen_record)

    def test_apply_merge_replaces_with_synthetic(self):
        conflict = _conflict_two_variants()
        bundle = [
            _pref("pref_aaaaaaaa"),
            {**_pref("pref_aaaaaaaa"),
             "preference_text": "Use a different storage layer"},
        ]
        res = resolve_conflict(conflict, "merge",
                               resolved_by="alice",
                               rationale="merging two variants")
        new_bundle = apply_resolutions(bundle, [res])
        # The synthetic record replaces the chosen id.
        chosen_records = [r for r in new_bundle
                          if r.get("id") == "pref_aaaaaaaa"]
        # The first one (originally position 0) is replaced by
        # the synthetic merge; the second one (rejected) is
        # flagged. So we have 2 records with the same id, but
        # one is the chosen synthetic and one is the rejected.
        synthetic = [r for r in chosen_records
                    if "resolved_conflict_ids" in r]
        self.assertEqual(len(synthetic), 1)
        self.assertEqual(synthetic[0]["resolved_conflict_ids"], [res.id])

    def test_apply_does_not_mutate_input(self):
        conflict = _conflict_two_variants()
        bundle = [_pref("pref_aaaaaaaa")]
        res = resolve_conflict(conflict, 0,
                               resolved_by="alice",
                               rationale="immutability test")
        new_bundle = apply_resolutions(bundle, [res])
        # The input is unchanged.
        self.assertNotIn("resolved_conflict_ids", bundle[0])
        self.assertNotIn("superseded_by_conflict_resolution", bundle[0])
        # The output has both records: the chosen (added) and
        # the rejected (flagged).
        self.assertEqual(len(new_bundle), 2)

    def test_apply_with_two_resolutions_does_not_double_apply(self):
        # Two separate conflicts resolved independently.
        conflict1 = ("pref_aaaaaaaa",
                     [(0, _pref("pref_aaaaaaaa")),
                      (1, {**_pref("pref_aaaaaaaa"),
                           "preference_text": "B"})])
        conflict2 = ("pref_bbbbbbbb",
                    [(0, _pref("pref_bbbbbbbb")),
                     (1, {**_pref("pref_bbbbbbbb"),
                          "preference_text": "B2"})])
        bundle = [
            _pref("pref_aaaaaaaa"),
            {**_pref("pref_aaaaaaaa"), "preference_text": "B"},
            _pref("pref_bbbbbbbb"),
            {**_pref("pref_bbbbbbbb"), "preference_text": "B2"},
        ]
        res1 = resolve_conflict(conflict1, 0,
                                resolved_by="alice",
                                rationale="resolving first conflict")
        res2 = resolve_conflict(conflict2, 0,
                                resolved_by="alice",
                                rationale="resolving second conflict")
        new_bundle = apply_resolutions(bundle, [res1, res2])
        # Both ids should have a chosen record with the
        # resolved_conflict_ids field set.
        for rid, expected_res in [("pref_aaaaaaaa", res1),
                                 ("pref_bbbbbbbb", res2)]:
            chosen = [r for r in new_bundle
                      if r.get("id") == rid
                      and "resolved_conflict_ids" in r]
            self.assertEqual(len(chosen), 1,
                             f"expected one chosen record for {rid}")
            self.assertEqual(chosen[0]["resolved_conflict_ids"],
                             [expected_res.id])

    def test_apply_raises_on_duplicate_conflict_id(self):
        conflict = _conflict_two_variants()
        bundle = [_pref("pref_aaaaaaaa")]
        res1 = resolve_conflict(conflict, 0,
                                resolved_by="alice",
                                rationale="first resolution")
        # Build a second resolution with the same conflict_id.
        # We use from_dict so we can override the id to match.
        res2_dict = asdict(res1)
        res2_dict["resolved_at"] = "2026-06-06T13:00:00Z"
        res2_dict["rationale"] = "second resolution with same conflict"
        res2 = ConflictResolution.from_dict(res2_dict)
        # Wait -- res2 has the same id as res1 because the content
        # is identical except for resolved_at. We need a different
        # resolution. Let me build a different one.
        # Actually for this test, the goal is to verify that
        # apply_resolutions rejects two resolutions on the same
        # conflict_id. We can achieve that by mutating the rationale.
        res2_dict["rationale"] = "a different rationale for the second"
        res2 = ConflictResolution.from_dict(res2_dict)
        # Now res1.id != res2.id (different rationale), but both
        # have the same conflict_id. apply_resolutions should
        # reject.
        with self.assertRaises(ValueError) as cm:
            apply_resolutions(bundle, [res1, res2])
        self.assertIn("two resolutions target the same conflict_id",
                      str(cm.exception))

    def test_apply_raises_on_missing_conflict_records(self):
        # Resolution refers to a conflict whose records are NOT
        # in the bundle.
        conflict = ("pref_zzzzzzzz",  # different id
                    [(0, _pref("pref_zzzzzzzz")),
                     (1, {**_pref("pref_zzzzzzzz"),
                          "preference_text": "B"})])
        bundle = [_pref("pref_aaaaaaaa")]
        res = resolve_conflict(conflict, 0,
                               resolved_by="alice",
                               rationale="missing conflict id test")
        with self.assertRaises(ValueError) as cm:
            apply_resolutions(bundle, [res])
        self.assertIn("no matching records found in bundle",
                      str(cm.exception))

    def test_apply_does_not_mutate_resolutions(self):
        conflict = _conflict_two_variants()
        bundle = [_pref("pref_aaaaaaaa")]
        res = resolve_conflict(conflict, 0,
                               resolved_by="alice",
                               rationale="resolution immutability test")
        original_chosen = copy.deepcopy(res.chosen_record)
        apply_resolutions(bundle, [res])
        # The resolution is unchanged.
        self.assertEqual(res.chosen_record, original_chosen)


# Need copy here too.
import copy


# --- validate_resolutions ---

class ValidateResolutionsTests(unittest.TestCase):
    def test_valid_resolution_returns_empty_list(self):
        conflict = _conflict_two_variants()
        bundle = [_pref("pref_aaaaaaaa"),
                  {**_pref("pref_aaaaaaaa"),
                   "preference_text": "B"}]
        res = resolve_conflict(conflict, 0,
                               resolved_by="alice",
                               rationale="valid resolution test")
        errors = validate_resolutions(bundle, [res])
        self.assertEqual(errors, [])

    def test_invalid_resolution_returns_error_messages(self):
        # Empty bundle.
        conflict = _conflict_two_variants()
        bundle: list[dict] = []
        res = resolve_conflict(conflict, 0,
                               resolved_by="alice",
                               rationale="no records in bundle test")
        errors = validate_resolutions(bundle, [res])
        self.assertGreater(len(errors), 0)
        self.assertTrue(any("no matching records" in e for e in errors))

    def test_duplicate_conflict_id_collected_as_error(self):
        conflict = _conflict_two_variants()
        bundle = [_pref("pref_aaaaaaaa"),
                  {**_pref("pref_aaaaaaaa"),
                   "preference_text": "B"}]
        res1 = resolve_conflict(conflict, 0,
                                resolved_by="alice",
                                rationale="first resolution")
        res2_dict = asdict(res1)
        res2_dict["rationale"] = "different rationale for the second"
        res2 = ConflictResolution.from_dict(res2_dict)
        errors = validate_resolutions(bundle, [res1, res2])
        self.assertTrue(any("resolved by more than one resolution"
                           in e for e in errors))

    def test_invalid_resolved_at_collected_as_error(self):
        conflict = _conflict_two_variants()
        bundle = [_pref("pref_aaaaaaaa"),
                  {**_pref("pref_aaaaaaaa"),
                   "preference_text": "B"}]
        # Build a resolution with malformed resolved_at by
        # using from_dict directly (which doesn't validate).
        res = resolve_conflict(conflict, 0,
                               resolved_by="alice",
                               rationale="malformed resolved_at test")
        # Manually overwrite the resolved_at to something bad.
        bad_dict = asdict(res)
        bad_dict["resolved_at"] = "not-iso"
        bad_res = ConflictResolution.from_dict(bad_dict)
        errors = validate_resolutions(bundle, [bad_res])
        self.assertTrue(any("ISO 8601" in e for e in errors))

    def test_empty_resolved_by_collected_as_error(self):
        conflict = _conflict_two_variants()
        bundle = [_pref("pref_aaaaaaaa"),
                  {**_pref("pref_aaaaaaaa"),
                   "preference_text": "B"}]
        res = resolve_conflict(conflict, 0,
                               resolved_by="alice",
                               rationale="empty resolved_by test")
        bad_dict = asdict(res)
        bad_dict["resolved_by"] = ""
        bad_res = ConflictResolution.from_dict(bad_dict)
        errors = validate_resolutions(bundle, [bad_res])
        self.assertTrue(any("resolved_by is required" in e
                           for e in errors))

    def test_short_rationale_collected_as_error(self):
        conflict = _conflict_two_variants()
        bundle = [_pref("pref_aaaaaaaa"),
                  {**_pref("pref_aaaaaaaa"),
                   "preference_text": "B"}]
        res = resolve_conflict(conflict, 0,
                               resolved_by="alice",
                               rationale="x" * 11)
        bad_dict = asdict(res)
        bad_dict["rationale"] = "short"
        bad_res = ConflictResolution.from_dict(bad_dict)
        errors = validate_resolutions(bundle, [bad_res])
        self.assertTrue(any("at least 10 characters" in e
                           for e in errors))


# --- Integration with merge_bundles ---

class ConflictResolutionIntegrationTests(unittest.TestCase):
    def test_end_to_end_merge_then_resolve(self):
        # Two bundles with the same id, different content.
        bundle_a = [_pref("pref_aaaaaaaa")]
        bundle_b = [{**_pref("pref_aaaaaaaa"),
                     "preference_text": "Use a different storage layer"}]
        merge_result = merge_bundles(bundle_a, bundle_b)
        self.assertEqual(len(merge_result.conflicts), 1)
        conflict_id, variants = merge_result.conflicts[0]
        # Resolve the conflict: pick the first variant.
        res = resolve_conflict(
            (conflict_id, variants), 0,
            resolved_by="alice",
            rationale="keeping the first variant after merge",
        )
        # Apply the resolution to bundle_b (which has the
        # second variant). Per the user's "keep the variants"
        # rule, apply_resolutions NEVER replaces: the chosen
        # is added and the rejected is flagged.
        new_bundle = apply_resolutions(bundle_b, [res])
        # The bundle now has 2 records: the chosen (added with
        # resolved_conflict_ids) and the original rejected
        # variant (flagged).
        self.assertEqual(len(new_bundle), 2)
        # The chosen carries the audit chain.
        chosen = [r for r in new_bundle
                  if r.get("resolved_conflict_ids") == [res.id]]
        self.assertEqual(len(chosen), 1)
        self.assertEqual(chosen[0]["preference_text"],
                         "Use content-derived ids")
        # The rejected carries the supersession link.
        rejected = [r for r in new_bundle
                    if r.get("superseded_by_conflict_resolution") == res.id]
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0]["preference_text"],
                         "Use a different storage layer")

    def test_apply_after_diff(self):
        # Diff two bundles, find a changed record, build a
        # conflict from the diff, resolve.
        a = [_pref("pref_aaaaaaaa")]
        b = [{**_pref("pref_aaaaaaaa"),
              "preference_text": "Use a different storage layer"}]
        diff = bundle_diff(a, b)
        self.assertEqual(len(diff.changed), 1)
        old, new = diff.changed[0]
        # Build a conflict from the diff (old is bundle 0,
        # new is bundle 1).
        conflict_id = old["id"]
        conflict = (conflict_id, [(0, old), (1, new)])
        res = resolve_conflict(conflict, 1,
                               resolved_by="bob",
                               rationale="picking the new version")
        self.assertEqual(res.chosen_version_index, 1)
        self.assertEqual(res.chosen_record["preference_text"],
                         "Use a different storage layer")


if __name__ == "__main__":
    unittest.main()
