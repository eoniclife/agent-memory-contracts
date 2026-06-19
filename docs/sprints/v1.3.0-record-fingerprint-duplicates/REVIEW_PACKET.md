# Sprint Review Packet: Record Fingerprints and Duplicate Modes

## Scope

Base: `f13bc1f5ba3cd44a0d200d7be179dc5df9a7b07b`

Branch: `codex/amc-v130-record-fingerprint-duplicates`

This sprint separates semantic identity from full-record payload equality for
bundle import/reconciliation workflows.

Changes:

- add public `record_fingerprint(record)`;
- add public `DuplicateRecordError` with `id_field`, `id_value`,
  `first_fingerprint`, `duplicate_fingerprint`, and `same_content`;
- add keyword-only `duplicate_mode` to `bundle_fingerprint`, `bundle_diff`, and
  `merge_bundles`;
- preserve legacy `duplicate_mode="last"` defaults;
- add CLI `--duplicate-mode {last,first,raise}` to `fingerprint`, `diff`, and
  `merge`;
- update stability docs, canonicalization notes, README, changelog, and tests.

## Non-Goals

- No v2 id/schema semantics.
- No change to canonical JSON v1 bytes.
- No change to default bundle fingerprint bytes.
- No product control plane, queue, adapter framework, or hosted service.
- No change to cross-bundle `prefer` conflict semantics in `merge_bundles`.

## Compatibility Contract

Legacy behavior remains the default:

- `bundle_fingerprint(records)` still applies last-write-wins for repeated
  `id_field` values.
- `bundle_diff(a, b)` still treats both inputs as last-write-wins sets by
  default.
- `merge_bundles(*bundles)` still reports intra-bundle duplicates in
  `duplicate_ids` and keeps the last same-bundle occurrence by default.

New opt-in modes:

- `duplicate_mode="last"`: legacy default.
- `duplicate_mode="first"`: keep the first same-bundle occurrence.
- `duplicate_mode="raise"`: raise `DuplicateRecordError` on any repeated
  semantic id, including identical replays. The error exposes whether the two
  payload fingerprints were equal through `same_content`.

## Local Gates

Passed:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_bundles.py tests/test_bundle_diff.py tests/test_merge.py tests/test_cli.py tests/test_cli_json.py tests/test_cli_merge.py
PYTHONPATH=src python3 -m pytest -q
PYTHONPATH=src python3 scripts/audit_public_api.py
PYTHONPATH=src mypy src/agent_memory_contracts/bundles.py src/agent_memory_contracts/bundle_diff.py src/agent_memory_contracts/merge.py src/agent_memory_contracts/__main__.py src/agent_memory_contracts/__init__.py
python -m mypy src/agent_memory_contracts  # temp venv with .[dev,jsonschema,langchain,mcp]
PYTHONPATH=src python3 -m compileall -q src
python scripts/check_release_artifacts.py --run-sdist-tests --run-examples  # temp venv
git diff --check
```

Notes:

- `scripts/audit_public_api.py` exited 0 and printed the repo's pre-existing
  STABILITY-vs-`__all__` warnings.
- Full pytest passed with expected skips for optional integrations and the
  absent Arthashila dataset.
- The release-artifact check passed after installing build/twine tooling inside
  a temporary venv; it built sdist/wheel, ran sdist tests with extras, and ran
  packaged examples.

## Reviewer Questions

1. Is `duplicate_mode="raise"` correctly strict on any repeated id, or should
   it only reject divergent same-id payloads?
2. Is `DuplicateRecordError` the right public shape, or should payload
   fingerprints be returned through a structured result type instead?
3. Does adding `--duplicate-mode` to CLI outputs create any compatibility issue,
   especially the additive `duplicate_mode` key in merge JSON output?
4. Are there any remaining bundle surfaces that should accept the same mode
   before this lands in v1.3.0?
