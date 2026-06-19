"""Golden vectors for canonicalization v1."""

from __future__ import annotations

from agent_memory_contracts import (
    ConflictResolution,
    bundle_fingerprint,
    compute_audit_pack,
    compute_hygiene_report,
    make_candidate_id,
    make_context_pack_build_receipt_id,
    make_context_pack_id,
    make_context_pack_validation_report_id,
    make_core_state_id,
    make_episode_id,
    make_ledger_entry_id,
    make_project_state_id,
    make_reducer_decision_id,
    make_source_id,
    make_span_id,
    make_state_reducer_decision_id,
    make_taste_card_id,
    make_taste_reducer_decision_id,
    record_fingerprint,
)
from agent_memory_contracts._canonical import (
    CANONICALIZATION_VERSION,
    canonical_json,
    sha256_hex,
)
from agent_memory_contracts.candidate_ids import canonical_payload as candidate_canonical
from agent_memory_contracts.contextpack_ids import canonical_payload as contextpack_canonical
from agent_memory_contracts.evidence_ids import _canonical_json as evidence_canonical
from agent_memory_contracts.ledger_ids import canonical_payload as ledger_canonical
from agent_memory_contracts.runtime.store import canonical_json as runtime_canonical
from agent_memory_contracts.state_ids import canonical_payload as state_canonical
from agent_memory_contracts.taste_ids import canonical_payload as taste_canonical


NESTED_VALUE = {"z": ["é", {"b": 2, "a": 1}], "a": None}
NESTED_CANONICAL = '{"a":null,"z":["é",{"a":1,"b":2}]}'
EDGE_VALUE = {
    "a": 1,
    "float": 1.0,
    "negative_zero": -0.0,
    "exponent_small": 1e-6,
    "exponent_large": 1e20,
    "escaped": 'quote " backslash \\ newline \n tab \t',
    "é": "café",
    "Ω": ["μ", {"z": 0}],
}
EDGE_CANONICAL = (
    '{"a":1,"escaped":"quote \\" backslash \\\\ newline \\n tab \\t",'
    '"exponent_large":1e+20,"exponent_small":1e-06,"float":1.0,'
    '"negative_zero":-0.0,"é":"café","Ω":["μ",{"z":0}]}'
)
EDGE_SHA256 = (
    "327c497ef1950350a7c0467ad98bcd9e21f6be82b7a9e6344ec09b73d39d6f28"
)


def test_canonical_json_v1_bytes_are_stable() -> None:
    assert CANONICALIZATION_VERSION == "canonical-json-v1"
    assert canonical_json(NESTED_VALUE) == NESTED_CANONICAL
    assert evidence_canonical(NESTED_VALUE) == NESTED_CANONICAL
    assert candidate_canonical(NESTED_VALUE) == NESTED_CANONICAL
    assert ledger_canonical(NESTED_VALUE) == NESTED_CANONICAL
    assert taste_canonical(NESTED_VALUE) == NESTED_CANONICAL
    assert state_canonical(NESTED_VALUE) == NESTED_CANONICAL
    assert contextpack_canonical(NESTED_VALUE) == NESTED_CANONICAL
    assert runtime_canonical(NESTED_VALUE) == NESTED_CANONICAL


def test_canonical_json_v1_edge_bytes_are_stable() -> None:
    assert canonical_json(EDGE_VALUE) == EDGE_CANONICAL
    assert sha256_hex(canonical_json(EDGE_VALUE)) == EDGE_SHA256
    assert record_fingerprint(EDGE_VALUE) == EDGE_SHA256


