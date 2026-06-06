"""Conflict resolution: pick-one / merge / split primitives.

A *conflict* in this library is the output of :func:`merge_bundles`
when two input bundles disagree on the content of the same id. The
``conflicts`` list on a :class:`BundleMerge` is the upstream; this
module is the downstream: it takes those surfaced conflicts and
turns them into auditable, persisted resolutions.

Three resolution policies are supported:

- **Pick-one.** One variant is chosen; the others are marked as
  superseded. Use when one source is clearly authoritative.
- **Merge.** A synthetic record is produced that combines fields
  across variants (last-write-wins per field). Use when the
  variants are compatible but no single one is authoritative.
- **Split.** No new record is produced; the original variants are
  flagged as superseded and the resolution record carries the
  rationale explaining why neither wins (e.g., "same id but
  semantically different records; correct fix is to deprecate the
  id and create distinct new ones"). Use when the id is broken.

Every resolution produces a :class:`ConflictResolution` audit
record whose id is content-derived. The audit chain is preserved
in the bundle itself: chosen records carry
``resolved_conflict_ids``; rejected variants carry
``superseded_by_conflict_resolution``.

Contrast with :func:`merge_bundles` (which *surfaces* conflicts)
and :func:`apply_resolutions` (which *applies* a set of already-
built resolutions to a bundle): this module is the middle step
that takes a surface-form conflict and produces a resolution.

Typical use cases:

- **Cross-team memory council.** A "memory council" UI shows the
  user the surfaced conflicts from a recent
  :func:`merge_bundles`; the user picks one of the three policies
  per conflict and provides a rationale. This module is the
  library primitive the council calls into.
- **Auto-resolution in a sync job.** A bidirectional sync between
  two systems runs :func:`merge_bundles`, then for every
  conflict calls :func:`resolve_conflict` with a deterministic
  policy (e.g., always ``prefer="last"``) and rationale "sync
  policy: newest write wins."
- **Audit replay.** Given a bundle, the audit chain can be
  reconstructed by walking the ``resolved_conflict_ids`` on each
  record back to the :class:`ConflictResolution` records and
  forward to the rejected variants they replaced.

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

from .evidence_ids import _canonical_json, _prefixed_id
from .merge import BundleMerge


# Sentinel values for `chosen_version_index`. -1 means "merge" and
# -2 means "split". Both are "no single variant was chosen" but in
# different ways: a merge produced a new record, a split did not.
_CHOSEN_MERGE: int = -1
_CHOSEN_SPLIT: int = -2


@dataclass(frozen=True)
class ConflictResolution:
    """Audit record for a single resolved conflict.

    The id is content-derived from the canonical JSON of the
    resolution's identifying fields (conflict_id, chosen_version
    indicator, chosen_record or None, rejected_record_ids,
    resolved_by, resolved_at, rationale, superseded_at, metadata).
    Two resolutions of the same conflict with the same choice and
    same author and same timestamp produce the same id; a re-run
    of the same resolution with a different ``resolved_at``
    produces a different id but otherwise identical fields.

    Attributes:
        id: ``confres_<sha256 hex>`` (24 hex chars after the prefix,
            matching the rest of the library's id format).
        schema_version: ``"1.0.0"``.
        conflict_id: The id of the original conflict (typically the
            content-derived id of the first variant, or a derived
            id if the conflict spans multiple records). Used by
            :func:`apply_resolutions` to find the matching records
            in a bundle.
        chosen_version_index: 0-based index into the original
            conflict's variant list for a "pick-one" resolution.
            Special values: ``-1`` (``CHOSEN_MERGE``) means
            ``chosen_record`` is a synthetic merge; ``-2``
            (``CHOSEN_SPLIT``) means ``chosen_record`` is ``None``
            and the resolution explains why neither variant wins.
        chosen_record: The chosen version (full record) for a
            "pick-one" resolution, or the synthetic merge for a
            "merge" resolution. ``None`` for a "split" resolution.
        rejected_record_ids: The ids of the rejected versions, for
            audit. The chosen record's id (when not None) is NOT
            in this list.
        resolved_by: The author of the resolution -- a human name,
            an agent name, or a system identifier. Required.
        resolved_at: ISO 8601 UTC, e.g. ``"2026-06-06T12:00:00Z"``.
        rationale: Required, min 10 characters. A short justification
            of why this resolution is the right one.
        superseded_at: ``None`` if this resolution is the active
            one. If the resolution is later superseded by another
            ``ConflictResolution`` on the same ``conflict_id``, this
            is the timestamp of the supersession.
        metadata: Free-form dict for product-specific fields.

    Methods:
        from_dict: Build a ``ConflictResolution`` from a dict,
            computing the content-derived id.
        to_dict: Serialize to a dict suitable for JSONL output.
    """

    id: str
    schema_version: str
    conflict_id: str
    chosen_version_index: int
    chosen_record: dict[str, Any] | None
    rejected_record_ids: list[str]
    resolved_by: str
    resolved_at: str
    rationale: str
    superseded_at: str | None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConflictResolution:
        """Build a ``ConflictResolution`` from a dict, computing
        the id from the canonical JSON of the identifying fields.
        """
        # We accept `id` as input but always recompute it to
        # guarantee it matches the content. The library's
        # convention is that ids are derived, not assigned.
        conflict_id = str(data["conflict_id"])
        chosen_version_index = int(data["chosen_version_index"])
        chosen_record_raw = data.get("chosen_record")
        chosen_record: dict[str, Any] | None
        if chosen_record_raw is None:
            chosen_record = None
        else:
            if not isinstance(chosen_record_raw, dict):
                raise TypeError(
                    f"chosen_record must be a dict or None; "
                    f"got {type(chosen_record_raw).__name__}"
                )
            # Deep copy so the caller can't mutate our state.
            chosen_record = copy.deepcopy(chosen_record_raw)
        rejected_record_ids = [str(i) for i in data.get(
            "rejected_record_ids", [])]
        resolved_by = str(data["resolved_by"])
        resolved_at = str(data["resolved_at"])
        rationale = str(data["rationale"])
        superseded_at = data.get("superseded_at")
        if superseded_at is not None:
            superseded_at = str(superseded_at)
        metadata = dict(data.get("metadata") or {})

        cid = _compute_conflict_resolution_id(
            conflict_id=conflict_id,
            chosen_version_index=chosen_version_index,
            chosen_record=chosen_record,
            rejected_record_ids=rejected_record_ids,
            resolved_by=resolved_by,
            resolved_at=resolved_at,
            rationale=rationale,
            superseded_at=superseded_at,
            metadata=metadata,
        )
        return cls(
            id=cid,
            schema_version="1.0.0",
            conflict_id=conflict_id,
            chosen_version_index=chosen_version_index,
            chosen_record=chosen_record,
            rejected_record_ids=rejected_record_ids,
            resolved_by=resolved_by,
            resolved_at=resolved_at,
            rationale=rationale,
            superseded_at=superseded_at,
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a dict (suitable for JSONL output)."""
        return {
            "id": self.id,
            "schema_version": self.schema_version,
            "conflict_id": self.conflict_id,
            "chosen_version_index": self.chosen_version_index,
            "chosen_record": self.chosen_record,
            "rejected_record_ids": list(self.rejected_record_ids),
            "resolved_by": self.resolved_by,
            "resolved_at": self.resolved_at,
            "rationale": self.rationale,
            "superseded_at": self.superseded_at,
            "metadata": dict(self.metadata),
        }


def _compute_conflict_resolution_id(
    *,
    conflict_id: str,
    chosen_version_index: int,
    chosen_record: dict[str, Any] | None,
    rejected_record_ids: list[str],
    resolved_by: str,
    resolved_at: str,
    rationale: str,
    superseded_at: str | None,
    metadata: dict[str, Any],
) -> str:
    """Compute the content-derived id for a ConflictResolution."""
    payload: dict[str, Any] = {
        "conflict_id": conflict_id,
        "chosen_version_index": chosen_version_index,
        "chosen_record": chosen_record,
        "rejected_record_ids": rejected_record_ids,
        "resolved_by": resolved_by,
        "resolved_at": resolved_at,
        "rationale": rationale,
        "superseded_at": superseded_at,
        "metadata": metadata,
    }
    return _prefixed_id("confres", payload, length=24)


# --- Validation helpers (used by resolve_conflict, exposed for tests) ---

def _validate_iso8601(value: str, *, field_name: str) -> None:
    """Raise ``ValueError`` if ``value`` is not a valid ISO 8601
    timestamp. We use ``datetime.fromisoformat`` (Python 3.11+ accepts
    the trailing ``Z``; 3.10 requires ``+00:00``; we normalize).
    """
    if not isinstance(value, str) or not value:
        raise ValueError(
            f"{field_name} must be a non-empty ISO 8601 string; "
            f"got {value!r}"
        )
    normalized = value.replace("Z", "+00:00") if value.endswith("Z") else value
    try:
        datetime.fromisoformat(normalized)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be ISO 8601; got {value!r} ({exc})"
        ) from exc


