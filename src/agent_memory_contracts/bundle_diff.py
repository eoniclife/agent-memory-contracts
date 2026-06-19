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
from dataclasses import dataclass, field
from typing import Any, Iterable

from .bundles import DuplicateMode, _canonical_records_by_id, bundle_fingerprint


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

    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    changed: list[tuple[dict[str, Any], dict[str, Any]]] = field(default_factory=list)
    unchanged_count: int = 0


def bundle_diff(
    a: Iterable[Any],
    b: Iterable[Any],
    *,
    id_field: str = "id",
    duplicate_mode: DuplicateMode = "last",
) -> BundleDiff:
    """Return the set-semantic diff between two bundles.

    Both ``a`` and ``b`` are treated as sets of records keyed by
    ``id_field``. Duplicate ids within either bundle are resolved by
    ``duplicate_mode``, exactly as in :func:`bundle_fingerprint`.

    When the two bundles have identical fingerprints the short-circuit
    fires and the function avoids the per-record diff loop.

    Args:
        a: The "before" bundle (an iterable of dicts, Mappings, or
            dataclass instances).
        b: The "after" bundle (same record types).
        id_field: The field that uniquely identifies each record.
            Defaults to ``"id"``.
        duplicate_mode: How to resolve repeated ``id_field`` values
            within each input bundle. ``"last"`` is the legacy default;
            ``"identical"`` collapses byte-identical repeats and raises
            on divergent same-id payloads; ``"raise"`` raises
            :class:`agent_memory_contracts.DuplicateRecordError` on any
            repeated id.

    Returns:
        A :class:`BundleDiff` describing ``added``, ``removed``,
        ``changed``, and ``unchanged_count``.
    """
    # Materialise iterables once; bundle_fingerprint consumes the
    # iterator, and we need the lists for both fp and the diff loop.
    a_list = list(a)
    b_list = list(b)

    a_by_id = _canonical_records_by_id(
        a_list, id_field=id_field, duplicate_mode=duplicate_mode,
    )
    b_by_id = _canonical_records_by_id(
        b_list, id_field=id_field, duplicate_mode=duplicate_mode,
    )

    # Short-circuit: equal fingerprints mean identical bundles.
    # We still need to compute the correct unchanged_count even
    # when fingerprints match (a=[r,r] and b=[r] have the same fp
    # but a's raw length differs from the unique-record count).
    fp_a = bundle_fingerprint(
        a_list, id_field=id_field, duplicate_mode=duplicate_mode,
    )
    fp_b = bundle_fingerprint(
        b_list, id_field=id_field, duplicate_mode=duplicate_mode,
    )
    if fp_a == fp_b:
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

    a_ids = set(a_by_id)
    b_ids = set(b_by_id)

    added_ids = b_ids - a_ids
    removed_ids = a_ids - b_ids
    common_ids = a_ids & b_ids

    added: list[dict[str, Any]] = []
    for id_val in added_ids:
        added.append(json.loads(b_by_id[id_val]))

    removed: list[dict[str, Any]] = []
    for id_val in removed_ids:
        removed.append(json.loads(a_by_id[id_val]))

    changed: list[tuple[dict[str, Any], dict[str, Any]]] = []
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