def test_id_golden_vectors_are_unchanged() -> None:
    assert (
        make_source_id(
            "chatgpt_conversation",
            "https://example.com/transcript",
            "a" * 64,
        )
        == "src_30af96ba2dc69b1ea84254cd"
    )
    assert (
        make_episode_id("src_" + "1" * 24, "turn", "line_range", "1-5")
        == "ep_b786eff487ad8e7f691d5fae"
    )
    assert (
        make_span_id("src_" + "1" * 24, "line_range", "1-5")
        == "span_9c210ceb7b234d35edf94125"
    )
    assert (
        make_candidate_id(
            "claim",
            ["span_b", "span_a"],
            {"object": "1%", "predicate": "is", "subject": "slo"},
        )
        == "cand_claim_725cfcb9582dddd1ba504173"
    )
    assert (
        make_ledger_entry_id(
            "fact",
            ["span_b", "span_a"],
            {
                "ledger_type": "fact",
                "subject": "slo",
                "predicate": "is",
                "object": "1%",
                "scope": "project",
                "valid_from": "2026-06-01T00:00:00Z",
                "evidence_span_ids": ["span_a", "span_b"],
            },
        )
        == "fact_4aca254da02ca9e14260ea02"
    )
    assert (
        make_reducer_decision_id(
            "promote",
            ["cand_b", "cand_a"],
            ["fact_b", "fact_a"],
            ["span_b", "span_a"],
            "ok",
        )
        == "redmem_94078c3540e896c7e696fc16"
    )
    assert (
        make_taste_card_id(
            ["span_b", "span_a"],
            {
                "subject": "memory",
                "principle": "prefer receipts",
                "evidence_span_ids": ["span_a", "span_b"],
            },
        )
        == "taste_44b50d267467dd6660e255da"
    )
    assert (
        make_taste_reducer_decision_id(
            "promote",
            ["cand_taste_b", "cand_taste_a"],
            ["taste_b", "taste_a"],
            ["span_b", "span_a"],
            "ok",
        )
        == "redtaste_3777156bea41cc9c027f485f"
    )
    assert (
        make_project_state_id(
            "p1",
            "2026-06-01T00:00:00Z",
            ["span_b", "span_a"],
            {"summary": "x", "evidence_span_ids": ["span_a", "span_b"]},
        )
        == "projstate_cdb3396d246eaf6147c20768"
    )
    assert (
        make_core_state_id(
            "Aditya",
            "2026-06-01T00:00:00Z",
            ["span_b", "span_a"],
            {"summary": "x", "evidence_span_ids": ["span_a", "span_b"]},
        )
        == "corestate_cb737bd33006de3caab104e9"
    )
    assert (
        make_state_reducer_decision_id(
            "promote",
            ["projstate_b", "projstate_a"],
            ["corestate_b", "corestate_a"],
            ["span_b", "span_a"],
            ["fact_b", "fact_a"],
            ["taste_b", "taste_a"],
            "ok",
        )
        == "redstate_3b611a591dbcc2801a39575c"
    )
    assert (
        make_context_pack_id(
            "task-1",
            ["span_b", "span_a"],
            {"task_type": "answer", "evidence_span_ids": ["span_a", "span_b"]},
        )
        == "ctx_7746e9705747c97354898acc"
    )
    assert (
        make_context_pack_build_receipt_id(
            "ctx_" + "1" * 24,
            {"task": "task-1", "records": ["b", "a"]},
            {"max_chars": 1000, "order": ["b", "a"]},
        )
        == "ctxreceipt_dce65501cdf595c4a08f0e67"
    )
    assert (
        make_context_pack_validation_report_id(
            "ctx_" + "1" * 24,
            "2026-06-01T00:00:00Z",
            "pass",
            {"grounded": True},
            [],
        )
        == "ctxval_41d8059843e12edeb790438a"
    )


