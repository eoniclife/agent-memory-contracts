"""Tests for the memory hygiene report primitives (v0.7.0)."""

from __future__ import annotations

import unittest

from agent_memory_contracts.hygiene import (
    MemoryHygieneReport,
    compute_hygiene_report,
    hygiene_report_to_markdown,
)


# --- Test fixtures ---

def _pref(id_str: str, *,
          valid_from: str = "2026-06-01T00:00:00Z",
          valid_until: str | None = None,
          stale_after: str | None = None,
          superseded_by: list[str] | None = None,
          evidence_span_ids: list[str] | None = None,
          ledger_type: str = "preference",
          privacy_class: str = "internal",
          **extra) -> dict:
    """A small preference record with controllable temporal fields."""
    return {
        "id": id_str,
        "schema_version": "1.0.0",
        "ledger_type": ledger_type,
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "subject": "memory architecture",
        "preference_text": f"test {id_str}",
        "domain": "architecture",
        "strength": "hard_constraint",
        "valid_from": valid_from,
        "valid_until": valid_until,
        "stale_after": stale_after,
        "superseded_by": (superseded_by if superseded_by is not None
                           else []),
        "evidence_span_ids": (evidence_span_ids
                                if evidence_span_ids is not None
                                else ["span_aaaa"]),
        "privacy_class": privacy_class,
        "metadata": {},
        **extra,
    }


def _span(id_str: str) -> dict:
    """A small evidence span record."""
    return {
        "id": id_str,
        "schema_version": "1.0.0",
        "source_id": "src_aaaa",
        "locator": {"kind": "line_range", "value": "1-2"},
        "span_hash_sha256": "0" * 64,
        "privacy_class": "internal",
        "metadata": {},
    }


# --- compute_hygiene_report: basic ---

