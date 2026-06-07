"""Tests for the decay module (v1.1.0)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta

from agent_memory_contracts import (
    DecayPolicy,
    DecayScore,
    apply_decay,
    default_decay_policy,
    default_migrator,
    migrate_bundle,
    v1_0_0_to_v1_1_0_step,
)


class TestDecayPolicy(unittest.TestCase):
    """DecayPolicy construction and validation."""

    def test_defaults(self) -> None:
        p = DecayPolicy()
        self.assertEqual(p.half_life_days, 90.0)
        self.assertEqual(p.curve, "exponential")
        self.assertEqual(p.supersession_weight, 0.5)
        self.assertEqual(p.new_evidence_weight, 0.1)

    def test_negative_half_life_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DecayPolicy(half_life_days=0)
        with self.assertRaises(ValueError):
            DecayPolicy(half_life_days=-1)

    def test_invalid_curve_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DecayPolicy(curve="quadratic")

    def test_weights_out_of_range_rejected(self) -> None:
        with self.assertRaises(ValueError):
            DecayPolicy(supersession_weight=1.5)
        with self.assertRaises(ValueError):
            DecayPolicy(new_evidence_weight=-0.1)

    def test_default_decay_policy_factory(self) -> None:
        self.assertEqual(default_decay_policy(), DecayPolicy())


class TestApplyDecay(unittest.TestCase):
    """apply_decay: the headline function."""

    def test_no_asserted_at_returns_fresh(self) -> None:
        p = DecayPolicy()
        score = apply_decay({}, p, "2026-06-07T00:00:00Z")
        self.assertEqual(score.score, 1.0)

    def test_record_in_future_returns_fresh(self) -> None:
        p = DecayPolicy()
        record = {"asserted_at": "2027-01-01T00:00:00Z"}
        score = apply_decay(record, p, "2026-06-07T00:00:00Z")
        self.assertEqual(score.score, 1.0)

    def test_malformed_timestamp_returns_fresh(self) -> None:
        p = DecayPolicy()
        record = {"asserted_at": "not-a-timestamp"}
        score = apply_decay(record, p, "2026-06-07T00:00:00Z")
        self.assertEqual(score.score, 1.0)

    def test_exponential_decay_at_half_life(self) -> None:
        p = DecayPolicy(half_life_days=30, curve="exponential")
        now = "2026-06-07T00:00:00Z"
        thirty_days_ago = (
            datetime(2026, 6, 7, tzinfo=timezone.utc) - timedelta(days=30)
        ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        record = {"asserted_at": thirty_days_ago}
        score = apply_decay(record, p, now)
        # At half-life, exponential should give 0.5
        self.assertAlmostEqual(score.score, 0.5, delta=0.01)

    def test_linear_decay_at_half_life(self) -> None:
        p = DecayPolicy(half_life_days=30, curve="linear")
        now = "2026-06-07T00:00:00Z"
        thirty_days_ago = (
            datetime(2026, 6, 7, tzinfo=timezone.utc) - timedelta(days=30)
        ).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        record = {"asserted_at": thirty_days_ago}
        score = apply_decay(record, p, now)
        # At half-life, linear: 1.0 - 30/(2*30) = 0.5
        self.assertAlmostEqual(score.score, 0.5, delta=0.01)

    def test_supersession_reduces_score(self) -> None:
        p = DecayPolicy(half_life_days=30, supersession_weight=0.5)
        now = "2026-06-07T00:00:00Z"
        record_no_super = {"asserted_at": now, "supersedes": []}
        record_with_super = {
            "asserted_at": now,
            "supersedes": ["fact_a", "fact_b"],
        }
        s_no = apply_decay(record_no_super, p, now)
        s_with = apply_decay(record_with_super, p, now)
        self.assertLess(s_with.score, s_no.score)


class TestMigrationStep(unittest.TestCase):
    """The v1.0.0 -> v1.1.0 migration step."""

    def test_step_metadata(self) -> None:
        step = v1_0_0_to_v1_1_0_step()
        self.assertEqual(step.from_version, "1.0.0")
        self.assertEqual(step.to_version, "1.1.0")

    def test_step_migrates_record(self) -> None:
        step = v1_0_0_to_v1_1_0_step()
        old = {
            "id": "fact_x",
            "schema_version": "1.0.0",
            "freshness_score": None,  # not present
        }
        new = step.migrate_record(old)
        self.assertEqual(new["schema_version"], "1.1.0")
        self.assertIn("freshness_score", new)
        self.assertIsNone(new["freshness_score"])

    def test_default_migrator_registers_step(self) -> None:
        m = default_migrator()
        steps = m.registered_steps()
        self.assertEqual(len(steps), 1)
        self.assertEqual(steps[0].from_version, "1.0.0")

    def test_migrate_bundle_runs_step(self) -> None:
        bundle = [
            {
                "id": "fact_x",
                "schema_version": "1.0.0",
                "freshness_score": None,
            }
        ]
        result = migrate_bundle(bundle, target_version="1.1.0")
        self.assertEqual(result.records_migrated, 1)
        self.assertEqual(result.bundle[0]["schema_version"], "1.1.0")


if __name__ == "__main__":
    unittest.main()
