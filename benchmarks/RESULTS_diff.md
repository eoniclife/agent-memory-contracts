# benchmark: bundle_diff
machine: Python 3.14.4 on Darwin arm64 (arm)

Each row diffs a pair of bundles of the same size, with 10%
of records carrying a different `note` value (so the
fingerprint short-circuit does not fire).

| n_records | bundles | seconds | per_record_us |
| ---: | ---: | ---: | ---: |
| 100 | 2 | 0.001020 | 10.20 |
| 1000 | 2 | 0.010192 | 10.19 |
| 10000 | 2 | 0.106512 | 10.65 |
| 50000 | 2 | 0.567260 | 11.35 |