def test_public_report_id_golden_vectors_are_unchanged() -> None:
    resolution = ConflictResolution.from_dict({
        "conflict_id": "pref_conflict_demo",
        "chosen_version_index": 0,
        "chosen_record": {
            "id": "pref_demo",
            "ledger_type": "preference",
            "preference_text": "Use canonical vectors",
        },
        "rejected_record_ids": ["pref_demo_old"],
        "resolved_by": "codex",
        "resolved_at": "2026-06-19T00:00:00Z",
        "rationale": "Golden vector resolution for canonicalization v1.",
        "superseded_at": None,
        "metadata": {"source": "canonicalization-test"},
    })
    assert resolution.id == "confres_90dd8c3927f2bc13abdb6757"

    hygiene_pref = {
        "id": "pref_demo",
        "schema_version": "1.0.0",
        "ledger_type": "preference",
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "subject": "memory architecture",
        "preference_text": "test pref_demo",
        "domain": "architecture",
        "strength": "hard_constraint",
        "valid_from": "2026-06-01T00:00:00Z",
        "valid_until": "2026-12-01T00:00:00Z",
        "stale_after": "2026-07-01T00:00:00Z",
        "evidence_span_ids": ["span_demo"],
        "privacy_class": "internal",
        "metadata": {},
    }
    hygiene_span = {
        "id": "span_demo",
        "schema_version": "1.0.0",
        "source_id": "src_demo",
        "locator": {"kind": "line_range", "value": "1-2"},
        "span_hash_sha256": "0" * 64,
        "privacy_class": "internal",
        "metadata": {},
    }
    hygiene = compute_hygiene_report(
        [hygiene_pref, hygiene_span],
        window_start="2026-06-01T00:00:00Z",
        window_end="2026-06-30T00:00:00Z",
        now="2026-06-15T00:00:00Z",
        conflicts={"surfaced": 1, "resolved": 1},
    )
    assert hygiene.id == "hygiene_de539c8fe7709c2b01a5491f"

    audit = compute_audit_pack(
        [
            {
                "id": "src_aaaa",
                "schema_version": "1.0.0",
                "source_type": "manual_note",
                "title": "Rate policy v4",
                "origin_uri": None,
                "raw_ref": {"kind": "local_path", "value": "policies/v4.md"},
                "content_hash_sha256": "a" * 64,
                "captured_at": "2026-05-18T00:00:00Z",
                "observed_at": "2026-05-18T00:00:00Z",
                "author_or_sender": "cfo@example.com",
                "participants": [],
                "privacy_class": "internal",
                "custody_status": "external_pointer",
                "parser_version": "v1",
                "metadata": {},
            },
            {
                "id": "span_aaaa",
                "schema_version": "1.0.0",
                "source_id": "src_aaaa",
                "episode_id": None,
                "locator": {"kind": "line_range", "value": "7-7"},
                "text_excerpt": "Grade B rate is 12.75%",
                "excerpt_policy": "short_quote_allowed",
                "span_hash_sha256": "b" * 64,
                "privacy_class": "internal",
                "metadata": {},
            },
            {
                "id": "cand_claim_aaaa",
                "schema_version": "1.0.0",
                "candidate_type": "claim",
                "evidence_span_ids": ["span_aaaa"],
                "claim_text": "Grade B rate is 12.75%",
                "confidence": "high",
                "status": "candidate",
                "metadata": {},
            },
            {
                "id": "redmem_aaaa",
                "schema_version": "1.0.0",
                "decision_type": "promote",
                "target_candidate_ids": ["cand_claim_aaaa"],
                "target_ledger_entry_ids": ["fact_aaaa"],
                "evidence_span_ids": ["span_aaaa"],
                "rationale": "chain verified",
                "decided_by": {
                    "agent": "test-reducer",
                    "model": "deterministic",
                    "tool": None,
                    "prompt_ref": None,
                },
                "decided_at": "2026-06-01T10:00:00Z",
                "confidence": "high",
                "risk_class": "low",
                "checks": {
                    "provenance": "pass",
                    "temporal_validity": "pass",
                    "contradiction_scan": "pass",
                    "privacy": "pass",
                    "usefulness": "pass",
                },
                "metadata": {},
            },
            {
                "id": "fact_aaaa",
                "schema_version": "1.0.0",
                "ledger_type": "fact",
                "status": "active",
                "confidence": "high",
                "scope": "company",
                "subject": "lap.grade_b.rate",
                "predicate": "rate_is",
                "object": "12.75%",
                "fact_text": "Grade B card rate is 12.75% under v4",
                "source_record_ids": ["src_aaaa"],
                "evidence_span_ids": ["span_aaaa"],
                "candidate_ids": ["cand_claim_aaaa"],
                "reducer_decision_id": "redmem_aaaa",
                "valid_from": "2026-05-18T00:00:00Z",
                "valid_until": None,
                "stale_after": None,
                "supersedes": [],
                "superseded_by": [],
                "metadata": {},
            },
        ],
        as_of="2026-06-15T12:00:00Z",
    )
    assert audit.id == "audit_cb4600493f45712458700420"


def test_bundle_fingerprint_golden_vector_is_unchanged() -> None:
    expected = (
        "e938e4a610b17645d766f22411e3d90e204c00110fba6f58f25ba2298d6a384e"
    )
    assert bundle_fingerprint([
        {"id": "b", "value": 2},
        {"id": "a", "value": "é"},
    ]) == expected
    assert bundle_fingerprint([
        {"id": "a", "value": "é"},
        {"id": "b", "value": 2},
    ]) == expected
