"""Worked example: conflict resolution + memory hygiene report.

Five scenarios demonstrate the conflict resolution and memory
hygiene primitives end-to-end. Run from the repo root with::

    PYTHONPATH=src python3 examples/conflict_resolution.py

Stdlib only on top of the contracts library (which is itself
stdlib-only). No network, no LLM call, no randomness.

.. versionadded:: 0.7.0
"""

from __future__ import annotations

import sys
from typing import Any

from agent_memory_contracts import (
    PreferenceLedgerEntry,
    make_ledger_entry_id,
    make_reducer_decision_id,
    make_source_id,
    make_span_id,
    merge_bundles,
    validate_ledger_bundle,
)
from agent_memory_contracts.conflict import (
    apply_resolutions,
    resolve_conflict,
    validate_resolutions,
)
from agent_memory_contracts.hygiene import (
    compute_hygiene_report,
    hygiene_report_to_markdown,
)


# ---------------------------------------------------------------------------
# Shared fixture helpers
# ---------------------------------------------------------------------------

T_OBSERVED = "2026-06-01T09:00:00Z"
T_DECIDED = "2026-06-01T10:00:00Z"


def _build_pref_evidence_pair(
    suffix: str,
    *,
    preference_text: str,
    domain: str = "architecture",
    subject: str = "memory architecture",
) -> tuple[dict, dict, str, str]:
    """Build a source / span / ledger-id triple for a single
    preference. Returns (source, span, ledger_id, span_id) as
    dicts / strings. Used as the building block for the scenarios
    below.
    """
    source_uri = f"sqlite://scenarios/{suffix}"
    source_id = make_source_id("synthetic_preference", source_uri,
                              f"{'a' * 60}{suffix[:4]}")
    span_id = make_span_id(source_id, "line_range", "payload-body")
    ledger_id = make_ledger_entry_id(
        "preference",
        [span_id],
        {
            "ledger_type": "preference",
            "subject": subject,
            "preference_text": preference_text,
            "domain": domain,
            "scope": "global",
            "valid_from": T_DECIDED,
            "evidence_span_ids": [span_id],
        },
    )
    source = {
        "id": source_id,
        "schema_version": "1.0.0",
        "source_type": "synthetic_preference",
        "title": f"Scenario {suffix} source",
        "origin_uri": source_uri,
        "raw_ref": {"kind": "external_uri", "value": source_uri},
        "content_hash_sha256": f"{'a' * 60}{suffix[:4]}",
        "captured_at": T_OBSERVED,
        "observed_at": T_OBSERVED,
        "author_or_sender": "scenarios",
        "participants": ["scenarios"],
        "privacy_class": "internal",
        "custody_status": "synthetic",
        "parser_version": "v1",
        "metadata": {"scenario": suffix},
    }
    span = {
        "id": span_id,
        "schema_version": "1.0.0",
        "source_id": source_id,
        "episode_id": None,
        "locator": {"kind": "line_range", "value": "payload-body"},
        "text_excerpt": None,
        "excerpt_policy": "none",
        "span_hash_sha256": "0" * 64,
        "privacy_class": "internal",
        "metadata": {"scenario": suffix},
    }
    return source, span, ledger_id, span_id


def _ledger_from_text(suffix: str, preference_text: str) -> dict:
    """Build a minimal PreferenceLedgerEntry dict for a single
    preference, including the synthetic reducer decision and
    the evidence plane."""
    source, span, ledger_id, span_id = _build_pref_evidence_pair(
        suffix, preference_text=preference_text)
    reducer_id = make_reducer_decision_id(
        decision_type="promote",
        target_candidate_ids=[],
        target_ledger_entry_ids=[ledger_id],
        evidence_span_ids=[span_id],
        rationale=(
            f"Synthetic reducer decision for scenario {suffix}; "
            "a real reducer would run provenance, temporal, "
            "and contradiction checks."
        ),
    )
    ledger = {
        "id": ledger_id,
        "schema_version": "1.0.0",
        "ledger_type": "preference",
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "source_record_ids": [source["id"]],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "candidate_ids": [],
        "reducer_decision_id": reducer_id,
        "subject": "memory architecture",
        "preference_text": preference_text,
        "domain": "architecture",
        "strength": "hard_constraint",
        "observed_at": T_OBSERVED,
        "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED,
        "valid_until": None,
        "stale_after": None,
        "created_at": T_DECIDED,
        "updated_at": T_DECIDED,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {"scenario": suffix},
    }
    return ledger