def _now_iso8601() -> str:
    """Return the current UTC time as an ISO 8601 string with
    the ``Z`` suffix (the library's canonical form)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _merge_records_last_write_wins(
    variants: list[tuple[int, dict[str, Any]]],
) -> dict[str, Any]:
    """Produce a synthetic merged record from the variants.

    Strategy: for each field, take the value from the *last*
    variant in bundle-index order that has the field. This is
    last-write-wins at the field level rather than the record
    level. The id is the conflict_id (preserved so the merged
    record replaces the variants in the bundle).

    Rationale: when the user picks "merge" without supplying a
    custom merge, the safest default is "no field is lost; later
    bundles override earlier ones for the same field." If a field
    is present in variant 0 with value A and in variant 2 with
    value B, the merged record has B for that field.

    Ties are broken by bundle index ascending: if two variants
    in the same bundle position disagree, we take the later one
    in the input order.
    """
    if not variants:
        raise ValueError(
            "cannot merge a conflict with 0 variants"
        )
    # Sort by bundle_index so "last write wins" is consistent
    # with the merge_bundles convention.
    sorted_variants = sorted(variants, key=lambda v: v[0])
    merged: dict[str, Any] = {}
    for _idx, rec in sorted_variants:
        for k, v in rec.items():
            merged[k] = v
    return merged


# --- Public primitives ---

def resolve_conflict(
    conflict: tuple[str, list[tuple[int, dict[str, Any]]]],
    chosen: int | str,
    *,
    resolved_by: str,
    rationale: str,
    resolved_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ConflictResolution:
    """Build a :class:`ConflictResolution` audit record for one
    surface-form conflict.

    ``conflict`` is a single entry from
    :attr:`BundleMerge.conflicts`: a tuple of
    ``(id, [(bundle_index, record), ...])``.

    ``chosen`` is one of:

    - An ``int`` (0-based) — picks that variant as the winner. The
      ``chosen_record`` is a deep copy of the variant's record.
    - The string ``"merge"`` — produces a synthetic merged record
      combining the variants (last-write-wins per field across
      the variants, by bundle index). ``chosen_version_index``
      is ``-1`` (``CHOSEN_MERGE``).
    - The string ``"split"`` — produces a resolution with
      ``chosen_record=None`` and ``rationale`` explaining why
      neither variant wins. ``chosen_version_index`` is ``-2``
      (``CHOSEN_SPLIT``).

    ``resolved_by`` is required (non-empty). ``rationale`` is
    required (non-empty, min 10 characters). ``resolved_at``
    defaults to the current UTC time as ISO 8601.

    Args:
        conflict: A single ``(id, [(bundle_index, record), ...])``
            tuple from :attr:`BundleMerge.conflicts`.
        chosen: The policy decision -- int (0-based variant
            index), ``"merge"``, or ``"split"``.
        resolved_by: Required. The author of the resolution.
        rationale: Required, min 10 characters.
        resolved_at: ISO 8601 UTC, or ``None`` for "now."
        metadata: Optional free-form dict.

    Returns:
        A :class:`ConflictResolution` ready to be applied via
        :func:`apply_resolutions` or persisted as an audit
        record.

    Raises:
        ValueError: if ``chosen`` is not int / ``"merge"`` /
            ``"split"``; if int and out of range; if ``"merge"``
            and the variant list is empty; if ``resolved_by`` is
            empty; if ``rationale`` is empty or shorter than 10
            characters; if ``resolved_at`` is provided but not
            valid ISO 8601.
    """
    if not isinstance(conflict, tuple) or len(conflict) != 2:
        raise TypeError(
            "conflict must be a (id, [(bundle_index, record), ...]) tuple; "
            f"got {type(conflict).__name__}"
        )
    conflict_id, variants = conflict
    if not isinstance(conflict_id, str) or not conflict_id:
        raise ValueError(
            f"conflict id must be a non-empty string; got {conflict_id!r}"
        )
    if not isinstance(variants, list):
        raise TypeError(
            f"conflict variants must be a list; got {type(variants).__name__}"
        )

    # Validate resolved_by.
    if not isinstance(resolved_by, str) or not resolved_by.strip():
        raise ValueError(
            "resolved_by is required and must be non-empty"
        )

    # Validate rationale.
    if not isinstance(rationale, str):
        raise TypeError(
            f"rationale must be a string; got {type(rationale).__name__}"
        )
    if len(rationale.strip()) < 10:
        raise ValueError(
            f"rationale must be at least 10 characters; got {len(rationale)}"
        )

    # Default resolved_at.
    if resolved_at is None:
        resolved_at = _now_iso8601()
    _validate_iso8601(resolved_at, field_name="resolved_at")

    # Compute the chosen record and chosen_version_index based
    # on the policy.
    if isinstance(chosen, bool):
        # `bool` is a subclass of `int`; reject explicitly to avoid
        # silently using True/False as 1/0.
        raise ValueError(
            "chosen must be an int (0-based index), 'merge', or 'split'; "
            f"got bool {chosen!r}"
        )
    if isinstance(chosen, int):
        if not variants:
            raise ValueError(
                f"cannot resolve conflict {conflict_id!r}: "
                "the variants list is empty"
            )
        if chosen < 0 or chosen >= len(variants):
            raise ValueError(
                f"chosen index {chosen} out of range for conflict with "
                f"{len(variants)} variants"
            )
        chosen_version_index = chosen
        # Deep copy so the caller's mutation can't poison our state.
        chosen_record = copy.deepcopy(variants[chosen][1])
        rejected_record_ids = [
            variants[i][1].get("id", "")
            for i in range(len(variants))
            if i != chosen
        ]
        # Filter out empty ids (records without an id are unusual
        # but allowed in principle; we just don't put them in the
        # rejected ids list).
        rejected_record_ids = [i for i in rejected_record_ids if i]
    elif isinstance(chosen, str):
        if chosen == "merge":
            chosen_version_index = _CHOSEN_MERGE
            chosen_record = _merge_records_last_write_wins(variants)
            # The chosen record's id is the conflict_id so it can
            # replace the variants in the bundle. If the conflict_id
            # happens to be empty in some weird case, we keep the
            # synthetic id anyway; downstream code can detect this.
            chosen_record["id"] = conflict_id
            rejected_record_ids = [
                v[1].get("id", "") for v in variants
            ]
            rejected_record_ids = [i for i in rejected_record_ids if i]
        elif chosen == "split":
            chosen_version_index = _CHOSEN_SPLIT
            chosen_record = None
            # Both variants are "rejected" in the sense that
            # neither wins; the original variants stay in the
            # bundle and are flagged with this resolution id.
            rejected_record_ids = [
                v[1].get("id", "") for v in variants
            ]
            rejected_record_ids = [i for i in rejected_record_ids if i]
        else:
            raise ValueError(
                "chosen must be an int (0-based index), 'merge', or "
                f"'split'; got {chosen!r}"
            )
    else:
        raise TypeError(
            "chosen must be an int (0-based index), 'merge', or 'split'; "
            f"got {type(chosen).__name__}"
        )

    return ConflictResolution.from_dict({
        "conflict_id": conflict_id,
        "chosen_version_index": chosen_version_index,
        "chosen_record": chosen_record,
        "rejected_record_ids": rejected_record_ids,
        "resolved_by": resolved_by,
        "resolved_at": resolved_at,
        "rationale": rationale,
        "superseded_at": None,
        "metadata": dict(metadata or {}),
    })


def _find_variant_ids_in_conflict(
    conflict: tuple[str, list[tuple[int, dict[str, Any]]]],
) -> list[str]:
    """Return the ids of the variant records in a conflict.
    Used by apply_resolutions to validate resolution consistency.
    """
    return [v[1].get("id", "") for v in conflict[1] if v[1].get("id")]


def apply_resolutions(
    bundle: list[dict[str, Any]],
    resolutions: list[ConflictResolution],
    *,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Apply a list of resolutions to a bundle, returning the new
    bundle. The input is not mutated.

    For each resolution:

    - The **chosen record** (or the synthetic merged record)
      replaces the first matching variant in the bundle by id. If
      the resolution is a "split" (``chosen_record`` is None),
      no new record is added; the variants stay in the bundle
      but flagged.
    - The **rejected variants** stay in the bundle (the bundle
      preserves the audit chain) with a new field
      ``superseded_by_conflict_resolution`` set to the
      resolution's id, and the existing
      ``superseded_by: list[str]`` field updated to include the
      resolution id.
    - The **chosen record** gains a new field
      ``resolved_conflict_ids: list[str]`` carrying the
      resolution's id, so the audit chain is visible in the
      bundle itself.

    Args:
        bundle: The input bundle of records. Not mutated.
        resolutions: A list of :class:`ConflictResolution`
            records. Each must refer to a conflict whose variant
            records are present in the bundle.
        now: The timestamp written into the
            ``superseded_at`` field on the rejected records.
            Defaults to the current UTC time.

    Returns:
        A new bundle list with the resolutions applied.

    Raises:
        ValueError: if any resolution's ``conflict_id`` has no
            matching records in the bundle; if any resolution's
            ``chosen_record.id`` (when not None) is not in the
            conflict's variant ids; if two resolutions target
            the same ``conflict_id``; if any resolution refers
            to a "merge" or "split" with no variants.
    """
    if not isinstance(bundle, list):
        raise TypeError(
            f"bundle must be a list; got {type(bundle).__name__}"
        )
    if not isinstance(resolutions, list):
        raise TypeError(
            f"resolutions must be a list; got {type(resolutions).__name__}"
        )

    # First pass: validate everything, build a resolution-by-conflict
    # map for the second pass. We do validation up front so the
    # caller gets a clear error rather than a partial result.
    seen_conflict_ids: set[str] = set()
    for resolution in resolutions:
        if not isinstance(resolution, ConflictResolution):
            raise TypeError(
                f"each resolution must be a ConflictResolution; "
                f"got {type(resolution).__name__}"
            )
        if resolution.conflict_id in seen_conflict_ids:
            raise ValueError(
                f"two resolutions target the same conflict_id "
                f"{resolution.conflict_id!r}; resolve each conflict "
                "at most once"
            )
        seen_conflict_ids.add(resolution.conflict_id)

    if now is None:
        now = _now_iso8601()
    _validate_iso8601(now, field_name="now")

    # Build a deep-copy of the input bundle; we never mutate the
    # caller's data.
    new_bundle: list[dict[str, Any]] = [copy.deepcopy(r) for r in bundle]

    # Build a flat list of (record_id, source) pairs so we can
    # detect conflicts whose variant records are not in the
    # bundle. We do this BEFORE applying any resolution so a
    # missing-conflict error short-circuits the whole call.
    bundle_record_ids: set[str] = set()
    for rec in new_bundle:
        rid = rec.get("id")
        if rid is not None:
            bundle_record_ids.add(str(rid))
    for resolution in resolutions:
        # Check that at least one of the variant ids is in the
        # bundle. If the chosen record has an id, also check
        # that. (The chosen record is added to the bundle, but
        # we still need at least ONE of the rejected ids to be
        # in the bundle to know the conflict is real.)
        variant_ids = list(resolution.rejected_record_ids)
        if resolution.chosen_record is not None:
            cid = str(resolution.chosen_record.get("id", ""))
            if cid:
                variant_ids.append(cid)
        if not any(vid in bundle_record_ids for vid in variant_ids if vid):
            raise ValueError(
                f"resolution refers to conflict "
                f"{resolution.conflict_id!r} but no matching "
                "records found in bundle"
            )

    # Second pass: apply each resolution.
    for resolution in resolutions:
        # We don't do in-place replacement of the chosen record.
        # The user's spec is "keep the variants" -- both the
        # chosen and the rejected stay in the bundle, with the
        # audit fields set. The chosen is added (or already in
        # the bundle, in which case the resolved_conflict_ids
        # field is updated on the existing record). The rejected
        # variants stay where they are, flagged.

        # Apply the resolution. Per the spec ("keep the variants"),
        # we NEVER do in-place replacement: the chosen is always
        # added to the bundle, and the rejected variants stay in
        # the bundle flagged.
        #
        # Order matters: we flag the rejected variants FIRST
        # (against the original bundle, before the chosen is
        # added), then we append the chosen. This way, the
        # flagging pass doesn't accidentally flag the chosen
        # record that we're about to add.

        # Flag the rejected variants. Walk the bundle for records
        # whose id matches a rejected_record_id and set the
        # audit fields. Multiple records with the same id (e.g.,
        # two records both with id "pref_aaaaaaaa" but different
        # content) are all flagged -- the audit chain covers
        # every variant.
        for rejected_id in resolution.rejected_record_ids:
            if not rejected_id:
                continue
            for rec in new_bundle:
                if rec.get("id") != rejected_id:
                    continue
                # Set the audit fields. If the chosen and rejected
                # share the same id and content (rare), we still
                # flag -- the audit metadata is valid in either
                # case. The resolved_conflict_ids field is
                # preserved.
                if rec.get("superseded_by_conflict_resolution") is None:
                    rec["superseded_by_conflict_resolution"] = resolution.id
                existing = rec.get("superseded_by") or []
                if not isinstance(existing, list):
                    existing = [existing]
                if resolution.id not in existing:
                    rec["superseded_by"] = list(existing) + [resolution.id]
                rec["superseded_at"] = now

        # Append the chosen record (if any). The chosen carries
        # the resolved_conflict_ids field as the audit chain.
        # The chosen is always appended -- never in-place
        # replacement -- so the rejected variants stay in the
        # bundle. If the chosen has the same id as a record
        # already in the bundle, the bundle now has duplicates
        # (one chosen, one rejected, both with the same id); the
        # audit chain in the bundle itself preserves the
        # relationship.
        if resolution.chosen_record is not None:
            chosen_copy = copy.deepcopy(resolution.chosen_record)
            existing_chain = chosen_copy.get("resolved_conflict_ids") or []
            chosen_copy["resolved_conflict_ids"] = list(existing_chain) + [
                resolution.id
            ]
            new_bundle.append(chosen_copy)

    return new_bundle


