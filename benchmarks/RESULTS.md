# benchmark suite: bundle primitives
machine: Python 3.14.4 on Darwin arm64 (arm)
total_runtime_seconds: 3.629

Three primitives are measured: `bundle_fingerprint`,
`bundle_diff`, and `merge_bundles`. Per-script reports
(and a copy of the markdown tables) also live in
`benchmarks/RESULTS_fingerprint.md`, `RESULTS_diff.md`,
and `RESULTS_merge.md` respectively.

---

# benchmark: bundle_fingerprint
machine: Python 3.14.4 on Darwin arm64 (arm)

| n_records | bundles | seconds | per_record_us |
| ---: | ---: | ---: | ---: |
| 100 | 1 | 0.000248 | 2.48 |
| 1000 | 1 | 0.002459 | 2.46 |
| 10000 | 1 | 0.024911 | 2.49 |
| 50000 | 1 | 0.129674 | 2.59 |

---

# benchmark: bundle_diff
machine: Python 3.14.4 on Darwin arm64 (arm)

Each row diffs a pair of bundles of the same size, with 10%
of records carrying a different `note` value (so the
fingerprint short-circuit does not fire).

| n_records | bundles | seconds | per_record_us |
| ---: | ---: | ---: | ---: |
| 100 | 2 | 0.001003 | 10.03 |
| 1000 | 2 | 0.010451 | 10.45 |
| 10000 | 2 | 0.108533 | 10.85 |
| 50000 | 2 | 0.546178 | 10.92 |

---

# benchmark: merge_bundles
machine: Python 3.14.4 on Darwin arm64 (arm)

Each row merges `bundles` copies of the same bundle of
`n_records` records, with `prefer='last'`. Same-id records
from different bundles go through the conflict-resolution
path; identical records do not generate a conflict.

| n_records | bundles | seconds | per_record_us |
| ---: | ---: | ---: | ---: |
| 100 | 2 | 0.000771 | 7.71 |
| 1000 | 2 | 0.007845 | 7.84 |
| 10000 | 2 | 0.085473 | 8.55 |
| 100 | 3 | 0.001210 | 12.10 |
| 1000 | 3 | 0.012054 | 12.05 |
| 10000 | 3 | 0.124978 | 12.50 |
