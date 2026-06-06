"""Shared helpers for the bundle-primitive benchmark suite.

This module is intentionally tiny. It exists so the three benchmark
scripts (`time_fingerprint.py`, `time_diff.py`, `time_merge.py`) all
synthesise bundles the same way and all print the same kind of row.

Nothing in here depends on the library under test -- it only builds
plain dicts that look like the records the bundle primitives consume.
That keeps the data-construction cost visible in the benchmark and
out of the timed path: every script materialises its records once
and feeds the same list(s) to the primitive under test in a tight
loop.

The functions are prefixed with an underscore on purpose: this is a
shared test-fixture module, not a public API. Import them as
``from common import _make_records, _format_row``.
"""

from __future__ import annotations

from typing import Any


def _make_records(n: int, *, with_collision: bool = False) -> list[dict]:
    """Return a deterministic list of ``n`` record dicts.

    Each record has the fields the bundle primitives expect:
    ``id`` (unique per record), ``schema_version`` (constant), and
    ``value`` (an integer). A couple of extra fields
    (``name``, ``tags``, ``note``) are included so the dicts are
    not pathologically small and so there is something for a
    content-only change to touch in the diff benchmark.

    The output is fully deterministic: given the same ``n`` the
    same list of dicts comes out, in the same order. That matters
    because the diff benchmark depends on the layout being stable
    across runs (and machines).

    Args:
        n: Number of records to produce.
        with_collision: When True, 10% of the records (every 10th
            record, at index ``i`` where ``i % 10 == 0``) have
            their ``note`` field set to a per-record value rather
            than the default. Two bundles built with the same ``n``
            and ``with_collision=True`` but with **different**
            values passed in for the collision field will therefore
            have the same ids but differ on those records' content
            -- the classic "same id, different bytes" case the
            diff primitive is supposed to catch.

            The function does not actually take a "second" collision
            value: that is the caller's job. See ``time_diff.py``
            for the pattern (it builds the second bundle by calling
            this function with a different ``_collision_token``).
    """
    records: list[dict] = []
    for i in range(n):
        if with_collision and i % 10 == 0:
            note = f"collision_token_A_record_{i}"
        else:
            note = f"note_{i}"
        records.append(
            {
                "id": f"rec_{i:08x}",
                "schema_version": "1.0.0",
                "value": i,
                "name": f"record {i}",
                "tags": ["tag-a", "tag-b"] if i % 2 == 0 else ["tag-c"],
                "note": note,
            }
        )
    return records


def _make_records_with_collision_alt(n: int) -> list[dict]:
    """Variant of :func:`_make_records` for the second half of a diff pair.

    Returns the same set of records as ``_make_records(n,
    with_collision=True)`` but with the ``note`` field on the
    collision positions set to a *different* per-record value.
    Same ids, same content everywhere else, different content on
    ~10% of the records -- exactly what a real "I edited these
    entries" diff looks like.

    Bundles produced this way will have a **different**
    ``bundle_fingerprint`` from bundles produced by
    ``_make_records(n, with_collision=True)``, so the short-circuit
    in ``bundle_diff`` does not fire and the per-record diff loop
    actually runs.
    """
    records: list[dict] = []
    for i in range(n):
        if i % 10 == 0:
            note = f"collision_token_B_record_{i}"
        else:
            note = f"note_{i}"
        records.append(
            {
                "id": f"rec_{i:08x}",
                "schema_version": "1.0.0",
                "value": i,
                "name": f"record {i}",
                "tags": ["tag-a", "tag-b"] if i % 2 == 0 else ["tag-c"],
                "note": note,
            }
        )
    return records


def _format_row(label: str, n: int, seconds: float, bundles: int = 1) -> str:
    """Return a markdown table row for one benchmark measurement.

    The columns match the spec: ``n_records``, ``bundles``,
    ``seconds``, ``per_record_us``. The ``label`` argument is not
    part of the row itself (each script only emits rows of one
    kind); it is kept on the signature for readability in case
    future benchmarks want a free-form label column.

    Args:
        label: Human-readable description of the row (currently
            ignored in the output, kept for API stability).
        n: Number of records per bundle.
        seconds: Wall-clock time the call took, in seconds.
        bundles: Number of input bundles. ``1`` for fingerprint;
            ``2`` for a diff pair; ``2`` or ``3`` for a merge.

    Returns:
        A markdown table row string with trailing newline.
    """
    del label  # unused, kept for API stability
    per_record_us = (seconds / n) * 1_000_000.0
    return (
        f"| {n} | {bundles} | {seconds:.6f} | {per_record_us:.2f} |\n"
    )


def _table_header() -> str:
    """Return the markdown table header for a benchmark script."""
    return (
        "| n_records | bundles | seconds | per_record_us |\n"
        "| ---: | ---: | ---: | ---: |\n"
    )