class ComputeHygieneBasicTests(unittest.TestCase):
    def test_empty_bundle_returns_zero_report(self):
        report = compute_hygiene_report([])
        self.assertEqual(report.total_records, 0)
        self.assertEqual(report.active_count, 0)
        self.assertEqual(report.stale_count, 0)
        self.assertEqual(report.expired_count, 0)
        self.assertEqual(report.superseded_count, 0)
        # All per-plane / per-type / per-privacy maps are empty.
        self.assertEqual(report.records_by_plane, {})
        self.assertEqual(report.records_by_type, {})
        self.assertEqual(report.records_by_privacy, {})
        # The id is content-derived.
        self.assertTrue(report.id.startswith("hygiene_"))
        self.assertEqual(report.schema_version, "1.0.0")

    def test_basic_counts(self):
        bundle = [
            _pref("pref_aaaa", privacy_class="internal"),
            _pref("pref_bbbb", privacy_class="restricted"),
            _pref("pref_cccc", privacy_class="internal"),
        ]
        report = compute_hygiene_report(bundle)
        self.assertEqual(report.total_records, 3)
        self.assertEqual(report.records_by_plane.get("ledger"), 3)
        self.assertEqual(report.records_by_privacy.get("internal"), 2)
        self.assertEqual(report.records_by_privacy.get("restricted"), 1)
        self.assertEqual(report.records_by_type.get("preference"), 3)

    def test_active_stale_expired_counts(self):
        # Per the spec, "active" and "stale" are independent
        # counters: a record can be both in its valid window
        # AND past its stale_after. So we have 2 active
        # (the "active" one and the "stale" one, which is in
        # its valid window but past stale_after), 1 stale,
        # and 1 expired.
        now = "2026-06-15T12:00:00Z"
        bundle = [
            _pref("pref_active",
                  valid_from="2026-06-01T00:00:00Z",
                  valid_until="2026-12-01T00:00:00Z"),
            _pref("pref_stale",
                  valid_from="2026-01-01T00:00:00Z",
                  valid_until="2026-12-01T00:00:00Z",
                  stale_after="2026-06-10T00:00:00Z"),
            _pref("pref_expired",
                  valid_from="2026-01-01T00:00:00Z",
                  valid_until="2026-05-01T00:00:00Z"),
        ]
        report = compute_hygiene_report(bundle, now=now)
        self.assertEqual(report.active_count, 2)
        self.assertEqual(report.stale_count, 1)
        self.assertEqual(report.expired_count, 1)

    def test_superseded_count(self):
        bundle = [
            _pref("pref_aaaa", superseded_by=["pref_bbbb"]),
            _pref("pref_bbbb"),
            _pref("pref_cccc"),  # not superseded
        ]
        report = compute_hygiene_report(bundle)
        self.assertEqual(report.superseded_count, 1)

    def test_default_window_is_bundle_time_range(self):
        # No window_start / window_end provided; the report
        # defaults to the bundle's earliest / latest ISO timestamp.
        bundle = [
            _pref("pref_aaaa", valid_from="2026-04-01T00:00:00Z"),
            _pref("pref_bbbb", valid_from="2026-07-01T00:00:00Z"),
        ]
        report = compute_hygiene_report(bundle)
        # The window should bracket the two valid_froms.
        self.assertEqual(report.window_start, "2026-04-01T00:00:00Z")
        self.assertEqual(report.window_end, "2026-07-01T00:00:00Z")

    def test_window_start_after_window_end_raises(self):
        with self.assertRaises(ValueError) as cm:
            compute_hygiene_report(
                [],
                window_start="2026-07-01T00:00:00Z",
                window_end="2026-06-01T00:00:00Z",
            )
        self.assertIn("window_start", str(cm.exception))
        self.assertIn("is after", str(cm.exception))
        self.assertIn("window_end", str(cm.exception))

    def test_malformed_window_raises(self):
        with self.assertRaises(ValueError) as cm:
            compute_hygiene_report(
                [], window_start="not-iso",
            )
        self.assertIn("window_start must be ISO 8601", str(cm.exception))
        self.assertIn("'not-iso'", str(cm.exception))

    def test_malformed_now_raises(self):
        with self.assertRaises(ValueError) as cm:
            compute_hygiene_report([], now="not-iso")
        self.assertIn("now must be ISO 8601", str(cm.exception))
        self.assertIn("'not-iso'", str(cm.exception))

    def test_non_list_bundle_raises(self):
        with self.assertRaises(TypeError):
            compute_hygiene_report("not a list")  # type: ignore[arg-type]

    def test_non_dict_record_raises(self):
        with self.assertRaises(TypeError) as cm:
            compute_hygiene_report([1, 2, 3])  # type: ignore[list-item]
        self.assertIn("each record must be a dict", str(cm.exception))
        self.assertIn("index 0", str(cm.exception))

    def test_evidence_integrity(self):
        # A ledger entry with no evidence_span_ids is a hygiene issue.
        bundle = [
            _pref("pref_aaaa", evidence_span_ids=[]),
            _pref("pref_bbbb", evidence_span_ids=["span_missing"]),
            _pref("pref_cccc", evidence_span_ids=["span_present"]),
            _span("span_present"),
        ]
        report = compute_hygiene_report(bundle)
        self.assertEqual(report.records_with_missing_evidence, 1)
        self.assertEqual(report.records_with_orphan_evidence, 1)

    def test_conflicts_argument(self):
        bundle = [_pref("pref_aaaa")]
        report = compute_hygiene_report(
            bundle, conflicts={"surfaced": 7, "resolved": 5})
        self.assertEqual(report.conflicts_surfaced_count, 7)
        self.assertEqual(report.conflicts_resolved_count, 5)

    def test_conflicts_argument_partial(self):
        bundle = [_pref("pref_aaaa")]
        report = compute_hygiene_report(
            bundle, conflicts={"surfaced": 3})
        self.assertEqual(report.conflicts_surfaced_count, 3)
        self.assertEqual(report.conflicts_resolved_count, 0)

    def test_conflicts_argument_invalid_type(self):
        with self.assertRaises(TypeError):
            compute_hygiene_report([], conflicts="not a dict")  # type: ignore[arg-type]


# --- MemoryHygieneReport: id derivation ---

class HygieneReportIdTests(unittest.TestCase):
    def test_id_is_content_derived(self):
        bundle = [_pref("pref_aaaa")]
        r1 = compute_hygiene_report(bundle, now="2026-06-15T12:00:00Z")
        r2 = compute_hygiene_report(bundle, now="2026-06-15T12:00:00Z")
        self.assertEqual(r1.id, r2.id)

    def test_id_changes_with_now(self):
        bundle = [_pref("pref_aaaa")]
        r1 = compute_hygiene_report(bundle, now="2026-06-15T12:00:00Z")
        r2 = compute_hygiene_report(bundle, now="2026-06-15T13:00:00Z")
        self.assertNotEqual(r1.id, r2.id)

    def test_id_changes_with_bundle(self):
        r1 = compute_hygiene_report([_pref("pref_aaaa")])
        r2 = compute_hygiene_report([_pref("pref_bbbb")])
        self.assertNotEqual(r1.id, r2.id)

    def test_id_starts_with_hygiene_prefix(self):
        bundle = [_pref("pref_aaaa")]
        r = compute_hygiene_report(bundle)
        self.assertTrue(r.id.startswith("hygiene_"))
        hex_part = r.id[len("hygiene_"):]
        self.assertEqual(len(hex_part), 24)
        self.assertTrue(all(c in "0123456789abcdef" for c in hex_part))

    def test_bundle_fingerprint_matches_input(self):
        # The bundle_fingerprint field on the report should equal
        # what bundle_fingerprint(bundle) returns for the same
        # bundle.
        bundle = [_pref("pref_aaaa"), _pref("pref_bbbb")]
        r = compute_hygiene_report(bundle)
        from agent_memory_contracts import bundle_fingerprint
        self.assertEqual(r.bundle_fingerprint, bundle_fingerprint(bundle))


