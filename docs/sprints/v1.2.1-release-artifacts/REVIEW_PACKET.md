# v1.2.1 Release Artifact Hardening Review Packet

Date: 2026-06-19

## Objective

Close the v1.2.0 release-artifact gap: an external reviewer should be able to
download the wheel or source distribution and verify the same public surface
that the repository README describes.

This sprint is intentionally limited to release reproducibility. It does not
change memory semantics, reducer behavior, schema versions, IDs, or runtime
contracts.

## Scope

Changed surfaces:

- `MANIFEST.in`: explicit source-distribution inclusion and generated-file
  pruning.
- `pyproject.toml`: dev extras now include release tooling; setuptools honors
  included package data.
- `scripts/check_release_artifacts.py`: reusable verifier for wheel/sdist
  contents, README local links, generated-file leaks, sdist-built wheel install
  smoke, and optional sdist test/example execution.
- `.github/workflows/ci.yml`: adds a visible `release-artifacts` job.
- `.github/workflows/publish.yml`: publish now reuses the same verifier before
  uploading artifacts.
- `.gitignore`: keeps local strategy/review scratchpads out of public release
  artifacts.

## Non-Goals

- No package version bump in this branch. The version should move only at the
  release-prep gate.
- No public API changes.
- No changes to schema canonicalization, semantic IDs, duplicate-ID policy,
  migration semantics, MCP tool semantics, supersession validation, LangChain
  behavior, or access-control models.
- No inclusion of private/local transcript paths in public artifacts.

## Artifact Gates

The verifier checks:

1. Source distribution includes docs, examples, benchmarks, scripts, tests,
   invariants, runtime modules, integration modules, and schema JSON.
2. Wheel includes the complete installable package surface from
   `src/agent_memory_contracts`, no unexpected top-level payloads, `py.typed`,
   and the exact schema filename set.
3. README local links resolve inside the source distribution.
4. Generated/local files such as `__pycache__`, `.pyc`, `.DS_Store`,
   `.venv`, `.mavis`, demo `out/`, and `docs/GPT_PRO_FEEDBACK_WORKPLAN.md`
   do not leak into artifacts.
5. Wheel is built through the generated sdist, installed in a fresh virtual
   environment, imported, and exercised through both module and console-script
   CLI entry points.
6. Optional full gate: extracted sdist installs in normal (non-editable) mode
   with dev/optional extras, runs the full test suite without an inherited
   `PYTHONPATH`, then runs the selected CI example scripts against the installed
   distribution.

## Local Verification

Passed locally on this branch:

```bash
.venv-release/bin/python scripts/check_release_artifacts.py
.venv-release/bin/python scripts/check_release_artifacts.py --run-sdist-tests --run-examples
PYTHONPATH=src .venv/bin/python -m pytest -q
PYTHONPATH=src .venv/bin/python -m mypy src/agent_memory_contracts
git diff --check
```

Results:

- Fast artifact gate passed: built the wheel through the generated sdist,
  `twine check` passed, wheel installed in a fresh virtualenv, package
  imported, and both CLI `--version` entry points returned
  `agent-memory-contracts 1.2.0`.
- Full artifact gate passed: extracted source distribution installed in normal
  (non-editable) mode with `[dev,jsonschema,langchain,mcp]`, full pytest suite
  passed without a verifier-injected `PYTHONPATH`, and the selected CI example
  scripts ran from outside the extracted source tree.
- Final artifact member counts: sdist `186`, wheel `67`.
- Repository pytest passed with the expected Arthashila dataset skips and the
  jsonschema-present skip.
- `mypy` passed: `Success: no issues found in 37 source files`.
- `git diff --check` passed.

Observed non-blocking warning:

- Setuptools warns that the current `project.license = { text = ... }` table
  style and license classifier are deprecated and should move to SPDX-style
  license metadata before 2027-02-18. This predates the artifact fix and should
  be handled as a follow-up release-hygiene item.

## Review Questions

1. Does the source distribution now contain every README-linked local path and
   every file needed to run tests from the extracted artifact?
2. Does the wheel contain only the installable package surface expected for a
   library distribution?
3. Are CI and publish using the same artifact verifier, or can they still
   drift?
4. Did the patch accidentally alter runtime or schema behavior?
5. Is any local/private planning material able to leak into public artifacts?

## Next Sprint Candidates

After this release-artifact gate passes, the next patch-sized candidates are:

- mixed-version migration order independence;
- MCP tool naming and server-enforced scope safety;
- supersession cycle rejection and defensive query helpers;
- LangChain docs/config correction.
