"""Decay primitives: time-based and event-based freshness scoring.

The library uses **decay** to compute a freshness score
(0.0–1.0) for each record. The score is computed at read
time (``compile_context_pack``) and used to weight records
in the context_pack. Records with score 0.0 are dropped;
records with score 1.0 are kept; records in between are
selected by the existing ``selection_strategy`` weighted
by freshness.

Decay is **additive** to the existing selection strategies
(``recent`` / ``diverse`` / ``frequent``) and is **opt-in**
(the compiler defaults to no decay, preserving the
v1.0.0 behavior).

This module ships in v1.1.0, the first sprint that
actually uses the schema migration framework
(``v1.0.0a2``): a new optional ``freshness_score`` field
is added to ledger entries and taste cards via a
registered ``MigrationStep``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class DecayPolicy:
    """Configuration for the decay scorer.

    Attributes:
        half_life_days: the time (in days) for a record
            with no events to decay to 0.5. Default: 90
            days (3 months).
        supersession_weight: the weight of a
            supersession event in the score. Default:
            0.5 (a supersession cuts the score in half).
        new_evidence_weight: the weight of a
            new-evidence event. Default: 0.1 (a new
            evidence span adds 0.1).
        curve: ``"exponential"`` (default) or
            ``"linear"``. Exponential is smoother; linear
            is more abrupt.
    """

    half_life_days: float = 90.0
    supersession_weight: float = 0.5
    new_evidence_weight: float = 0.1
    curve: str = "exponential"

    def __post_init__(self) -> None:
        if self.half_life_days <= 0:
            raise ValueError("half_life_days must be positive")
        if self.curve not in ("exponential", "linear"):
            raise ValueError(
                f"curve must be 'exponential' or 'linear', got {self.curve!r}"
            )
        if not 0.0 <= self.supersession_weight <= 1.0:
            raise ValueError("supersession_weight must be in [0.0, 1.0]")
        if not 0.0 <= self.new_evidence_weight <= 1.0:
            raise ValueError("new_evidence_weight must be in [0.0, 1.0]")


@dataclass(frozen=True)
class DecayScore:
    """The freshness score for a record.

    Attributes:
        score: overall freshness in ``[0.0, 1.0]``.
            1.0 = fresh, 0.0 = stale.
        time_component: the time-decay component.
        event_component: the event-decay component.
        half_life_days: the policy's half-life at
            scoring time.
    """

    score: float
    time_component: float
    event_component: float
    half_life_days: float


def _parse_iso8601(s: str) -> datetime | None:
    """Parse an ISO-8601 string to a UTC datetime. Returns
    None on failure.
    """
    if not s or not isinstance(s, str):
        return None
    # Accept "Z" or "+00:00"
    text = s.replace("Z", "+00:00") if s.endswith("Z") else s
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _record_event_count(record: dict[str, Any]) -> int:
    """Count the events in a record (supersedes +
    new-evidence-proxy).

    The v1.1.0 record shape has ``supersedes`` and
    ``superseded_by`` lists. We use ``supersedes`` as a
    proxy for "this record replaced others" and the
    presence of evidence as "this record was used."
    """
    supersedes = record.get("supersedes", []) or []
    evidence_span_ids = record.get("evidence_span_ids", []) or []
    return len(supersedes) + len(evidence_span_ids)


def apply_decay(
    record: dict[str, Any],
    policy: DecayPolicy,
    as_of: str,
) -> DecayScore:
    """Compute the freshness score for a record at a time.

    The record is a dict (the bundle's record shape).
    ``as_of`` is an ISO-8601 timestamp string for the
    "now" of the scoring. The score is in ``[0.0, 1.0]``;
    a value below 0.0 is clamped to 0.0; above 1.0 is
    clamped to 1.0.

    Records without an ``asserted_at`` field (or with a
    malformed timestamp) are treated as fully fresh
    (score 1.0). This is a defensive default for
    malformed records.
    """
    asserted_at = record.get("asserted_at")
    if not asserted_at:
        return DecayScore(
            score=1.0,
            time_component=1.0,
            event_component=0.0,
            half_life_days=policy.half_life_days,
        )

    record_dt = _parse_iso8601(asserted_at)
    now_dt = _parse_iso8601(as_of)
    if record_dt is None or now_dt is None:
        return DecayScore(
            score=1.0,
            time_component=1.0,
            event_component=0.0,
            half_life_days=policy.half_life_days,
        )

    delta_seconds = (now_dt - record_dt).total_seconds()
    if delta_seconds < 0:
        # Record is from the future; treat as fresh.
        return DecayScore(
            score=1.0,
            time_component=1.0,
            event_component=0.0,
            half_life_days=policy.half_life_days,
        )

    # Time component
    half_life_seconds = policy.half_life_days * 86400.0
    if policy.curve == "exponential":
        time_component = math.pow(0.5, delta_seconds / half_life_seconds)
    else:  # linear
        # Linear: 1.0 at t=0, 0.0 at t=2*half_life, clamped.
        time_component = max(0.0, 1.0 - delta_seconds / (2.0 * half_life_seconds))

    # Event component: each event applies a multiplicative
    # weight. Supersession events reduce freshness;
    # new-evidence events also reduce freshness (more
    # evidence means more recent churn).
    event_count = _record_event_count(record)
    event_component = (
        math.pow(1.0 - policy.supersession_weight, _count_supersedes(record))
        * math.pow(1.0 - policy.new_evidence_weight, _count_new_evidence(record))
    )

    # Combine: take the minimum (more conservative).
    raw = min(time_component, event_component)
    score = max(0.0, min(1.0, raw))

    return DecayScore(
        score=score,
        time_component=time_component,
        event_component=event_component,
        half_life_days=policy.half_life_days,
    )


def _count_supersedes(record: dict[str, Any]) -> int:
    return len(record.get("supersedes", []) or [])


def _count_new_evidence(record: dict[str, Any]) -> int:
    return len(record.get("evidence_span_ids", []) or [])


def default_decay_policy() -> DecayPolicy:
    """Return the default decay policy (3-month half-life)."""
    return DecayPolicy()


# ---------------------------------------------------------------------------
# Migration step: v1.0.0 -> v1.1.0
# ---------------------------------------------------------------------------


def _migrate_v1_0_0_to_v1_1_0(record: dict[str, Any]) -> dict[str, Any]:
    """Add the optional ``freshness_score`` field; bump
    ``schema_version``.

    This is a no-op for the field itself: existing
    records do not get a computed score. The field is
    added with a value of ``None``; the compiler
    handles the ``None`` case gracefully by falling
    back to the v1.0.0 binary ``exclude_stale`` logic.
    """
    new = dict(record)
    new["schema_version"] = "1.1.0"
    # The field is optional; default is None.
    if "freshness_score" not in new:
        new["freshness_score"] = None
    return new


def v1_0_0_to_v1_1_0_step() -> Any:
    """The migration step ``v1.0.0`` -> ``v1.1.0``.

    Returns a :class:`MigrationStep` from the migrations
    module. The step is a no-op field add.
    """
    # Lazy import to avoid a circular import.
    from agent_memory_contracts.migrations import MigrationStep

    return MigrationStep(
        from_version="1.0.0",
        to_version="1.1.0",
        description="Add optional freshness_score field; bump schema_version",
        migrate_record=_migrate_v1_0_0_to_v1_1_0,
    )


__all__ = [
    "DecayPolicy",
    "DecayScore",
    "apply_decay",
    "default_decay_policy",
    "v1_0_0_to_v1_1_0_step",
]
