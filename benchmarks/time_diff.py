"""Benchmark ``bundle_diff`` on pairs of bundles (100..50_000 records).

Run from the repo root:

    PYTHONPATH=src python3 benchmarks/time_diff.py

Each pair of bundles has the same ids, the same content on 90% of
the records, and **different** content on the remaining 10% (the
"collision" positions, at index ``i % 10 == 0``). That guarantees
the two bundles have different fingerprints, so the
short-circuit in :func:`bundle_diff` does **not** fire and the
per-record diff loop is what gets timed -- which is the work we
actually want to measure.

Sizes match the fingerprint benchmark (100, 1_000, 10_000, 50_000)
so the per_record_us columns are directly comparable.
"""

from __future__ import annotations

import platform
import sys
import timeit
from pathlib import Path

# ``bundle_diff`` is not re-exported in the package ``__init__`` (it
# lives in a submodule), so we import the function from the
# submodule path directly. The same pattern is used by
# ``__main__.py``.
from agent_memory_contracts.bundle_diff import bundle_diff

from common import (
    _format_row,
    _make_records,
    _make_records_with_collision_alt,
    _table_header,
)


SIZES: tuple[int, ...] = (100, 1_000, 10_000, 50_000)


def _reps_for(n: int) -> int:
    """Per-size inner-rep count for ``timeit``.

    ``bundle_diff`` is roughly 2x the work of ``bundle_fingerprint``
    (it canonicalises both sides and then diffs), so the
    calibration table is correspondingly tighter on the larger
    sizes to keep total wall-clock under ~1s per row.
    """
    if n <= 100:
        return 100
    if n <= 1_000:
        return 30
    if n <= 10_000:
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
    lines.append("# benchmark: bundle_diff\n")
    lines.append(f"machine: {_machine_info()}\n")
    lines.append("\n")
    lines.append("Each row diffs a pair of bundles of the same size, with 10%\n")
    lines.append("of records carrying a different `note` value (so the\n")
    lines.append("fingerprint short-circuit does not fire).\n")
    lines.append("\n")
    lines.append(_table_header())

    for n in SIZES:
        a = _make_records(n, with_collision=True)
        b = _make_records_with_collision_alt(n)
        # Sanity: the two bundles really are different (the short
        # circuit would otherwise time ``bundle_fingerprint``'s
        # internal loop, not the per-record diff). If this ever
        # raises, the harness is broken and the timing is lying.
        from agent_memory_contracts import bundle_fingerprint
        fp_a = bundle_fingerprint(a)
        fp_b = bundle_fingerprint(b)
        if fp_a == fp_b:
            raise RuntimeError(
                f"diff harness broken: bundles of size {n} have the "
                f"same fingerprint ({fp_a}); the short-circuit would "
                f"hide the per-record diff work."
            )

        reps = _reps_for(n)
        total = timeit.timeit(
            stmt="bundle_diff(a, b)",
            globals={"bundle_diff": bundle_diff, "a": a, "b": b},
            number=reps,
        )
        seconds = total / reps
        lines.append(_format_row("diff", n, seconds, bundles=2))

    return "".join(lines)


def main() -> int:
    report = run()
    sys.stdout.write(report)
    out_path = Path(__file__).with_name("RESULTS_diff.md")
    out_path.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
