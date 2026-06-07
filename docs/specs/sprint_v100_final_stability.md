# Sprint v1.0.0-final spec: stability commitment

**Status:** applied per the user's "best judgment" mandate.
Decisions captured inline.

**Branching decision:** staying on `main`.

---

## Problem

The library has reached feature-completeness for the v1.0.0
commitment:

- **v0.7.0** — conflict resolution + memory hygiene (Sprint 21)
- **v0.8.0** — citation graph + provenance traversal (Sprint 22)
- **v0.9.0** — access control + bundle scope (Sprint 23)
- **v1.0.0-alpha.1** — embedding input + text rendering (Sprint 24a)
- **v1.0.0-alpha.2** — schema migration framework (Sprint 24b)
- **v1.0.0-alpha.3** — ContextPack compiler (Sprint 24c)
- **v1.0.0-alpha.4** — end-to-end company brain demo (Sprint 24d)

501 tests pass. mypy --strict is clean on 27 source files.
Zero runtime dependencies. All schemas at "1.0.0".

What's missing for the v1.0.0 final release is **not code
but a commitment**: a SemVer policy, a public API freeze,
a CHANGELOG discipline, and an audit trail of the public
surface.

This sprint is the meta-sprint. It ships:
1. A `docs/STABILITY.md` document that codifies the
   library's SemVer policy and the public API surface.
2. An audit script that walks the public surface and
   verifies the freeze.
3. A CHANGELOG discipline section that documents the
   rules for future releases.
4. The version bump from `1.0.0a4` to `1.0.0`.

No new library code. No new tests (the audit script
counts). This is the "we're done with the library feature
work for v1.0.0" sprint.

---

## What's in this sprint

### 1. `docs/STABILITY.md` (new file)

A 200-400 line document that codifies:

- **The public API surface.** Every name exported from
  `agent_memory_contracts.__init__` is listed, with
  the module it lives in and a one-line description.
  This is the freeze.
- **The SemVer policy.** After v1.0.0:
  - Patch (1.0.x): bug fixes only. No new features.
  - Minor (1.x.0): new features; backwards compatible.
  - Major (x.0.0): breaking changes allowed.
  - Pre-release (1.0.0aN, 1.0.0bN, 1.0.0rcN): not
    stable; APIs may change.
- **The CHANGELOG discipline.** Every release has a
  section in `CHANGELOG.md` with: Added, Changed,
  Removed, Fixed. PRs that change the public API must
  include a CHANGELOG entry.
- **The schema policy.** The 23 (now 24) JSON Schemas
  in `src/agent_memory_contracts/schemas/` are at
  `"1.0.0"`. A schema change requires:
  1. A schema migration step registered in
     `default_migrator()`.
  2. A CHANGELOG entry.
  3. A bump in the schema's `SCHEMA_VERSION`
     constant.
  4. A schema-migration test in `tests/test_migrations.py`.
- **The deprecation policy.** A name can be deprecated
  but not removed before one minor release has passed
  since the deprecation. The deprecation warning
  must be visible at import time.

### 2. `scripts/audit_public_api.py` (new file)

A small script that:
- Walks `agent_memory_contracts.__init__`
- Lists every name in `__all__`
- Verifies the audit matches `docs/STABILITY.md`
- Exits 0 if they match, 1 if they don't (CI gate)

This is the "did anyone add a public name without
updating the stability doc" check.

### 3. CHANGELOG discipline section

A short section at the top of `CHANGELOG.md` that
documents the rules:
- Every release has Added, Changed, Removed, Fixed.
- Breaking changes go in their own "BREAKING" section.
- The first 1.0.0 release is the commit point;
  everything after is SemVer.

### 4. Version bump

`pyproject.toml` version: `1.0.0a4` → `1.0.0`.
`__init__.py.__version__`: `"1.0.0a4"` → `"1.0.0"`.
Git tag: `v1.0.0`.
GitHub Release: titled "v1.0.0", with notes from the
CHANGELOG.

---

## What's NOT in this sprint

- **No new library code.** The library is feature-
  complete for v1.0.0; this is the meta-sprint.
- **No schema changes.** The 24 JSON Schemas stay at
  `"1.0.0"`.
- **No new tests.** The audit script is run, but it's
  a script not a test (so the test count stays at
  501).
- **No PyPI release.** Per the user's standing
  instruction ("not pypi just yet"), no GitHub
  Release is created; the tag is on `main` but the
  publish workflow doesn't auto-fire.

---

## Decisions applied to this sprint

Applied 2026-06-07 per the user's "best judgment" mandate.

### 9 small decisions (all defaults)

1. **Stability doc location:** `docs/STABILITY.md`
   (next to `architecture.md` and `migration.md`).
2. **Public API list source:** `__all__` in
   `agent_memory_contracts/__init__.py`. The audit
   walks `__all__`, not the runtime `dir()` (which
   includes private internals).
3. **Audit script location:** `scripts/audit_public_api.py`.
4. **CI gate:** the audit script exits non-zero on
   mismatch; the CI workflow runs it on every push.
5. **SemVer policy:** standard (patch = bug fixes,
   minor = new features, major = breaking).
6. **Pre-release tags:** `1.0.0aN` (alpha),
   `1.0.0bN` (beta), `1.0.0rcN` (release candidate).
7. **Deprecation policy:** one minor release between
   deprecation and removal; warning at import time.
8. **Schema policy:** the four-step process above
   (migration step, CHANGELOG, version bump, test).
9. **CHANGELOG format:** the existing
   Keep-a-Changelog format. No change.

### 3 bigger-question decisions (all defaults)

- **The public API is frozen at v1.0.0.** Every
  name in `__all__` is locked. Adding a new public
  name in v1.0.x is a minor release (1.1.0);
  removing a public name in v1.0.x is forbidden
  (would require v2.0.0).
- **The schemas are at "1.0.0".** The migration
  framework (v1.0.0a2) is the safety net for future
  changes; the v1.0.0 commit is the freeze. v1.1.0
  may add a new field (with a migration step); v2.0.0
  may rename or remove fields.
- **No GitHub Release / no PyPI publish yet.** The
  user's standing instruction. The tag is on `main`
  but the publish workflow doesn't auto-fire. When
  the user gives the word, the publish workflow
  fires via `gh release create` or `workflow_dispatch`.

### Minor implementation choices

- **The audit script is ~50 LOC.** It imports
  `agent_memory_contracts`, walks `__all__`, and
  checks each name against the STABILITY doc.
- **The STABILITY doc is human-readable Markdown.**
  No machine-readable manifest; the audit script
  parses the doc with a simple regex.
- **The CHANGELOG discipline section is short**
  (~30 lines). It documents the rules; the actual
  CHANGELOG entries follow the existing format.
- **The version bump is the only "code change" in
  this sprint.** Two lines: `pyproject.toml` and
  `__init__.py.__version__`.
