# Canonicalization v1

Canonicalization v1 is the byte contract used by Agent Memory Contracts for
content-derived ids, bundle fingerprints, and runtime payload comparison.

The rule is deliberately small:

```python
json.dumps(
    value,
    sort_keys=True,
    separators=(",", ":"),
    ensure_ascii=False,
)
```

The resulting text is encoded as UTF-8 before hashing with SHA-256.

## Compatibility

This document records existing behavior. It does not change any valid v1 id
bytes, bundle fingerprints, or runtime payload bytes.

Changing this rule for valid records is a v2 event. v1.3.x changes may
centralize implementation, add wrappers, or add tests, but must preserve the
golden vectors below.

## Scope

Canonicalization v1 applies to:

- evidence ids: `src_*`, `ep_*`, `span_*`;
- candidate ids: `cand_*`;
- ledger ids: `fact_*`, `pref_*`, `dec_*`, `redmem_*`;
- taste ids: `taste_*`, `redtaste_*`;
- state ids: `projstate_*`, `corestate_*`, `redstate_*`;
- ContextPack ids: `ctx_*`, `ctxreceipt_*`, `ctxval_*`;
- conflict resolution, hygiene, and audit ids:
  `confres_*`, `hygiene_*`, `audit_*`;
- `record_fingerprint(record)`;
- `bundle_fingerprint(records)`;
- runtime `canonical_json(payload)` comparisons.

Audit-anchor scope text deliberately preserves its legacy JSON serialization.
The anchor fingerprint still uses `bundle_fingerprint(...)`; the stored `scope`
column is a replay descriptor and is not part of canonicalization v1.

Runtime grounding also derives internal `task_*`, `ans_*`, and synthetic
`redstate_*` identifiers through runtime canonical JSON. Those are runtime
implementation identities, not public conformance vectors in this document.

## Non-Goals

- No RFC 8785 claim.
- No numeric normalization beyond Python's standard `json.dumps` behavior.
- No portable guarantee for non-finite floats (`NaN`, `Infinity`,
  `-Infinity`); valid portable v1 values should use finite numbers.
- No change to default duplicate handling in bundle fingerprints.
- No new canonicalization rule; public wrappers and duplicate modes must
  delegate to the same v1 byte contract.

## Golden Vectors

| Vector | Value |
| --- | --- |
| `canonical_nested` | `{"a":null,"z":["é",{"a":1,"b":2}]}` |
| `canonical_edge` | `{"a":1,"escaped":"quote \" backslash \\ newline \n tab \t","exponent_large":1e+20,"exponent_small":1e-06,"float":1.0,"negative_zero":-0.0,"é":"café","Ω":["μ",{"z":0}]}` |
| `canonical_edge_sha256` | `327c497ef1950350a7c0467ad98bcd9e21f6be82b7a9e6344ec09b73d39d6f28` |
| `source` | `src_30af96ba2dc69b1ea84254cd` |
| `episode` | `ep_b786eff487ad8e7f691d5fae` |
| `span` | `span_9c210ceb7b234d35edf94125` |
| `candidate` | `cand_claim_725cfcb9582dddd1ba504173` |
| `ledger` | `fact_4aca254da02ca9e14260ea02` |
| `reducer` | `redmem_94078c3540e896c7e696fc16` |
| `taste` | `taste_44b50d267467dd6660e255da` |
| `taste_reducer` | `redtaste_3777156bea41cc9c027f485f` |
| `project_state` | `projstate_cdb3396d246eaf6147c20768` |
| `core_state` | `corestate_cb737bd33006de3caab104e9` |
| `state_reducer` | `redstate_3b611a591dbcc2801a39575c` |
| `context_pack` | `ctx_7746e9705747c97354898acc` |
| `context_receipt` | `ctxreceipt_dce65501cdf595c4a08f0e67` |
| `context_report` | `ctxval_41d8059843e12edeb790438a` |
| `conflict_resolution` | `confres_90dd8c3927f2bc13abdb6757` |
| `hygiene_report` | `hygiene_de539c8fe7709c2b01a5491f` |
| `audit_pack` | `audit_cb4600493f45712458700420` |
| `bundle` | `e938e4a610b17645d766f22411e3d90e204c00110fba6f58f25ba2298d6a384e` |
