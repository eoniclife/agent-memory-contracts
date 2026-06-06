"""Run all three bundle-primitive benchmarks in sequence.

Run from the repo root:

    PYTHONPATH=src python3 benchmarks/run_all.py

The driver imports each benchmark module's ``run()`` function
directly (so we get the report strings back as Python values, not
captured stdout) and concatenates them into
``benchmarks/RESULTS.md``, separated by a horizontal rule. The
machine-info header appears once at the top.

We also print the concatenated report to stdout so the
``run_all.py`` invocation is informative in a terminal.

Stdlib only: ``subprocess`` is not needed because we import in
process. The drivers of the per-script ``RESULTS_*.md`` files are
still useful as standalone artifacts; this script does not delete
them.
"""

from __future__ import annotations

import platform
import sys
import time
from pathlib import Path

import time_diff
import time_fingerprint
import time_merge


def _machine_info() -> str:
    """One-line machine description, identical shape to the per-script versions."""
    py = platform.python_version()
    system = platform.system()
    machine = platform.machine()
    processor = platform.processor()
    cpu_part = f" ({processor})" if processor else ""
    return f"Python {py} on {system} {machine}{cpu_part}"


def main() -> int:
    started = time.perf_counter()

    fp_report = time_fingerprint.run()
    diff_report = time_diff.run()
    merge_report = time_merge.run()

    elapsed = time.perf_counter() - started

    sections = [
        "# benchmark suite: bundle primitives\n",
        f"machine: {_machine_info()}\n",
        f"total_runtime_seconds: {elapsed:.3f}\n",
        "\n",
        "Three primitives are measured: `bundle_fingerprint`,\n",
        "`bundle_diff`, and `merge_bundles`. Per-script reports\n",
        "(and a copy of the markdown tables) also live in\n",
        "`benchmarks/RESULTS_fingerprint.md`, `RESULTS_diff.md`,\n",
        "and `RESULTS_merge.md` respectively.\n",
        "\n",
        "---\n",
        "\n",
        fp_report,
        "\n---\n",
        "\n",
        diff_report,
        "\n---\n",
        "\n",
        merge_report,
    ]
    combined = "".join(sections)

    # Persist the canonical baseline.
    out_path = Path(__file__).with_name("RESULTS.md")
    out_path.write_text(combined, encoding="utf-8")

    # Echo to stdout for interactive use.
    sys.stdout.write(combined)
    sys.stdout.write(f"\n_total wall-clock for run_all.py: {elapsed:.3f} s_\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
