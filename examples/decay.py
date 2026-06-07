"""Decay example.

Demonstrates the v1.1.0 decay module: compute freshness
scores for a bundle of facts with various `asserted_at`
timestamps, and use the scores to weight records in a
context_pack.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from agent_memory_contracts import (
    DecayPolicy,
    apply_decay,
    default_decay_policy,
    v1_0_0_to_v1_1_0_step,
    migrate_bundle,
)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _days_ago(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).strftime(
        "%Y-%m-%dT%H:%M:%S+00:00"
    )


def main() -> None:
    now = _now()
    policy = DecayPolicy(half_life_days=30, curve="exponential")

    # A small bundle of facts at different ages.
    facts = [
        {"id": "fact_001", "asserted_at": _days_ago(0)},  # 1.0 (today)
        {"id": "fact_002", "asserted_at": _days_ago(15)},  # ~0.71
        {"id": "fact_003", "asserted_at": _days_ago(30)},  # 0.5
        {"id": "fact_004", "asserted_at": _days_ago(60)},  # 0.25
        {"id": "fact_005", "asserted_at": _days_ago(120)},  # 0.0625
    ]

    print("=" * 70)
    print("Decay scoring (half_life_days=30, exponential)")
    print("=" * 70)
    for fact in facts:
        score = apply_decay(fact, policy, now)
        age_days = (datetime.now(timezone.utc) - _parse(fact["asserted_at"])).days
        print(
            f"  {fact['id']}  age={age_days:3d}d  score={score.score:.4f}  "
            f"time={score.time_component:.4f}"
        )

    # The first concrete migration step.
    print()
    print("=" * 70)
    print("First concrete schema migration (v1.0.0 -> v1.1.0)")
    print("=" * 70)
    step = v1_0_0_to_v1_1_0_step()
    print(f"  from_version: {step.from_version}")
    print(f"  to_version:   {step.to_version}")
    print(f"  description:  {step.description}")

    # Migrate a v1.0.0 bundle to v1.1.0.
    v1_0_bundle = [
        {
            "id": "fact_001",
            "schema_version": "1.0.0",
            "freshness_score": None,
        }
    ]
    result = migrate_bundle(v1_0_bundle, target_version="1.1.0")
    print()
    print(f"  records_migrated:   {result.records_migrated}")
    print(f"  records_unchanged:  {result.records_unchanged}")
    print(f"  steps_applied:      {result.steps_applied}")
    print(f"  migrated schema_version: {result.bundle[0]['schema_version']}")

    # Default policy.
    print()
    print("=" * 70)
    print("Default decay policy")
    print("=" * 70)
    print(f"  default_decay_policy(): {default_decay_policy()}")


def _parse(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


if __name__ == "__main__":
    main()