# ---------------------------------------------------------------------------
# Scenario A: pick-one resolution
# ---------------------------------------------------------------------------

def scenario_a_pick_one() -> None:
    """Two engineering teams both wrote a 'use SQLite for state'
    preference in the same week. The team lead picks the design-doc
    version with explicit rationale.

    Note on realism: the library's id derivation is content-
    based, so two teams' preferences with different text get
    different ids (no conflict). For this example we simulate
    the realistic case where the reducer produced two records
    sharing an id (the same recommendation, with different
    justification text). This is the same pattern as Scenario C.
    """
    print("=" * 70)
    print("Scenario A: pick-one resolution")
    print("=" * 70)
    print()

    # Build two ledger records that SHARE an id (simulating a
    # reducer that produced two slightly different versions of
    # the same preference).
    shared_id = "pref_sqlitestate00123456789"
    rec_a = _ledger_from_text("A",
        "Use SQLite for state -- it has ACID transactions.")
    rec_b = _ledger_from_text("B",
        "Use SQLite for state -- the design doc has benchmark data.")
    rec_a["id"] = shared_id
    rec_b["id"] = shared_id

    # Wrap each in a single-record bundle for the merge.
    bundle_team_a = [rec_a]
    bundle_team_b = [rec_b]

    # Merge: a conflict surfaces.
    merge_result = merge_bundles(bundle_team_a, bundle_team_b)
    assert len(merge_result.conflicts) == 1, (
        "expected exactly one conflict, got "
        f"{len(merge_result.conflicts)}"
    )
    conflict_id, variants = merge_result.conflicts[0]
    print(f"  conflict surfaced: id={conflict_id}, "
          f"variants={len(variants)}")
    for i, rec in enumerate(variants):
        print(f"    variant {i}: preference_text={rec[1]['preference_text']!r}")
    print()

    # Team lead picks variant 1 (the design-doc version) with a
    # clear rationale.
    res = resolve_conflict(
        (conflict_id, variants), 1,
        resolved_by="team_lead@engineering",
        rationale=(
            "Design doc has explicit benchmark data; the other "
            "version is a passing comment. Picking the design-doc "
            "version."
        ),
    )
    print(f"  resolution built: id={res.id}")
    print(f"    chosen: {res.chosen_record['preference_text']!r}")
    print(f"    rationale: {res.rationale!r}")
    print()

    # Apply the resolution to bundle_team_b (which had the
    # second variant). The chosen is added; the rejected is
    # flagged. Both stay in the bundle.
    new_bundle = apply_resolutions(bundle_team_b, [res])
    print(f"  bundle_team_b after apply: {len(new_bundle)} records "
          f"(was 1)")
    for rec in new_bundle:
        flags = []
        if "resolved_conflict_ids" in rec:
            flags.append("chosen")
        if "superseded_by_conflict_resolution" in rec:
            flags.append("rejected (flagged)")
        print(f"    id={rec['id'][:24]}..  role={','.join(flags) or 'untouched'}")
    print()
    print("  Scenario A complete.")
    print()


# ---------------------------------------------------------------------------
# Scenario B: merge resolution
# ---------------------------------------------------------------------------

