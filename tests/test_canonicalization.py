"""Golden vectors for canonicalization v1."""

from __future__ import annotations

from agent_memory_contracts import (
    bundle_fingerprint,
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
)
from agent_memory_contracts._canonical import (
    CANONICALIZATION_VERSION,
    canonical_json,
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
