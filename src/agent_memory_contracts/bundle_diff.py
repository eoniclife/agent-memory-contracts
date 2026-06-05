"""Diff two bundles and describe the differences in set-semantic terms.

A *bundle diff* answers: what records were added, removed, or changed
between two bundles, treated as sets keyed by ``id_field``?  The
primitive is designed to support cache invalidation, audit logs,
and UI-side rendering of "what changed in this rebuild".

The short-circuit is the key optimisation: when two bundles have the
same :func:`bundle_fingerprint` they are identical, so the diff is
returned without iterating any records.

.. versionadded:: 0.4.0
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Iterable, Mapping

from .bundles import bundle_fingerprint


@dataclass(frozen=True)
class BundleDiff:
    """The result of diffing two bundles.

    Attributes:
        added: Records in ``b`` but not in ``a`` (each a plain dict).
        removed: Records in ``a`` but not in ``b`` (each a plain dict).
        changed: Tuples of ``(old_record, new_record)`` for records
            that share the same ``id_field`` value but differ in
            content.  Both sides are full plain dicts.
        unchanged_count: Number of records in both bundles with
            identical content.
    """

    added: list[dict] = field(default_factory=list)
    removed: list[dict] = field(default_factory=list)
    changed: list[tuple[dict, dict]] = field(default_factory=list)
    unchanged_count: int = 0


def _record_to_dict(record: Any) -> dict:
    """Convert a record to a plain dict for use in BundleDiff fields.

    Dataclass instances are flattened via ``asdict``.  Mappings are
    copied.  Anything else is coerced via the Mapping protocol.
    """
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    elif isinstance(record, Mapping):
        return dict(record)
    else:
        return dict(record)  # type: ignore[arg-type]


def _canonical_record(record: Any, id_field: str) -> tuple[str, str]:
    """Return ``(id_value, canonical_json)`` for a single record."""
    if is_dataclass(record) and not isinstance(record, type):
        rec_dict = asdict(record)
        id_value = getattr(record, id_field, "")
    elif isinstance(record, Mapping):
        rec_dict = dict(record)
        id_value = rec_dict.get(id_field, "")
    else:
        rec_dict = dict(record)  # type: ignore[arg-type]
        id_value = rec_dict.get(id_field, "")
    canonical = json.dumps(
        rec_dict,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return str(id_value), canonical


def bundle_diff(
    a: Iterable[Any],
    b: Iterable[Any],
    *,
    id_field: str = "id",
) -> BundleDiff:
    """Return the set-semantic diff between two bundles.

    Both ``a`` and ``b`` are treated as sets of records keyed by
    ``id_field``.  Duplicate ids within a single bundle are resolved
    by last-write-wins, exactly as in :func:`bundle_fingerprint`.

    When the two bundles have identical fingerprints the short-circuit
    fires and the function avoids the per-record diff loop.

    Args:
        a: The "before" bundle (an iterable of dicts, Mappings, or
            dataclass instances).
        b: The "after" bundle (same record types).
        id_field: The field that uniquely identifies each record.
            Defaults to ``"id"``.

    Returns:
        A :class:`BundleDiff` describing ``added``, ``removed``,
        ``changed``, and ``unchanged_count``.
    """
    # Materialise iterables once; bundle_fingerprint consumes the
    # iterator, and we need the lists for both fp and the diff loop.
    a_list = list(a)
    b_list = list(b)

    # Short-circuit: equal fingerprints mean identical bundles.
    # We still need to compute the correct unchanged_count even
    # when fingerprints match (a=[r,r] and b=[r] have the same fp
    # but a's raw length differs from the unique-record count).
    fp_a = bundle_fingerprint(a_list, id_field=id_field)
    fp_b = bundle_fingerprint(b_list, id_field=id_field)
    if fp_a == fp_b:
        a_by_id: dict[str, str] = {}
        for record in a_list:
            id_val, canonical = _canonical_record(record, id_field)
            a_by_id[id_val] = canonical
        b_by_id: dict[str, str] = {}
        for record in b_list:
            id_val, canonical = _canonical_record(record, id_field)
            b_by_id[id_val] = canonical
        unchanged_count = sum(
            1
            for id_val in a_by_id
            if id_val in b_by_id and a_by_id[id_val] == b_by_id[id_val]
        )
        return BundleDiff(
            added=[],
            removed=[],
            changed=[],
            unchanged_count=unchanged_count,
        )

    # Build id -> canonical_json maps (last-write-wins for dupes).
    a_by_id: dict[str, str] = {}
    for record in a_list:
        id_val, canonical = _canonical_record(record, id_field)
        a_by_id[id_val] = canonical

    b_by_id: dict[str, str] = {}
    for record in b_list:
        id_val, canonical = _canonical_record(record, id_field)
        b_by_id[id_val] = canonical

    a_ids = set(a_by_id)
    b_ids = set(b_by_id)

    added_ids = b_ids - a_ids
    removed_ids = a_ids - b_ids
    common_ids = a_ids & b_ids

    added: list[dict] = []
    for id_val in added_ids:
        added.append(json.loads(b_by_id[id_val]))

    removed: list[dict] = []
    for id_val in removed_ids:
        removed.append(json.loads(a_by_id[id_val]))

    changed: list[tuple[dict, dict]] = []
    unchanged_count = 0
    for id_val in common_ids:
        a_canon = a_by_id[id_val]
        b_canon = b_by_id[id_val]
        if a_canon == b_canon:
            unchanged_count += 1
        else:
            changed.append((json.loads(a_canon), json.loads(b_canon)))

    return BundleDiff(
        added=added,
        removed=removed,
        changed=changed,
        unchanged_count=unchanged_count,
    )


__all__ = ["BundleDiff", "bundle_diff"]