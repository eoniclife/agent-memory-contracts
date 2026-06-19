# Sprint Review Packet: Machine-Readable Access Decisions

## Scope

Base: `24d6d0742f96a035a543bddb767f2567d5a0c27d`

Branch: `codex/amc-v130-access-decision-codes`

This sprint makes access-control results usable by product control planes
without parsing human-readable English strings.

Changes:

- add additive fields to `AccessDecision`:
  - `reason_code`;
  - `privacy_class`;
  - `max_privacy_class`;
  - `record_type`;
  - `allowed_record_types`;
- preserve the existing constructor shape by giving every new field a default;
- keep new metadata fields out of dataclass equality/hash comparisons so
  existing expected `AccessDecision(record_id, action, reason)` values still
  compare equal to structured `check_access` results;
- keep `record_id`, `action`, and human-readable `reason` unchanged;
- add `AccessSummary.by_reason_code`;
- make `summarize_access` prefer structured decision fields and fall back to
  the legacy `reason` parser for manually constructed old-style decisions;
- preserve `AccessSummary.by_privacy_class` as a privacy-class summary, so
  record-type-only drops are counted in `by_reason_code` but do not introduce
  new privacy-class buckets;
- normalize dict discriminator values to stable record-type names such as
  `fact_ledger_entry` and `candidate_claim`;
- preserve legacy allowlist aliases for candidate and ledger discriminator
  values such as `fact` and `claim`, while continuing to emit canonical
  `record_type` metadata;
- serialize the structured fields from MCP `evaluate_access_scope`;
- use MCP bundle-plane names as the authoritative record type for
  plane-organized input, so payload discriminator fields cannot override the
  plane selected by the caller;
- include structured summary maps in MCP access results.

## Non-Goals

- No new role, user, team, tenant, or policy model.
- No field-level redaction implementation.
- No schema, ID, canonicalization, bundle, runtime, or persistence changes.
- No breaking change to existing `AccessDecision(record_id, action, reason)`
  call sites.

## Compatibility Contract

Existing callers can continue to:

```python
AccessDecision("id", "allow", "privacy_class=public <= max=internal")
```

The new fields default to:

```python
reason_code="unspecified"
privacy_class=None
max_privacy_class=None
record_type=None
allowed_record_types=None
```

Current `check_access` decisions use stable reason codes:

- `privacy_allowed`
- `privacy_exceeds_scope`
- `record_type_not_allowed`

MCP fail-closed unknown privacy handling uses:

- `unknown_privacy_class`

Human-readable `reason` remains present for logs and UI copy, but programmatic
branching should use `action` and `reason_code`.

`AccessSummary.by_reason_code` is also excluded from dataclass equality
comparisons so existing six-field expected summaries remain compatible with
new structured summaries.

Stable `record_type` metadata uses schema-style names: for example
`ledger_type="fact"` becomes `fact_ledger_entry`, and
`candidate_type="claim"` becomes `candidate_claim`. MCP access evaluation also
uses the bundle plane as the authoritative type for plane-organized records.
For compatibility, `allowed_record_types` accepts both those canonical names and
legacy discriminator aliases such as `fact`, `preference`, `decision`, `claim`,
`task`, and `taste_signal`; emitted decision metadata remains canonical.

Record-type-only drops are intentionally excluded from
`AccessSummary.by_privacy_class`. They are surfaced in
`AccessSummary.by_reason_code["record_type_not_allowed"]` instead, preserving
the legacy meaning of the privacy-class summary.

## Local Gates

Run before PR:

```bash
PYTHONPATH=src python3 -m pytest -q tests/test_access.py tests/test_integrations_mcp.py
PYTHONPATH=src python3 -m pytest -q
/tmp/amc-release-check-venv/bin/python -m mypy src/agent_memory_contracts/access.py src/agent_memory_contracts/integrations/mcp.py src/agent_memory_contracts/__init__.py
/tmp/amc-release-check-venv/bin/python -m mypy src/agent_memory_contracts
PYTHONPATH=src python3 scripts/audit_public_api.py
PYTHONPATH=src python3 -m compileall -q src
git diff --check
/tmp/amc-release-check-venv/bin/python scripts/check_release_artifacts.py --run-sdist-tests --run-examples
```

## Reviewer Questions

1. Are the reason-code strings sufficiently narrow and stable?
2. Does adding dataclass fields with defaults preserve practical public API
   compatibility for existing constructors?
3. Should MCP include the structured summary maps now, or keep only per-decision
   fields until a product UI consumes them?
4. Are there any remaining access surfaces that still require string parsing?