# --- MemoryHygieneReport: from_dict / to_dict round-trip ---

class HygieneReportRoundTripTests(unittest.TestCase):
    def test_round_trip(self):
        bundle = [_pref("pref_aaaa"), _pref("pref_bbbb")]
        original = compute_hygiene_report(bundle)
        d = original.to_dict()
        roundtripped = MemoryHygieneReport.from_dict(d)
        self.assertEqual(original.to_dict(), roundtripped.to_dict())

    def test_id_recomputed_on_round_trip(self):
        bundle = [_pref("pref_aaaa")]
        original = compute_hygiene_report(bundle)
        d = original.to_dict()
        d["id"] = "hygiene_DEADBEEF"  # deliberately wrong
        roundtripped = MemoryHygieneReport.from_dict(d)
        # The id is recomputed from the canonical fields, not
        # taken from the input.
        self.assertEqual(roundtripped.id, original.id)
        self.assertNotEqual(roundtripped.id, "hygiene_DEADBEEF")


# --- hygiene_report_to_markdown ---

class HygieneMarkdownTests(unittest.TestCase):
    def test_markdown_contains_headline(self):
        bundle = [_pref("pref_aaaa"), _pref("pref_bbbb")]
        r = compute_hygiene_report(bundle)
        md = hygiene_report_to_markdown(r)
        self.assertIn("# Memory hygiene report", md)
        self.assertIn("2 total records", md)
        self.assertIn("Window:", md)
        self.assertIn("Bundle fingerprint:", md)
        self.assertIn("Computed at:", md)

    def test_markdown_includes_plane_table(self):
        bundle = [_pref("pref_aaaa"), _pref("pref_bbbb"), _span("span_x")]
        r = compute_hygiene_report(bundle)
        md = hygiene_report_to_markdown(r)
        self.assertIn("## By plane", md)
        self.assertIn("| ledger |", md)
        self.assertIn("| evidence |", md)

    def test_markdown_includes_temporal_table(self):
        bundle = [_pref("pref_aaaa"), _pref("pref_stale",
                  stale_after="2026-06-10T00:00:00Z")]
        r = compute_hygiene_report(bundle, now="2026-06-15T00:00:00Z")
        md = hygiene_report_to_markdown(r)
        self.assertIn("## Temporal", md)
        self.assertIn("| active |", md)
        self.assertIn("| stale | 1 |", md)

    def test_markdown_omits_conflict_section_when_zero(self):
        # No conflicts passed; the Conflicts section should
        # not appear.
        bundle = [_pref("pref_aaaa")]
        r = compute_hygiene_report(bundle)
        md = hygiene_report_to_markdown(r)
        self.assertNotIn("## Conflicts", md)

    def test_markdown_includes_conflict_section_when_nonzero(self):
        bundle = [_pref("pref_aaaa")]
        r = compute_hygiene_report(
            bundle, conflicts={"surfaced": 5, "resolved": 3})
        md = hygiene_report_to_markdown(r)
        self.assertIn("## Conflicts", md)
        self.assertIn("**Surfaced:** 5", md)
        self.assertIn("**Resolved:** 3", md)
        self.assertIn("**Open:** 2", md)

    def test_markdown_includes_evidence_integrity(self):
        bundle = [
            _pref("pref_aaaa", evidence_span_ids=[]),
        ]
        r = compute_hygiene_report(bundle)
        md = hygiene_report_to_markdown(r)
        self.assertIn("## Evidence integrity", md)
        self.assertIn("**Missing evidence:** 1", md)

    def test_markdown_includes_type_table(self):
        bundle = [_pref("pref_aaaa", ledger_type="preference"),
                  _pref("pref_bbbb", ledger_type="preference"),
                  _pref("pref_cccc", ledger_type="fact")]
        r = compute_hygiene_report(bundle)
        md = hygiene_report_to_markdown(r)
        self.assertIn("## By type", md)
        self.assertIn("| preference | 2 |", md)
        self.assertIn("| fact | 1 |", md)

    def test_markdown_includes_privacy_table(self):
        bundle = [_pref("pref_aaaa", privacy_class="internal"),
                  _pref("pref_bbbb", privacy_class="restricted")]
        r = compute_hygiene_report(bundle)
        md = hygiene_report_to_markdown(r)
        self.assertIn("## By privacy", md)
        self.assertIn("| internal | 1 |", md)
        self.assertIn("| restricted | 1 |", md)

    def test_markdown_includes_report_id_in_footer(self):
        bundle = [_pref("pref_aaaa")]
        r = compute_hygiene_report(bundle)
        md = hygiene_report_to_markdown(r)
        self.assertIn("Report id:", md)
        self.assertIn(r.id, md)

    def test_markdown_is_pure_function(self):
        # Calling hygiene_report_to_markdown twice on the
        # same report produces the same output.
        bundle = [_pref("pref_aaaa"), _pref("pref_bbbb")]
        r = compute_hygiene_report(bundle)
        md1 = hygiene_report_to_markdown(r)
        md2 = hygiene_report_to_markdown(r)
        self.assertEqual(md1, md2)


