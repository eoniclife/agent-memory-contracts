"""Many-to-one set-semantic merge of bundles.

A *bundle merge* takes N bundles (each an iterable of records keyed by
``id_field``) and returns a single bundle that is their set union. When
the same id appears in multiple input bundles with **different**
content, the merge reports a conflict and lets the caller pick a
resolution policy via ``prefer``. When the same id appears more than
once **within** a single input bundle, the duplicate is resolved
silently by last-write-wins (matching the convention used by
:func:`bundle_fingerprint` and :func:`bundle_diff`).

Contrast with :func:`bundle_diff`:

- :func:`bundle_diff` answers "what changed between **two** bundles?"
  and returns a structured diff (added / removed / changed /
  unchanged). It is the right primitive for *change detection* and
  for rendering "what changed in this rebuild".
- :func:`merge_bundles` answers "what is the union of **N** bundles?"
  and returns the merged bundle plus a list of conflicts that arose
  during the union. It is the right primitive for *fan-in*: pulling
  records from multiple sources into one consolidated bundle.

Typical use cases:

- **Multi-source ingest.** Pull evidence records from several
  importers (chat transcripts, repo diffs, bookmarks, manual notes)
  and unify them into one evidence bundle. Conflicts are surfaced
  for the reducer to triage.
- **Bidirectional sync.** When syncing between two systems that may
  have independently updated the same record, merge both views into
  one bundle and surface the conflicts for manual review.
- **Backfill.** Append a historical re-extraction to an existing
  bundle. Disjoint records are added; overlapping records that
  changed are flagged as conflicts.

Like the rest of the bundle primitives, this module is
standard-library only. Records can be dicts, Mappings, or dataclass
instances; comparison is via canonical JSON.

.. versionadded:: 0.5.0
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .bundles import _canonical_record


@dataclass(frozen=True)
class BundleMerge:
    """The result of merging N bundles into one.

    Attributes:
        records: The merged bundle as a list of plain dicts, sorted
            by ``id_field`` for deterministic output.
        conflicts: One entry per id that appeared with **different
            content** in different input bundles. Each entry is a
            tuple ``(id, [(bundle_index, record_dict), ...])`` where
            ``bundle_index`` is the position of the bundle in the
            call to :func:`merge_bundles` (0-based). The list
            preserves the input order of bundles and, within a
            bundle, the input order of records. Records are full
            dicts (or dataclass-asdict) -- not just the conflicting
            fields. ``conflicts`` is populated whenever two bundles
            disagree on an id's content, regardless of the
            ``prefer`` policy; the policy only affects the *winner*
            in ``records``.
        duplicate_ids: Ids that appeared more than once in a single
            input bundle. The intra-bundle last-write-wins policy
            means these were resolved silently; the list is exposed
            so callers can audit (e.g. flag ingestion bugs). Sorted
            in the order in which the duplicates were first
            observed (lowest bundle index, then lowest record
            position).
    """

    records: list[dict] = field(default_factory=list)
    conflicts: list[tuple[str, list[tuple[int, dict]]]] = field(default_factory=list)
    duplicate_ids: list[str] = field(default_factory=list)


def merge_bundles(
    *bundles: Iterable[Any],
    id_field: str = "id",
    prefer: str = "last",
) -> BundleMerge:
    """Return the set-semantic union of N bundles.

    Each input bundle is an iterable of records (dicts, Mappings, or
    dataclass instances). Records are deduplicated by ``id_field``;
    duplicate ids within a single input bundle are resolved by
    last-write-wins and reported in ``duplicate_ids``.

    When the same id appears in two or more different bundles with
    different content, the merge is a *conflict*:

    - Under ``prefer="last"`` (the default) and ``prefer="first"``,
      the chosen record is the one from the bundle at the relevant
      end of the call sequence, but the conflict is still recorded
      in ``conflicts`` so the caller can audit.
    - Under ``prefer="raise"``, the merge aborts with ``ValueError``
      the moment a conflict is detected. No partial result is
      returned.

    The merged ``records`` list is sorted by ``id_field`` for
    deterministic output, independent of input order or count.

    Args:
        *bundles: The input bundles. Each is an iterable of records.
            No bundles at all is allowed; the result is an empty
            ``BundleMerge``.
        id_field: The field that uniquely identifies each record.
            Defaults to ``"id"``.
        prefer: How to resolve cross-bundle conflicts. One of:

            - ``"last"`` (default): the record from the **last**
              bundle that carries the id wins.
            - ``"first"``: the record from the **first** bundle that
              carries the id wins.
            - ``"raise"``: raise ``ValueError`` on the first conflict.

    Returns:
        A :class:`BundleMerge` with the merged records, the list of
        conflicts (always populated when present, even if
        ``prefer`` resolved them), and the list of ids that were
        duplicated within a single input bundle.

    Raises:
        ValueError: If ``prefer`` is not one of ``"last"``,
            ``"first"``, ``"raise"``, or if ``prefer="raise"`` and a
            cross-bundle conflict is detected.
    """
    if prefer not in ("last", "first", "raise"):
        raise ValueError(
            f"prefer must be 'last', 'first', or 'raise'; got {prefer!r}"
        )

    # Per-id accumulator:
    #   winning_canonical: the canonical JSON of the record that
    #     will end up in the merged bundle (per the prefer policy).
    #   winning_dict: the matching plain dict (output form).
    #   contributing: list of (bundle_index, record_dict) -- one
    #     entry per bundle that carried this id, in bundle order.
    #     We keep this even when the canonicals all agree, so the
    #     conflict list is always informative.
    #   bundle_indices_seen: parallel list of bundle indices for
    #     `contributing` (used to pick first/last winner).
    #   duplicate_seen: whether this id was duplicated *within* a
    #     single bundle.
    winning_canonical: dict[str, str] = {}
    winning_dict: dict[str, dict] = {}
    contributing: dict[str, list[tuple[int, dict]]] = {}
    bundle_indices_seen: dict[str, list[int]] = {}
    duplicate_ids_in_order: list[str] = []
    duplicate_seen: set[str] = set()

    for bundle_index, bundle in enumerate(bundles):
        # Per-bundle last-write-wins.
        per_bundle_winner: dict[str, str] = {}
        per_bundle_dict: dict[str, dict] = {}
        per_bundle_order: list[str] = []  # first-seen order in this bundle

        for record in bundle:
            id_value, canonical = _canonical_record(record, id_field)
            if id_value in per_bundle_winner:
                # Duplicate within this bundle. Last write wins; track
                # the id as duplicated (report it once).
                if id_value not in duplicate_seen:
                    duplicate_seen.add(id_value)
                    duplicate_ids_in_order.append(id_value)
            else:
                per_bundle_order.append(id_value)
            per_bundle_winner[id_value] = canonical
            # We also need the plain dict for `contributing`.
            # _canonical_record returned the canonical JSON; we
            # can either json.loads(canonical) or recompute via
            # asdict/dict(record). The canonical is already
            # correct, so json.loads is the cheapest path that
            # preserves the exact bytes we hashed.
            per_bundle_dict[id_value] = json.loads(canonical)

        for id_value in per_bundle_order:
            canonical = per_bundle_winner[id_value]
            rec_dict = per_bundle_dict[id_value]
            if id_value not in winning_canonical:
                # First time we see this id across all bundles.
                winning_canonical[id_value] = canonical
                winning_dict[id_value] = rec_dict
                contributing[id_value] = [(bundle_index, rec_dict)]
                bundle_indices_seen[id_value] = [bundle_index]
            else:
                # Already seen in an earlier bundle.
                bundle_indices_seen[id_value].append(bundle_index)
                if canonical == winning_canonical[id_value]:
                    # Same content as the current winner. The
                    # contributing list gets a new entry so the
                    # caller can see this id was present in
                    # multiple bundles, but no conflict.
                    contributing[id_value].append((bundle_index, rec_dict))
                else:
                    # Conflict: this id has different content in
                    # different bundles.
                    contributing[id_value].append((bundle_index, rec_dict))
                    if prefer == "raise":
                        raise ValueError(
                            f"merge_bundles: id {id_value!r} has different "
                            f"content in bundle {bundle_index} than in an "
                            f"earlier bundle (prefer='raise')."
                        )
                    elif prefer == "last":
                        # Last bundle wins. Update both the
                        # canonical and the output dict, but keep
                        # the old contribution in the conflicts
                        # list -- the caller still wants to know
                        # the disagreement happened.
                        winning_canonical[id_value] = canonical
                        winning_dict[id_value] = rec_dict
                    else:  # prefer == "first" -- keep first winner
                        pass

    # Build the conflict list: an id is a conflict iff it appeared
    # in >= 2 different bundles with >= 2 distinct canonicals.
    # Equivalently: contributing has >= 2 entries AND not all
    # canonicals are equal. We can derive this from the contributing
    # list by canonicalising each entry's dict and grouping equal
    # values. To keep this O(n) per id, we re-canonicalise each
    # contributing dict and dedupe.
    conflicts: list[tuple[str, list[tuple[int, dict]]]] = []
    for id_value, entries in contributing.items():
        # Group by canonical. If > 1 group, this is a conflict.
        # We do not re-canonicalise; we compare dict equality
        # via the canonical that was already produced in the loop
        # above. Re-derive by recomputing canonical for each entry
        # is the cleanest, and we already have per-bundle canonicals
        # -- but those are per-bundle winners. If the same id
        # appears twice in a single bundle, the per-bundle winner
        # is the last occurrence, and the contributing list only
        # has that one entry for that bundle. So
        # `per-bundle-canonical == json.dumps(contributing[i][1])`
        # is always true. Use dict equality directly: two records
        # with the same canonical JSON will compare equal as
        # dicts.
        unique_dicts: list[dict] = []
        for _idx, rec in entries:
            if rec not in unique_dicts:
                unique_dicts.append(rec)
        if len(unique_dicts) > 1:
            # Sort the entries for stability: bundle index
            # ascending, then position-within-bundle ascending.
            # Position-within-bundle is not separately tracked; we
            # only have bundle index. Within a bundle the id can
            # only appear once after last-write-wins, so the sort
            # by bundle index is sufficient and stable.
            sorted_entries = sorted(entries, key=lambda e: e[0])
            conflicts.append((id_value, sorted_entries))

    # Sort conflicts by id for deterministic output.
    conflicts.sort(key=lambda c: c[0])

    # Sort records by id for deterministic output. Use string
    # sort on the id -- the canonical form (id string) is the
    # content-derived key.
    sorted_ids = sorted(winning_dict.keys())
    records = [winning_dict[k] for k in sorted_ids]

    return BundleMerge(
        records=records,
        conflicts=conflicts,
        duplicate_ids=duplicate_ids_in_order,
    )


__all__ = ["BundleMerge", "merge_bundles"]
