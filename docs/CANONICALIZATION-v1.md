# Canonicalization v1

Canonicalization v1 is the byte contract used by Agent Memory Contracts for
content-derived ids, bundle fingerprints, runtime payload comparison, and audit
anchor scope serialization.

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
bytes, bundle fingerprints, runtime payload bytes, or gate-built audit anchor
scope bytes.

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
- `bundle_fingerprint(records)`;
- runtime `canonical_json(payload)` comparisons;
- audit anchor scope serialization for `make_scope(...)` scopes.

## Non-Goals

- No RFC 8785 claim.
- No numeric normalization beyond Python's standard `json.dumps` behavior.
- No change to duplicate handling in bundle fingerprints.
- No new public API in this release; existing helper names delegate to one
  private implementation.

## Golden Vectors

| Vector | Value |
| --- | --- |
| `canonical_nested` | `{"a":null,"z":["é",{"a":1,"b":2}]}` |
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
| `bundle` | `e938e4a610b17645d766f22411e3d90e204c00110fba6f58f25ba2298d6a384e` |