def scenario_b_merge() -> None:
    """Three teams wrote a 'preferred deployment cadence' preference
    with different content (daily, weekly, on-demand). The team
    lead picks 'merge' to combine the variants.

    Note: same realism caveat as Scenario A. The library's
    content-derived ids make these three records have different
    ids by default; we force them to share an id to simulate
    the conflict scenario.
    """
    print("=" * 70)
    print("Scenario B: merge resolution")
    print("=" * 70)
    print()

    shared_id = "pref_deploycadence00123456789"
    rec_daily = _ledger_from_text("Daily", "Deploy daily.")
    rec_weekly = _ledger_from_text("Weekly", "Deploy weekly.")
    rec_on_demand = _ledger_from_text("OnDemand",
                                       "Deploy on-demand.")
    for rec in (rec_daily, rec_weekly, rec_on_demand):
        rec["id"] = shared_id

    bundle_daily = [rec_daily]
    bundle_weekly = [rec_weekly]
    bundle_on_demand = [rec_on_demand]

    merge_result = merge_bundles(
        bundle_daily, bundle_weekly, bundle_on_demand)
    assert len(merge_result.conflicts) == 1
    conflict_id, variants = merge_result.conflicts[0]
    print(f"  conflict surfaced: id={conflict_id}, variants=3")
    for i, rec in enumerate(variants):
        print(f"    variant {i}: preference_text={rec[1]['preference_text']!r}")
    print()

    res = resolve_conflict(
        (conflict_id, variants), "merge",
        resolved_by="team_lead@engineering",
        rationale=(
            "No single winner; the deployment cadence is a team "
            "decision. Merging the three perspectives into a "
            "synthetic record with last-write-wins per field."
        ),
    )
    print(f"  resolution built: id={res.id}")
    print(f"    chosen_version_index: {res.chosen_version_index} "
          "(expected -1 for merge)")
    print(f"    chosen_record id: {res.chosen_record['id']}")
    print(f"    chosen_record preference_text: "
          f"{res.chosen_record['preference_text']!r} (last variant)")
    print(f"    rejected_record_ids: {res.rejected_record_ids}")
    print()

    # The synthetic merged record can replace the variants in
    # any of the input bundles. Apply to bundle_weekly.
    new_bundle = apply_resolutions(bundle_weekly, [res])
    print(f"  bundle_weekly after apply: {len(new_bundle)} records")
    for rec in new_bundle:
        flags = []
        if "resolved_conflict_ids" in rec:
            flags.append("chosen (merged)")
        if "superseded_by_conflict_resolution" in rec:
            flags.append("rejected (flagged)")
        print(f"    id={rec['id'][:24]}..  role={','.join(flags) or 'untouched'}")
    print()
    print("  Scenario B complete.")
    print()


# ---------------------------------------------------------------------------
# Scenario C: split resolution
# ---------------------------------------------------------------------------

def scenario_c_split() -> None:
    """A reducer mistake produced two records with the same id
    that refer to different things. The team lead picks 'split'
    with a rationale explaining the issue.
    """
    print("=" * 70)
    print("Scenario C: split resolution")
    print("=" * 70)
    print()

    # Two records that share an id but encode different things.
    # We build them as dicts and then stuff them into a fake
    # conflict for the resolution primitive.
    rec_first = _ledger_from_text("C1",
        "Use a relational store for state.")
    rec_second = _ledger_from_text("C1",
        "Use a graph store for state.")
    # Force them to share an id (simulating a reducer mistake).
    shared_id = "pref_sharedidsharedidshar"
    rec_first["id"] = shared_id
    rec_second["id"] = shared_id

    conflict = (shared_id, [(0, rec_first), (1, rec_second)])

    res = resolve_conflict(
        conflict, "split",
        resolved_by="reducer_review",
        rationale=(
            "Same id but the two records encode different facts. "
            "The correct fix is to deprecate the id and create two "
            "new memories with distinct ids; deferring to v0.8.0."
        ),
    )
    print(f"  resolution built: id={res.id}")
    print(f"    chosen_version_index: {res.chosen_version_index} "
          "(expected -2 for split)")
    print(f"    chosen_record: {res.chosen_record} "
          "(expected None for split)")
    print(f"    rejected_record_ids: {res.rejected_record_ids}")
    print()

    # Apply the split to a bundle that contains both records.
    bundle_with_both = [rec_first, rec_second]
    new_bundle = apply_resolutions(bundle_with_both, [res])
    print(f"  bundle after apply: {len(new_bundle)} records "
          f"(was 2; both variants stay flagged)")
    for rec in new_bundle:
        flag = rec.get("superseded_by_conflict_resolution")
        print(f"    id={rec['id'][:24]}..  "
              f"preference_text={rec['preference_text']!r}  "
              f"flag={flag[:24] if flag else None}..")
    print()
    print("  Scenario C complete.")
    print()


# ---------------------------------------------------------------------------
# Scenario D: weekly hygiene report
# ---------------------------------------------------------------------------

