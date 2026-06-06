"""Benchmark ``bundle_fingerprint`` on bundles of 100..50_000 records.

Run from the repo root:

    PYTHONPATH=src python3 benchmarks/time_fingerprint.py

The script prints a markdown table to stdout and also writes the
same table (preceded by a machine-info header) to
``benchmarks/RESULTS_fingerprint.md`` so the results can be checked
into the repo as a baseline.

Stdlib only: ``timeit``, ``time.perf_counter``, and the project
itself. No new dependencies.

Methodology:

- For each size, build the bundle once and then call
  ``bundle_fingerprint`` repeatedly inside ``timeit.timeit`` to
  amortise the Python call overhead. The number of inner reps is
  calibrated per size so the total timed wall-clock is in the
  0.1s-1s range; the reported ``seconds`` is the mean per call.
- The smallest size (100) gets a lot of reps because each call is
  well under a millisecond. The largest size (50_000) gets one
  rep because each call already takes low hundreds of ms.
- The harness does *not* warm up the JIT or GC: this library is
  pure CPython, so there is nothing to warm. Each measurement is
  a single ``timeit.timeit`` call whose internal repetition loop
  amortises transient startup cost.
"""

from __future__ import annotations

import platform
import sys
import timeit
from pathlib import Path

# The library under test. ``PYTHONPATH=src`` makes this importable
# without an editable install.
from agent_memory_contracts import bundle_fingerprint

from common import _format_row, _make_records, _table_header


# Sizes to benchmark. These are the four sizes the spec asks for.
SIZES: tuple[int, ...] = (100, 1_000, 10_000, 50_000)


def _reps_for(n: int) -> int:
    """Pick a per-size number of inner reps for ``timeit``.

    Goal: total timed wall-clock between 0.1s and 1s, so a single
    measurement resolves to ~1ms-per-call precision. We do not
    know the per-call cost ahead of time, so this is a calibration
    table built from the expected order of magnitude (fingerprint
    is roughly O(n) and the spec expects 1k records in <10ms).
    """
    if n <= 100:
        return 200
    if n <= 1_000:
        return 100
    if n <= 10_000:
        return 5
    return 1


def _machine_info() -> str:
    """Return a short, generic machine description for the header.

    Intentionally omits hostname, user, or any other
    personally-identifying detail. The shape is ``Python X.Y.Z on
    <system> <arch> (<cpu>)`` -- e.g. ``Python 3.12.11 on Darwin
    arm64 (arm)``.
    """
    py = platform.python_version()
    system = platform.system()  # "Darwin", "Linux", "Windows"
    machine = platform.machine()  # "arm64", "x86_64", ...
    processor = platform.processor()  # "arm", "i386", "" on Linux
    cpu_part = f" ({processor})" if processor else ""
    return f"Python {py} on {system} {machine}{cpu_part}"


def run() -> str:
    """Run the benchmark and return the markdown report as a string."""
    lines: list[str] = []
    lines.append("# benchmark: bundle_fingerprint\n")
    lines.append(f"machine: {_machine_info()}\n")
    lines.append("\n")
    lines.append(_table_header())

    for n in SIZES:
        records = _make_records(n)
        reps = _reps_for(n)
        # ``timeit.timeit`` returns the total seconds for ``reps``
        # executions of the statement. We bind ``records`` via
        # ``globals=globals()`` so the call is a real local lookup,
        # not a globals()-dict re-lookup on every iteration.
        total = timeit.timeit(
            stmt="bundle_fingerprint(records)",
            globals={"bundle_fingerprint": bundle_fingerprint,
                     "records": records},
            number=reps,
        )
        seconds = total / reps
        lines.append(_format_row("fingerprint", n, seconds, bundles=1))

    return "".join(lines)


def main() -> int:
    report = run()
    # Print to stdout for interactive runs.
    sys.stdout.write(report)
    # Also persist a per-script file (the spec asks for this; it
    # makes the benchmark results easy to diff between runs).
    out_path = Path(__file__).with_name("RESULTS_fingerprint.md")
    out_path.write_text(report, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
