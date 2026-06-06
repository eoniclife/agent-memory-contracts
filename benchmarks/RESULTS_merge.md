# benchmark: merge_bundles
machine: Python 3.14.4 on Darwin arm64 (arm)

Each row merges `bundles` copies of the same bundle of
`n_records` records, with `prefer='last'`. Same-id records
from different bundles go through the conflict-resolution
path; identical records do not generate a conflict.

| n_records | bundles | seconds | per_record_us |
| ---: | ---: | ---: | ---: |
| 100 | 2 | 0.000792 | 7.92 |
| 1000 | 2 | 0.008077 | 8.08 |
| 10000 | 2 | 0.085880 | 8.59 |
| 100 | 3 | 0.001211 | 12.11 |
| 1000 | 3 | 0.012144 | 12.14 |
| 10000 | 3 | 0.124458 | 12.45 |
