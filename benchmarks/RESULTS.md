# benchmark suite: bundle primitives
machine: Python 3.14.4 on Darwin arm64 (arm)
total_runtime_seconds: 3.653

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
| 100 | 1 | 0.000254 | 2.54 |
| 1000 | 1 | 0.002572 | 2.57 |
| 10000 | 1 | 0.025800 | 2.58 |
| 50000 | 1 | 0.134591 | 2.69 |

---

# benchmark: bundle_diff
machine: Python 3.14.4 on Darwin arm64 (arm)

Each row diffs a pair of bundles of the same size, with 10%
of records carrying a different `note` value (so the
fingerprint short-circuit does not fire).

| n_records | bundles | seconds | per_record_us |
| ---: | ---: | ---: | ---: |
| 100 | 2 | 0.001037 | 10.37 |
| 1000 | 2 | 0.010309 | 10.31 |
| 10000 | 2 | 0.106563 | 10.66 |
| 50000 | 2 | 0.549401 | 10.99 |

---

# benchmark: merge_bundles
machine: Python 3.14.4 on Darwin arm64 (arm)

Each row merges `bundles` copies of the same bundle of
`n_records` records, with `prefer='last'`. Same-id records
from different bundles go through the conflict-resolution
path; identical records do not generate a conflict.

| n_records | bundles | seconds | per_record_us |
| ---: | ---: | ---: | ---: |
| 100 | 2 | 0.000799 | 7.99 |
| 1000 | 2 | 0.008053 | 8.05 |
| 10000 | 2 | 0.084535 | 8.45 |
| 100 | 3 | 0.001187 | 11.87 |
| 1000 | 3 | 0.011899 | 11.90 |
| 10000 | 3 | 0.127912 | 12.79 |