def scenario_d_weekly_report() -> None:
    """A bundle of 12 records spanning 6 months. We compute a
    hygiene report and print the Markdown.
    """
    print("=" * 70)
    print("Scenario D: weekly hygiene report")
    print("=" * 70)
    print()

    # Build a small bundle with a mix of states.
    bundle: list[dict] = []
    # 5 active preferences.
    for i in range(5):
        suffix = f"D_active_{i}"
        ledger = _ledger_from_text(suffix,
            f"Active preference {i}.")
        # valid_from is recent enough to be active as of `now`.
        ledger["valid_from"] = "2026-06-01T00:00:00Z"
        ledger["valid_until"] = "2026-12-01T00:00:00Z"
        bundle.append(ledger)
    # 3 stale preferences.
    for i in range(3):
        suffix = f"D_stale_{i}"
        ledger = _ledger_from_text(suffix, f"Stale preference {i}.")
        ledger["valid_from"] = "2026-01-01T00:00:00Z"
        ledger["valid_until"] = "2026-12-01T00:00:00Z"
        ledger["stale_after"] = "2026-06-10T00:00:00Z"
        bundle.append(ledger)
    # 2 superseded preferences.
    for i in range(2):
        suffix = f"D_super_{i}"
        ledger = _ledger_from_text(suffix, f"Superseded {i}.")
        ledger["superseded_by"] = [f"pref_newer_{i:08x}"]
        bundle.append(ledger)
    # 2 expired preferences.
    for i in range(2):
        suffix = f"D_expired_{i}"
        ledger = _ledger_from_text(suffix, f"Expired {i}.")
        ledger["valid_from"] = "2026-01-01T00:00:00Z"
        ledger["valid_until"] = "2026-05-01T00:00:00Z"
        bundle.append(ledger)

    # Compute the report.
    report = compute_hygiene_report(
        bundle,
        now="2026-06-15T12:00:00Z",
        conflicts={"surfaced": 3, "resolved": 2},
    )
    print(f"  total_records: {report.total_records}")
    print(f"  active_count: {report.active_count}")
    print(f"  stale_count: {report.stale_count}")
    print(f"  expired_count: {report.expired_count}")
    print(f"  superseded_count: {report.superseded_count}")
    print(f"  conflicts_surfaced: {report.conflicts_surfaced_count}, "
          f"resolved: {report.conflicts_resolved_count}, "
          f"open: {report.conflicts_surfaced_count - report.conflicts_resolved_count}")
    print()
    print("  --- Markdown report ---")
    print()
    print(hygiene_report_to_markdown(report))
    print("  Scenario D complete.")
    print()


# ---------------------------------------------------------------------------
# Scenario E: windowed + diff-augmented hygiene report
# ---------------------------------------------------------------------------

def scenario_e_windowed_report() -> None:
    """A 500-record bundle for Q2 2026. We compute a hygiene
    report over the Q2 window with diff-augmented conflict counts.
    """
    print("=" * 70)
    print("Scenario E: windowed hygiene report (Q2 2026)")
    print("=" * 70)
    print()

    # Build a synthetic 50-record bundle (500 is too long to
    # print; 50 keeps the example readable).
    bundle: list[dict] = []
    for i in range(50):
        suffix = f"E_pref_{i:04d}"
        ledger = _ledger_from_text(suffix, f"Q2 preference {i}.")
        # Spread the records across Q2.
        month = (i % 3) + 4  # 4, 5, 6 = April, May, June
        ledger["valid_from"] = f"2026-{month:02d}-01T00:00:00Z"
        ledger["valid_until"] = f"2026-{month:02d}-28T23:59:59Z"
        ledger["stale_after"] = f"2026-{month:02d}-15T00:00:00Z"
        bundle.append(ledger)

    # Compute the report over the Q2 window with diff-augmented
    # conflict counts (3 surfaced this cycle, 2 resolved, 1
    # open).
    report = compute_hygiene_report(
        bundle,
        window_start="2026-04-01T00:00:00Z",
        window_end="2026-06-30T23:59:59Z",
        now="2026-06-30T12:00:00Z",
        conflicts={"surfaced": 3, "resolved": 2},
    )
    print(f"  window: {report.window_start} -> {report.window_end}")
    print(f"  total_records: {report.total_records}")
    print(f"  records_by_plane: {dict(report.records_by_plane)}")
    print(f"  active_count: {report.active_count}")
    print(f"  stale_count: {report.stale_count}")
    print(f"  conflicts_surfaced: {report.conflicts_surfaced_count}, "
          f"resolved: {report.conflicts_resolved_count}")
    print()
    print("  --- Markdown report (truncated to first 30 lines) ---")
    print()
    md = hygiene_report_to_markdown(report)
    for line in md.splitlines()[:30]:
        print(f"  {line}")
    print(f"  ... ({len(md.splitlines())} lines total)")
    print()
    print("  Scenario E complete.")
    print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    scenario_a_pick_one()
    scenario_b_merge()
    scenario_c_split()
    scenario_d_weekly_report()
    scenario_e_windowed_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
