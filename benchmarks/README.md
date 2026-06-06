# benchmarks

A stdlib-only benchmark suite for the bundle-level primitives in
`agent_memory_contracts`:

- [`bundle_fingerprint`](../../src/agent_memory_contracts/bundles.py)
- [`bundle_diff`](../../src/agent_memory_contracts/bundle_diff.py)
- [`merge_bundles`](../../src/agent_memory_contracts/merge.py)

The suite exists so users can see the performance envelope of the
three primitives on their own hardware, and so the project ships a
reproducible baseline (`RESULTS.md`) that future regressions can be
diffed against.

The suite is **stdlib only**. No `pytest-benchmark`, no `asv`, no
`pyperf` — just `timeit` and `time.perf_counter`. The only
import-time dependency is the library under test itself, which is
already stdlib-only.

## What's measured

| script | primitive | what it does | sizes |
| --- | --- | --- | --- |
| `time_fingerprint.py` | `bundle_fingerprint` | SHA-256 over the canonical-JSON union of one bundle | 100, 1k, 10k, 50k records |
| `time_diff.py` | `bundle_diff` | set-semantic diff of a pair of bundles, with 10% of records colliding on a non-id field | 100, 1k, 10k, 50k records per bundle |
| `time_merge.py` | `merge_bundles` | union of N identical bundles (exercises the conflict-resolution path) | 100, 1k, 10k records per bundle × {2, 3} bundles |

For each size the script runs the primitive several times inside
`timeit.timeit`, divides by the inner-rep count, and reports a
markdown table with the columns

```
| n_records | bundles | seconds | per_record_us |
```

`per_record_us` is `(seconds / n_records) × 1e6`. It is the most
useful column for "how much does one record cost".

## How to run

From the repo root:

```bash
PYTHONPATH=src python3 benchmarks/time_fingerprint.py
PYTHONPATH=src python3 benchmarks/time_diff.py
PYTHONPATH=src python3 benchmarks/time_merge.py

# Or all three at once:
PYTHONPATH=src python3 benchmarks/run_all.py
```

Each per-script command prints a markdown table to stdout and also
writes it to `benchmarks/RESULTS_<primitive>.md`. `run_all.py`
concatenates the three tables into `benchmarks/RESULTS.md` (with a
machine-info header and a total-runtime footer) and echoes the
concatenated result to stdout.

The whole `run_all.py` should finish in well under 60 seconds on
any reasonable machine. On a 2024-class Apple M-series laptop the
total runtime is typically 5-10 seconds.

## How to read the output

A row like

```
| 10000 | 1 | 0.029415 | 2.94 |
```

means: a single `bundle_fingerprint` call on a bundle of 10 000
records took 29.4 ms on the reported machine, which works out to
2.94 microseconds per record. The `per_record_us` column should be
roughly constant across sizes if the primitive is genuinely
O(n); a per_record_us that grows with n is a red flag for an
accidental O(n²).

The "expected envelope" the suite is calibrated against is
(roughly):

- 1 000 records: single-digit ms.
- 10 000 records: tens of ms.
- 50 000 records: low hundreds of ms.

Numbers that are 100× off those values usually mean the harness
has a bug (most often: timing the data-construction step too, or
an accidental O(n²) introduced by a recent refactor). Numbers that
are 2-3× *better* than those values just mean the machine is
faster; that is fine and is the whole reason the suite runs on
the user's hardware rather than on a single reference rig.

## Reproducing the baseline

The committed `RESULTS.md` is the baseline captured on a Python
3.12 / arm64 Darwin machine (Apple M-series). To regenerate it on
your own hardware:

```bash
PYTHONPATH=src python3 benchmarks/run_all.py
git diff benchmarks/RESULTS.md
```

A diff of the `seconds` and `per_record_us` columns is the
expected outcome; a diff of the *structure* (number of rows,
column order) is a sign that someone changed the harness and
should re-read this README.

## Why stdlib only

Two reasons.

1. The library itself is stdlib-only. The benchmark should not
   pull in dependencies the library does not need, or it stops
   being a faithful "what does this cost to use?" measurement.
2. `timeit` is genuinely good at the thing we are doing: amortise
   call overhead with a tight inner-rep loop, and give us a
   high-precision mean. `pyperf` would be a marginal improvement
   for the price of a new dependency, and `pytest-benchmark` would
   add a `dev` dependency that is not otherwise needed.

If the suite ever needs a more sophisticated methodology (e.g.
GC-pause control, statistical confidence intervals across runs),
that is a fine moment to revisit the dependency question. As of
this writing it does not.