# --- Edge cases ---

class HygieneEdgeCaseTests(unittest.TestCase):
    def test_records_without_temporal_fields_counted_as_active(self):
        # A record without valid_from / valid_until is "active" by
        # default (the library's convention).
        bundle = [
            {"id": "x_aaaa", "schema_version": "1.0.0",
             "ledger_type": "preference", "status": "active"},
        ]
        report = compute_hygiene_report(bundle)
        self.assertEqual(report.active_count, 1)

    def test_records_with_invalid_timestamps_are_active(self):
        # A record with an unparseable valid_from is treated as
        # active (we don't crash on bad timestamps; we just
        # ignore them for the temporal check).
        bundle = [
            _pref("pref_aaaa", valid_from="not-iso",
                  valid_until="also-not-iso"),
        ]
        report = compute_hygiene_report(bundle)
        # The temporal fields can't be parsed, so the record is
        # active by default.
        self.assertEqual(report.active_count, 1)
        # And the window defaults to "now" (no valid timestamps
        # in the bundle).
        self.assertTrue(report.window_start.endswith("Z"))
        self.assertTrue(report.window_end.endswith("Z"))

    def test_window_z_suffix_accepted(self):
        # The library's canonical form uses Z; ensure we accept it.
        bundle = [_pref("pref_aaaa")]
        r = compute_hygiene_report(
            bundle,
            window_start="2026-06-01T00:00:00Z",
            window_end="2026-06-30T00:00:00Z",
        )
        self.assertEqual(r.window_start, "2026-06-01T00:00:00Z")
        self.assertEqual(r.window_end, "2026-06-30T00:00:00Z")

    def test_idempotent_rerun(self):
        # Re-running compute_hygiene_report with the same input
        # and same `now` produces a report with the same id
        # (assuming bundle_fingerprint is also stable).
        bundle = [_pref("pref_aaaa"), _pref("pref_bbbb")]
        r1 = compute_hygiene_report(bundle, now="2026-06-15T12:00:00Z")
        r2 = compute_hygiene_report(bundle, now="2026-06-15T12:00:00Z")
        self.assertEqual(r1.id, r2.id)
        self.assertEqual(r1.bundle_fingerprint, r2.bundle_fingerprint)

    def test_includes_evidence_plane_in_correct_count(self):
        # Sources and spans are in the evidence plane.
        bundle = [
            {"id": "src_aaaa", "schema_version": "1.0.0",
             "source_type": "chatgpt_conversation",
             "title": "x", "origin_uri": "https://x",
             "raw_ref": {"kind": "external_uri", "value": "https://x"},
             "content_hash_sha256": "a" * 64,
             "captured_at": "2026-05-30T12:00:00Z",
             "observed_at": "2026-05-30T12:00:00Z",
             "author_or_sender": None, "participants": [],
             "privacy_class": "internal",
             "custody_status": "synthetic",
             "parser_version": "v1",
             "metadata": {}},
            _span("span_aaaa"),
            _pref("pref_aaaa", evidence_span_ids=["span_aaaa"]),
        ]
        report = compute_hygiene_report(bundle)
        self.assertEqual(report.records_by_plane.get("evidence"), 2)
        self.assertEqual(report.records_by_plane.get("ledger"), 1)
        # The ledger entry's evidence_span_ids points at span_aaaa,
        # which IS in the bundle, so no orphan.
        self.assertEqual(report.records_with_orphan_evidence, 0)


if __name__ == "__main__":
    unittest.main()
