"""Memory hygiene report: snapshot of a memory system's health.

A *memory hygiene report* is a structured summary of a memory
bundle's state: how many records are active vs stale vs expired
vs superseded, how the records are distributed across the six
planes and types, whether any records have missing or orphan
evidence, and (optionally) how many conflicts were surfaced and
resolved.

This is the library primitive a "memory hygiene" product
workflow calls into. Typical use cases:

- **Weekly hygiene report.** A cron job runs
  :func:`compute_hygiene_report` on the current bundle every
  Monday morning. The Markdown-formatted output goes into the
  team's ``docs/hygiene/weekly/`` directory. Engineers skim it
  over coffee; memory stewards dig into the conflict counts.
- **Quarterly health audit.** A long-window report (Q1, Q2, etc.)
  produced at quarter close, used to show "we went from 4,500
  records to 3,200 in Q2 because 1,800 expired, 600 were
  superseded, 100 were rejected by the reducer" in a board
  meeting.
- **CLI command.** ``python -m agent_memory_contracts hygiene
  weekly.jsonl`` runs the same primitive from the shell,
  emitting a Markdown report (default) or a JSON envelope
  (``--json``) for programmatic consumption.

Contrast with :func:`bundle_fingerprint` (which is a single
hash) and :func:`bundle_diff` (which is "what changed between
two bundles?"). The hygiene report is "what does the current
bundle LOOK LIKE?" -- a structural snapshot, not a content
hash or a delta.

Like the rest of the bundle primitives, this module is
standard-library only.

.. versionadded:: 0.7.0
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .bundles import bundle_fingerprint
from .evidence_ids import _canonical_json, _prefixed_id


# Canonical timestamp form: ISO 8601 with trailing Z. Used for
# default window bounds and `now`.
_ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now_iso8601() -> str:
    """Return the current UTC time as an ISO 8601 string with
    the ``Z`` suffix."""
    return datetime.now(timezone.utc).strftime(_ISO_FMT)


def _validate_iso8601(value: str | None, *, field_name: str) -> None:
    """Raise ``ValueError`` if ``value`` is not a valid ISO 8601
    string. ``None`` is allowed (means "default")."""
    if value is None:
        return
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{field_name} must be ISO 8601; got {value!r}"
        )
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be ISO 8601; got {value!r} ({exc})"
        ) from exc


def _parse_iso(value: str) -> datetime:
    """Parse an ISO 8601 string into a datetime. Accepts both
    the ``Z`` suffix and ``+00:00`` offset. Internal use only."""
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    return datetime.fromisoformat(normalized)


# Plane names that we recognize as "record planes" (every record
# has one). Records that don't match a known plane go into
# "other" in the report.
_RECOGNIZED_PLANES = {
    "evidence", "candidate", "ledger", "taste", "state",
    "contextpack", "reducer_decision",
}


def _infer_plane(record: dict[str, Any]) -> str:
    """Infer the plane of a record from its fields. Records in
    the library's six planes have a ``schema_name`` or a
    content-derived id prefix that identifies the plane.

    The plane inference is best-effort: a record that doesn't
    match a known pattern goes into "other" and is counted but
    not labeled.
    """
    # Records with a `schema_name` field are the cleanest signal.
    schema_name = record.get("schema_name")
    if isinstance(schema_name, str):
        if schema_name in _RECOGNIZED_PLANES:
            return schema_name
        if "ledger" in schema_name:
            return "ledger"
        if "candidate" in schema_name:
            return "candidate"
        if "taste" in schema_name:
            return "taste"
        if "state" in schema_name:
            return "state"
        if "context_pack" in schema_name or "contextpack" in schema_name:
            return "contextpack"
        if "reducer" in schema_name:
            return "reducer_decision"
        if "source_record" in schema_name or "evidence" in schema_name:
            return "evidence"
    # Records with a content-derived id prefix.
    rid = str(record.get("id", ""))
    if rid.startswith("src_") or rid.startswith("ep_") or rid.startswith("span_"):
        return "evidence"
    if rid.startswith("cand_"):
        return "candidate"
    if rid.startswith("fact_") or rid.startswith("pref_") or rid.startswith("dec_"):
        return "ledger"
    if rid.startswith("tcard_") or rid.startswith("tastered_"):
        return "taste"
    if rid.startswith("projst_") or rid.startswith("corest_") or rid.startswith("statered_"):
        return "state"
    if rid.startswith("cpack_") or rid.startswith("cpbld_") or rid.startswith("cpval_"):
        return "contextpack"
    if rid.startswith("redmem_") or rid.startswith("tastered_"):
        return "reducer_decision"
    if rid.startswith("confres_") or rid.startswith("hygiene_"):
        return "audit"
    return "other"


def _infer_type(record: dict[str, Any]) -> str | None:
    """Infer the type of a record (e.g., ``"preference"``,
    ``"fact"``, ``"decision"`` for ledger entries; ``None`` for
    planes that don't have a type)."""
    t = record.get("ledger_type") or record.get("candidate_type")
    if isinstance(t, str):
        return t
    return None


def _infer_privacy(record: dict[str, Any]) -> str:
    """Return the privacy_class field, or ``"unspecified"``."""
    p = record.get("privacy_class")
    if isinstance(p, str):
        return p
    return "unspecified"


def _record_is_temporally_active(
    record: dict[str, Any],
    now: datetime,
) -> bool:
    """Return True if the record is in its valid window as of
    ``now``. Records without ``valid_from``/``valid_until`` are
    considered active (the library's default)."""
    valid_from = record.get("valid_from")
    valid_until = record.get("valid_until")
    if valid_from is not None:
        try:
            vf = _parse_iso(str(valid_from))
        except (TypeError, ValueError):
            vf = None
        if vf is not None and now < vf:
            return False
    if valid_until is not None:
        try:
            vu = _parse_iso(str(valid_until))
        except (TypeError, ValueError):
            vu = None
        if vu is not None and now > vu:
            return False
    return True


def _record_is_stale(
    record: dict[str, Any],
    now: datetime,
) -> bool:
    """Return True if the record is past its ``stale_after`` as
    of ``now``. Records without ``stale_after`` are not stale."""
    stale_after = record.get("stale_after")
    if stale_after is None:
        return False
    try:
        sa = _parse_iso(str(stale_after))
    except (TypeError, ValueError):
        return False
    return now > sa


def _record_is_superseded(record: dict[str, Any]) -> bool:
    """Return True if the record has a non-empty ``superseded_by``
    field (list or scalar)."""
    sb = record.get("superseded_by")
    if sb is None:
        return False
    if isinstance(sb, list):
        return len(sb) > 0
    return True  # scalar truthy


def _record_has_missing_evidence(record: dict[str, Any]) -> bool:
    """A ledger / taste / state entry should reference at least
    one evidence span. If it doesn't, this is a hygiene issue."""
    if record.get("evidence_span_ids") in (None, [], ()):
        return True
    return False


def _record_has_orphan_evidence(
    record: dict[str, Any],
    bundle_record_ids: set[str],
) -> bool:
    """A record's evidence_span_ids should point at records that
    exist in the bundle. If any referenced span is missing, the
    evidence is orphan."""
    span_ids = record.get("evidence_span_ids")
    if not isinstance(span_ids, list) or not span_ids:
        return False
    return any(str(sid) not in bundle_record_ids for sid in span_ids)


@dataclass(frozen=True)
class MemoryHygieneReport:
    """A snapshot of a memory bundle's health over a time window.

    Attributes:
        id: ``hygiene_<sha256 hex>``. Content-derived from all
            identifying fields (bundle_fingerprint, window,
            computed_at, the counts).
        schema_version: ``"1.0.0"``.
        bundle_fingerprint: The SHA-256 of the bundle the report
            describes. Links the report to the exact bundle state.
        window_start: ISO 8601 UTC, the inclusive lower bound.
        window_end: ISO 8601 UTC, the inclusive upper bound.
        computed_at: ISO 8601 UTC, when the report was computed.
        total_records: Total record count in the bundle.
        records_by_plane: ``{plane_name: count}``.
        records_by_type: ``{type_name: count}`` -- the ``type``
            field of each record (e.g., ``"preference"`` for a
            PreferenceLedgerEntry). Records without a type go
            into ``"unspecified"``.
        records_by_privacy: ``{privacy_class: count}``.
        active_count: Records in their valid window as of ``now``.
        stale_count: Records past their ``stale_after`` as of
            ``now``.
        expired_count: Records past their ``valid_until`` as of
            ``now``.
        superseded_count: Records with a non-empty
            ``superseded_by`` field.
        conflicts_surfaced_count: From the optional ``conflicts``
            argument to :func:`compute_hygiene_report`. ``0`` if
            the caller did not supply it.
        conflicts_resolved_count: From the optional ``conflicts``
            argument. ``0`` if the caller did not supply it.
        records_with_missing_evidence: Ledger / taste / state
            entries with no ``evidence_span_ids``.
        records_with_orphan_evidence: Records whose
            ``evidence_span_ids`` reference records not in the
            bundle.
        metadata: Free-form dict for product-specific fields.

    Methods:
        from_dict: Build a ``MemoryHygieneReport`` from a dict,
            computing the content-derived id.
        to_dict: Serialize to a dict (suitable for JSONL output).
    """

    id: str
    schema_version: str
    bundle_fingerprint: str
    window_start: str
    window_end: str
    computed_at: str
    total_records: int
    records_by_plane: dict[str, int]
    records_by_type: dict[str, int]
    records_by_privacy: dict[str, int]
    active_count: int
    stale_count: int
    expired_count: int
    superseded_count: int
    conflicts_surfaced_count: int
    conflicts_resolved_count: int
    records_with_missing_evidence: int
    records_with_orphan_evidence: int
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryHygieneReport:
        """Build a ``MemoryHygieneReport`` from a dict, computing
        the id from the canonical JSON of the identifying fields.
        """
        bundle_fingerprint = str(data["bundle_fingerprint"])
        window_start = str(data["window_start"])
        window_end = str(data["window_end"])
        computed_at = str(data["computed_at"])
        # We recompute the id from the canonical form. The
        # library's convention is that ids are derived, not
        # assigned, so the input id is ignored.
        cid = _compute_hygiene_id(
            bundle_fingerprint=bundle_fingerprint,
            window_start=window_start,
            window_end=window_end,
            computed_at=computed_at,
            total_records=int(data["total_records"]),
            records_by_plane=dict(data.get("records_by_plane") or {}),
            records_by_type=dict(data.get("records_by_type") or {}),
            records_by_privacy=dict(data.get("records_by_privacy") or {}),
            active_count=int(data["active_count"]),
            stale_count=int(data["stale_count"]),
            expired_count=int(data["expired_count"]),
            superseded_count=int(data["superseded_count"]),
            conflicts_surfaced_count=int(data["conflicts_surfaced_count"]),
            conflicts_resolved_count=int(data["conflicts_resolved_count"]),
            records_with_missing_evidence=int(
                data["records_with_missing_evidence"]),
            records_with_orphan_evidence=int(
                data["records_with_orphan_evidence"]),
        )
        return cls(
            id=cid,
            schema_version="1.0.0",
            bundle_fingerprint=bundle_fingerprint,
            window_start=window_start,
            window_end=window_end,
            computed_at=computed_at,
            total_records=int(data["total_records"]),
            records_by_plane=dict(data.get("records_by_plane") or {}),
            records_by_type=dict(data.get("records_by_type") or {}),
            records_by_privacy=dict(data.get("records_by_privacy") or {}),
            active_count=int(data["active_count"]),
            stale_count=int(data["stale_count"]),
            expired_count=int(data["expired_count"]),
            superseded_count=int(data["superseded_count"]),
            conflicts_surfaced_count=int(data["conflicts_surfaced_count"]),
            conflicts_resolved_count=int(data["conflicts_resolved_count"]),
            records_with_missing_evidence=int(
                data["records_with_missing_evidence"]),
            records_with_orphan_evidence=int(
                data["records_with_orphan_evidence"]),
            metadata=dict(data.get("metadata") or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict (suitable for JSONL output)."""
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "bundle_fingerprint": self.bundle_fingerprint,
            "window_start": self.window_start,
            "window_end": self.window_end,
            "computed_at": self.computed_at,
            "total_records": self.total_records,
            "records_by_plane": dict(self.records_by_plane),
            "records_by_type": dict(self.records_by_type),
            "records_by_privacy": dict(self.records_by_privacy),
            "active_count": self.active_count,
            "stale_count": self.stale_count,
            "expired_count": self.expired_count,
            "superseded_count": self.superseded_count,
            "conflicts_surfaced_count": self.conflicts_surfaced_count,
            "conflicts_resolved_count": self.conflicts_resolved_count,
            "records_with_missing_evidence":
                self.records_with_missing_evidence,
            "records_with_orphan_evidence":
                self.records_with_orphan_evidence,
            "metadata": dict(self.metadata),
        }


def _compute_hygiene_id(
    *,
    bundle_fingerprint: str,
    window_start: str,
    window_end: str,
    computed_at: str,
    total_records: int,
    records_by_plane: dict[str, int],
    records_by_type: dict[str, int],
    records_by_privacy: dict[str, int],
    active_count: int,
    stale_count: int,
    expired_count: int,
    superseded_count: int,
    conflicts_surfaced_count: int,
    conflicts_resolved_count: int,
    records_with_missing_evidence: int,
    records_with_orphan_evidence: int,
) -> str:
    """Compute the content-derived id for a MemoryHygieneReport."""
    payload: dict[str, Any] = {
        "bundle_fingerprint": bundle_fingerprint,
        "window_start": window_start,
        "window_end": window_end,
        "computed_at": computed_at,
        "total_records": total_records,
        "records_by_plane": records_by_plane,
        "records_by_type": records_by_type,
        "records_by_privacy": records_by_privacy,
        "active_count": active_count,
        "stale_count": stale_count,
        "expired_count": expired_count,
        "superseded_count": superseded_count,
        "conflicts_surfaced_count": conflicts_surfaced_count,
        "conflicts_resolved_count": conflicts_resolved_count,
        "records_with_missing_evidence": records_with_missing_evidence,
        "records_with_orphan_evidence": records_with_orphan_evidence,
    }
    return _prefixed_id("hygiene", payload, length=24)


def compute_hygiene_report(
    bundle: list[dict[str, Any]],
    *,
    window_start: str | None = None,
    window_end: str | None = None,
    now: str | None = None,
    conflicts: dict[str, int] | None = None,
) -> MemoryHygieneReport:
    """Compute a :class:`MemoryHygieneReport` for a bundle.

    ``window_start`` and ``window_end`` default to the bundle's
    full time range (the earliest and latest ISO timestamps
    found in the records), or to the current UTC time if the
    bundle has no timestamps. The window is inclusive on both
    ends; records with timestamps inside the window contribute to
    the temporal counts (``active``, ``stale``, ``expired``,
    ``superseded``). Records outside the window still contribute
    to the structural counts (``total_records``,
    ``records_by_plane``, etc.) but their temporal state is
    computed against ``now`` regardless of the window.

    ``now`` defaults to the current UTC time and is used to
    determine "active / stale / expired" relative to each
    record's ``valid_from`` / ``valid_until`` / ``stale_after``
    fields.

    ``conflicts`` is an optional dict that augments the report's
    conflict counts. Shape:
    ``{"surfaced": int, "resolved": int}``. Either field may
    be omitted (defaults to 0).

    Args:
        bundle: A list of record dicts.
        window_start: ISO 8601 UTC, inclusive. ``None`` for
            "earliest timestamp in bundle, or now."
        window_end: ISO 8601 UTC, inclusive. ``None`` for
            "latest timestamp in bundle, or now."
        now: ISO 8601 UTC, the "as of" timestamp. ``None`` for
            "now."
        conflicts: Optional ``{"surfaced": int, "resolved":
            int}`` to populate the report's conflict counts.

    Returns:
        A :class:`MemoryHygieneReport`.

    Raises:
        ValueError: if ``window_start`` or ``window_end`` is
            provided but not valid ISO 8601; if
            ``window_start`` is after ``window_end``.
        TypeError: if any record in the bundle is not a dict.
    """
    if not isinstance(bundle, list):
        raise TypeError(
            f"bundle must be a list; got {type(bundle).__name__}"
        )
    for i, rec in enumerate(bundle):
        if not isinstance(rec, dict):
            raise TypeError(
                f"each record must be a dict; got "
                f"{type(rec).__name__} at index {i}"
            )

    _validate_iso8601(window_start, field_name="window_start")
    _validate_iso8601(window_end, field_name="window_end")
    if now is None:
        now = _now_iso8601()
    _validate_iso8601(now, field_name="now")

    # Parse `now` once; we use it for every record's temporal
    # check.
    now_dt = _parse_iso(now)

    # Default window bounds: scan the bundle for the earliest
    # and latest ISO timestamps. We look at the most common
    # temporal fields.
    if window_start is None or window_end is None:
        ts_list: list[datetime] = []
        temporal_fields = (
            "valid_from", "valid_until", "stale_after",
            "created_at", "updated_at", "asserted_at",
        )
        for rec in bundle:
            for field_name in temporal_fields:
                v = rec.get(field_name)
                if isinstance(v, str) and v:
                    try:
                        ts_list.append(_parse_iso(v))
                    except (TypeError, ValueError):
                        continue
        if ts_list:
            min_ts = min(ts_list)
            max_ts = max(ts_list)
            if window_start is None:
                window_start = min_ts.strftime(_ISO_FMT)
            if window_end is None:
                window_end = max_ts.strftime(_ISO_FMT)
        else:
            # No timestamps in the bundle. Default to `now`.
            if window_start is None:
                window_start = now
            if window_end is None:
                window_end = now

    if window_start > window_end:
        raise ValueError(
            f"window_start {window_start!r} is after "
            f"window_end {window_end!r}"
        )

    # Build the per-plane / per-type / per-privacy counts and
    # the temporal counts.
    total_records = len(bundle)
    records_by_plane: dict[str, int] = {}
    records_by_type: dict[str, int] = {}
    records_by_privacy: dict[str, int] = {}
    active_count = 0
    stale_count = 0
    expired_count = 0
    superseded_count = 0
    missing_evidence_count = 0
    orphan_evidence_count = 0

    # Pre-compute the bundle's record-id set for the orphan check.
    bundle_record_ids: set[str] = {
        str(r.get("id", "")) for r in bundle if r.get("id")
    }

    for rec in bundle:
        plane = _infer_plane(rec)
        records_by_plane[plane] = records_by_plane.get(plane, 0) + 1

        rt = _infer_type(rec)
        type_key = rt if rt is not None else "unspecified"
        records_by_type[type_key] = records_by_type.get(type_key, 0) + 1

        privacy = _infer_privacy(rec)
        records_by_privacy[privacy] = (
            records_by_privacy.get(privacy, 0) + 1)

        if _record_is_temporally_active(rec, now_dt):
            active_count += 1
        if _record_is_stale(rec, now_dt):
            stale_count += 1
        # Expired: in the past tense. A record is expired if
        # `now > valid_until`. This is a different state from
        # "stale" (which is about staleness, not expiration).
        valid_until = rec.get("valid_until")
        if valid_until is not None:
            try:
                vu = _parse_iso(str(valid_until))
            except (TypeError, ValueError):
                vu = None
            if vu is not None and now_dt > vu:
                expired_count += 1
        if _record_is_superseded(rec):
            superseded_count += 1
        # Evidence integrity (ledger / taste / state planes).
        if plane in ("ledger", "taste", "state"):
            if _record_has_missing_evidence(rec):
                missing_evidence_count += 1
            if _record_has_orphan_evidence(rec, bundle_record_ids):
                orphan_evidence_count += 1

    # Conflict counts.
    if conflicts is None:
        conflicts_surfaced_count = 0
        conflicts_resolved_count = 0
    else:
        if not isinstance(conflicts, dict):
            raise TypeError(
                f"conflicts must be a dict; got {type(conflicts).__name__}"
            )
        conflicts_surfaced_count = int(conflicts.get("surfaced", 0))
        conflicts_resolved_count = int(conflicts.get("resolved", 0))

    # Compute the bundle fingerprint for the report.
    bundle_fp = bundle_fingerprint(bundle)

    return MemoryHygieneReport.from_dict({
        "bundle_fingerprint": bundle_fp,
        "window_start": window_start,
        "window_end": window_end,
        "computed_at": now,
        "total_records": total_records,
        "records_by_plane": records_by_plane,
        "records_by_type": records_by_type,
        "records_by_privacy": records_by_privacy,
        "active_count": active_count,
        "stale_count": stale_count,
        "expired_count": expired_count,
        "superseded_count": superseded_count,
        "conflicts_surfaced_count": conflicts_surfaced_count,
        "conflicts_resolved_count": conflicts_resolved_count,
        "records_with_missing_evidence": missing_evidence_count,
        "records_with_orphan_evidence": orphan_evidence_count,
        "metadata": {},
    })


def hygiene_report_to_markdown(report: MemoryHygieneReport) -> str:
    """Format a :class:`MemoryHygieneReport` as a Markdown table.

    Pure function. No I/O, no side effects. Returns a string
    the caller can print, write to disk, or embed.

    The output is a single Markdown document with:
    - A one-line summary (total records, window, fingerprint)
    - A "By plane" table
    - A "By type" table (omitted if no typed planes are present)
    - A "By privacy" table
    - A "Temporal" table (active, stale, expired, superseded)
    - A "Conflicts" line (if either conflict count > 0)
    - An "Evidence integrity" line (missing + orphan counts)
    - A footer with ``bundle_fingerprint`` and ``computed_at``
    """
    lines: list[str] = []

    # One-line summary.
    lines.append(f"# Memory hygiene report")
    lines.append("")
    lines.append(
        f"**Window:** {report.window_start} → {report.window_end}  ")
    lines.append(
        f"**Bundle fingerprint:** `{report.bundle_fingerprint}`  ")
    lines.append(f"**Computed at:** {report.computed_at}")
    lines.append("")

    # Headline: total records + active / stale / expired / superseded.
    lines.append(f"**{report.total_records} total records** — "
                 f"{report.active_count} active, "
                 f"{report.stale_count} stale, "
                 f"{report.expired_count} expired, "
                 f"{report.superseded_count} superseded.")
    lines.append("")

    # By plane.
    if report.records_by_plane:
        lines.append("## By plane")
        lines.append("")
        lines.append("| Plane | Count |")
        lines.append("| ---: | ---: |")
        for plane, count in sorted(report.records_by_plane.items(),
                                   key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| {plane} | {count} |")
        lines.append("")

    # By type. Skip if the only key is "unspecified" with the
    # same count as total_records (i.e., no typed records).
    typed = {k: v for k, v in report.records_by_type.items()
             if k != "unspecified"}
    if typed:
        lines.append("## By type")
        lines.append("")
        lines.append("| Type | Count |")
        lines.append("| ---: | ---: |")
        for t, count in sorted(typed.items(),
                               key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| {t} | {count} |")
        lines.append("")

    # By privacy.
    if report.records_by_privacy:
        lines.append("## By privacy")
        lines.append("")
        lines.append("| Privacy | Count |")
        lines.append("| ---: | ---: |")
        for p, count in sorted(report.records_by_privacy.items(),
                               key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| {p} | {count} |")
        lines.append("")

    # Temporal detail. Always shown.
    lines.append("## Temporal")
    lines.append("")
    lines.append("| State | Count |")
    lines.append("| ---: | ---: |")
    lines.append(f"| active | {report.active_count} |")
    lines.append(f"| stale | {report.stale_count} |")
    lines.append(f"| expired | {report.expired_count} |")
    lines.append(f"| superseded | {report.superseded_count} |")
    lines.append("")

    # Conflicts.
    if report.conflicts_surfaced_count > 0 or report.conflicts_resolved_count > 0:
        lines.append("## Conflicts")
        lines.append("")
        lines.append(f"- **Surfaced:** {report.conflicts_surfaced_count}")
        lines.append(f"- **Resolved:** {report.conflicts_resolved_count}")
        if report.conflicts_surfaced_count > report.conflicts_resolved_count:
            open_count = (
                report.conflicts_surfaced_count - report.conflicts_resolved_count)
            lines.append(f"- **Open:** {open_count}")
        lines.append("")

    # Evidence integrity. Always shown when the planes have
    # applicable records. (We always show it; even zero is
    # informative.)
    lines.append("## Evidence integrity")
    lines.append("")
    lines.append(
        f"- **Missing evidence:** "
        f"{report.records_with_missing_evidence}")
    lines.append(
        f"- **Orphan evidence:** "
        f"{report.records_with_orphan_evidence}")
    lines.append("")

    # Footer.
    lines.append("---")
    lines.append("")
    lines.append(f"Report id: `{report.id}`  ")
    lines.append(f"Schema version: `{report.schema_version}`  ")
    lines.append(
        f"Re-run with the same bundle to reproduce; the report "
        f"id is content-derived and will only differ if the "
        f"counts or window change.")

    return "\n".join(lines) + "\n"


__all__ = [
    "MemoryHygieneReport",
    "compute_hygiene_report",
    "hygiene_report_to_markdown",
]
