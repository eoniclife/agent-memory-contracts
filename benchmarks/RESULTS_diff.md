# benchmark: bundle_diff
machine: Python 3.14.4 on Darwin arm64 (arm)

Each row diffs a pair of bundles of the same size, with 10%
of records carrying a different `note` value (so the
fingerprint short-circuit does not fire).

| n_records | bundles | seconds | per_record_us |
| ---: | ---: | ---: | ---: |
| 100 | 2 | 0.001052 | 10.52 |
| 1000 | 2 | 0.010464 | 10.46 |
| 10000 | 2 | 0.108690 | 10.87 |
| 50000 | 2 | 0.567253 | 11.35 |