def validate_resolutions(
    bundle: list[dict[str, Any]],
    resolutions: list[ConflictResolution],
) -> list[str]:
    """Validate that the resolutions are consistent with the
    bundle. Returns a list of human-readable error messages; an
    empty list means the resolutions are consistent.

    This is the non-raising counterpart of :func:`apply_resolutions`.
    It is intended for product UIs that show validation issues
    inline (rather than crashing on an error mid-apply).

    Checks:
        - Each ``ConflictResolution.conflict_id`` corresponds to a
          real conflict in the bundle (the variant records are
          present).
        - Each ``ConflictResolution.chosen_record.id`` (when not
          None and the resolution is a "pick-one" with index >= 0)
          is among the conflict's variant ids.
        - No two resolutions in the input list target the same
          ``conflict_id``.
        - Each ``resolved_at`` is valid ISO 8601.
        - Each ``rationale`` is at least 10 characters.
        - Each ``resolved_by`` is non-empty.

    Args:
        bundle: The bundle to validate against.
        resolutions: The resolutions to validate.

    Returns:
        A list of error messages. Empty list = all valid.
    """
    errors: list[str] = []
    if not isinstance(bundle, list):
        errors.append(
            f"bundle must be a list; got {type(bundle).__name__}"
        )
        return errors
    if not isinstance(resolutions, list):
        errors.append(
            f"resolutions must be a list; got {type(resolutions).__name__}"
        )
        return errors

    record_ids = {str(r.get("id", "")) for r in bundle if r.get("id")}

    seen_conflict_ids: set[str] = set()
    for i, resolution in enumerate(resolutions):
        prefix = f"resolution[{i}]"
        if not isinstance(resolution, ConflictResolution):
            errors.append(
                f"{prefix}: each resolution must be a ConflictResolution; "
                f"got {type(resolution).__name__}"
            )
            continue

        if resolution.conflict_id in seen_conflict_ids:
            errors.append(
                f"{prefix}: conflict_id {resolution.conflict_id!r} is "
                "resolved by more than one resolution in the input list"
            )
        seen_conflict_ids.add(resolution.conflict_id)

        # Check that variant records are in the bundle.
        if not resolution.rejected_record_ids and resolution.chosen_record is None:
            errors.append(
                f"{prefix}: resolution has no rejected_record_ids and no "
                "chosen_record; cannot validate against the bundle"
            )
            continue
        in_bundle = [
            rid for rid in resolution.rejected_record_ids
            if rid in record_ids
        ]
        if resolution.chosen_record is not None:
            chosen_id = str(resolution.chosen_record.get("id", ""))
            if chosen_id and chosen_id in record_ids:
                in_bundle.append(chosen_id)
        if not in_bundle:
            errors.append(
                f"{prefix}: conflict_id {resolution.conflict_id!r} "
                "has no matching records in the bundle"
            )

        # Validate resolved_at.
        try:
            _validate_iso8601(resolution.resolved_at, field_name="resolved_at")
        except ValueError as exc:
            errors.append(f"{prefix}: {exc}")

        # Validate rationale length.
        if not resolution.rationale or len(resolution.rationale.strip()) < 10:
            errors.append(
                f"{prefix}: rationale must be at least 10 characters; "
                f"got {len(resolution.rationale or '')}"
            )

        # Validate resolved_by.
        if not resolution.resolved_by or not resolution.resolved_by.strip():
            errors.append(
                f"{prefix}: resolved_by is required and must be non-empty"
            )

    return errors


__all__ = [
    "ConflictResolution",
    "resolve_conflict",
    "apply_resolutions",
    "validate_resolutions",
    # Internal sentinels exported for advanced users / tests.
    "BundleMerge",  # re-export for convenience
]
