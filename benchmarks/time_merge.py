"""Benchmark ``merge_bundles`` on 2 and 3 input bundles (100..10_000 records).

Run from the repo root:

    PYTHONPATH=src python3 benchmarks/time_merge.py

Each row reports the cost of merging N bundles of M records each
(where N is 2 or 3 and M is 100, 1_000, or 10_000). All bundles in
a row are built from the same ``_make_records`` call, so the merge
sees N copies of every record and the conflict-resolution path
runs: same id from multiple bundles with the same content is
treated as a non-conflict but is still recorded in the
``contributing`` list and canonicalised once per bundle.

Sizes skip the 50_000 size used by the fingerprint and diff
benchmarks: merge is the most expensive of the three primitives
(constant-factor bigger than fingerprint, and 2-3x the input
volume per call) and we do not want the run-all to blow past
60s on a single row.
"""

from __future__ import annotations

import platform
import sys
import timeit
from pathlib import Path

from agent_memory_contracts import merge_bundles

from common import _format_row, _make_records, _table_header


SIZES: tuple[int, ...] = (100, 1_000, 10_000)
BUNDLE_COUNTS: tuple[int, ...] = (2, 3)


def _reps_for(n: int, bundle_count: int) -> int:
    """Per-size, per-bundle-count inner-rep count for ``timeit``.

    Total input volume is ``n * bundle_count``. Calibration targets
    the same 0.1-1s per row as the other benchmarks.
    """
    total = n * bundle_count
    if total <= 200:
        return 100
    if total <= 3_000:
        return 30
    if total <= 30_000:
        return 3
    return 1


def _machine_info() -> str:
    py = platform.python_version()
    system = platform.system()
    machine = platform.machine()
    processor = platform.processor()
    cpu_part = f" ({processor})" if processor else ""
    return f"Python {py} on {system} {machine}{cpu_part}"


def run() -> str:
    lines: list[str] = []
    lines.append("# benchmark: merge_bundles\n")
    lines.append(f"machine: {_machine_info()}\n")
    lines.append("\n")
    lines.append("Each row merges `bundles` copies of the same bundle of\n")
    lines.append("`n_records` records, with `prefer='last'`. Same-id records\n")
    lines.append("from different bundles go through the conflict-resolution\n")
    lines.append("path; identical records do not generate a conflict.\n")
    lines.append("\n")
    lines.append(_table_header())

    for bundle_count in BUNDLE_COUNTS:
        for n in SIZES:
            # All bundles in a row are the same bundle repeated
            # ``bundle_count`` times. That is a realistic "fan-in
            # from N importers" workload and exercises the
            # contributing/conflict accounting in the merge
            # implementation.
            records = _make_records(n)
            bundles = tuple(records for _ in range(bundle_count))

            reps = _reps_for(n, bundle_count)
            # ``merge_bundles`` is variadic; the cleanest way to
            # call it from a ``timeit`` statement is to bind a
            # closure-like wrapper. We do that in ``globals`` so
            # the statement itself is the function call.
            def _do_merge() -> None:
                merge_bundles(*bundles)

            total = timeit.timeit(
                stmt="_do_merge()",
                globals={"_do_merge": _do_merge},
                number=reps,
            )
            seconds = total / reps
            lines.append(_format_row("merge", n, seconds, bundles=bundle_count))

    return "".join(lines)


def main() -> int:
    report = run()
    sys.stdout.write(report)
    out_path = Path(__file__).with_name("RESULTS_merge.md")
    out_path.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
