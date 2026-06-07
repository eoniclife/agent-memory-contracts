"""Worked example for the ContextPack compiler.

Run from the repository root::

    PYTHONPATH=src python examples/compilation.py

Demonstrates the headline use cases of the ContextPack
compiler: take a bundle of trusted memories, a task
description, an optional access scope, and produce a
task-ready ContextPack with a BuildReceipt and
ValidationReport attached.

The example does NOT call an embedding model or store
vectors. The library stops at the pack boundary; the
product owns the embedding model and the vector DB.

.. versionadded:: 1.0.0-alpha.3
"""

from __future__ import annotations

import sys
from typing import Any

from agent_memory_contracts import (
    CompilationPolicy,
    ContextPackTask,
    compile_context_pack,
    public_scope,
    team_scope,
)


T_CAPTURED = "2026-06-06T12:00:00Z"
T_DECIDED = "2026-06-06T13:00:00Z"


def _src_dict(rid: str, privacy: str = "internal") -> dict[str, Any]:
    return {
        "id": rid,
        "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": f"Source {rid[-4:]}",
        "origin_uri": f"https://example.com/{rid[-4:]}",
        "raw_ref": {"kind": "external_uri", "value": f"https://example.com/{rid[-4:]}"},
        "content_hash_sha256": "a" * 64,
        "captured_at": T_CAPTURED, "observed_at": T_CAPTURED,
        "author_or_sender": None, "participants": [],
        "privacy_class": privacy,
        "custody_status": "synthetic", "parser_version": "v1", "metadata": {},
    }


def _span_dict(span_rid: str, src_rid: str, privacy: str = "internal") -> dict[str, Any]:
    return {
        "id": span_rid,
        "schema_version": "1.0.0",
        "source_id": src_rid, "episode_id": None,
        "locator": {"kind": "line_range", "value": "10-15"},
        "text_excerpt": "We should ship spec-first.",
        "excerpt_policy": "short_quote_allowed",
        "span_hash_sha256": "b" * 64,
        "privacy_class": privacy, "metadata": {},
    }


def _fact_dict(fact_rid: str, src_rid: str, span_rid: str, privacy: str = "internal") -> dict[str, Any]:
    return {
        "id": fact_rid,
        "schema_version": "1.0.0",
        "ledger_type": "fact", "status": "active",
        "confidence": "high", "scope": "global",
        "source_record_ids": [src_rid], "episode_record_ids": [],
        "evidence_span_ids": [span_rid], "candidate_ids": [],
        "reducer_decision_id": "redmem_" + "a" * 24,
        "observed_at": None, "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED, "valid_until": None, "stale_after": None,
        "created_at": T_DECIDED, "updated_at": T_DECIDED,
        "supersedes": [], "superseded_by": [], "metadata": {},
        "subject": "memory", "predicate": "approach", "object": "spec-first",
        "fact_text": "Spec-first beats no spec.",
        "privacy_class": privacy,
    }


def _state_dict() -> dict[str, Any]:
    return {
        "id": "projstate_" + "d" * 24,
        "schema_version": "1.0.0",
        "state_type": "project_state",
        "status": "active", "as_of": T_CAPTURED,
        "summary": "spec-first beats no spec",
        "active_fact_ids": [], "active_preference_ids": [],
        "active_decision_ids": [], "active_taste_card_ids": [],
        "source_record_ids": [], "episode_record_ids": [], "evidence_span_ids": [],
        "reducer_decision_id": "redstate_" + "d" * 21,
        "project_id": "agent-memory-contracts",
        "summary_text": "spec-first beats no spec",
        "supersedes": [], "superseded_by": [], "metadata": {},
    }


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def demo_basic_compilation() -> None:
    """Build a small bundle, compile, print the result."""
    section("1. Basic compilation: 1 source, 1 span, 1 fact, 1 state")
    src = _src_dict("src_" + "a" * 24)
    span = _span_dict("span_" + "a" * 24, src["id"])
    fact = _fact_dict("fact_" + "a" * 24, src["id"], span["id"])
    state = _state_dict()
    bundle = [src, span, fact, state]
    task = ContextPackTask(
        task_id="t1", task_title="what is the spec-first approach?",
        task_type="research", task_summary="User asked about the spec-first approach.",
        project_id="agent-memory-contracts",
        risk_class="low", sensitivity="internal",
    )
    result = compile_context_pack(bundle, task=task)
    print(f"pack_id:           {result.context_pack.id}")
    print(f"pack_hash:         {result.context_pack.pack_hash_sha256[:32]}...")
    print(f"task_type:         {result.context_pack.task['task_type']}")
    print(f"task_sensitivity:  {result.context_pack.task['sensitivity']}")
    print(f"selected records:  {len(result.selected_record_ids)}")
    print(f"  ids: {[rid[:24] + '...' for rid in result.selected_record_ids]}")
    print(f"excluded records:  {len(result.excluded_record_ids)}")
    print(f"  ids: {[rid[:24] + '...' for rid in result.excluded_record_ids]}")
    print(f"validation status: {result.validation_report.status}")
    print(f"build_receipt:     {result.build_receipt.builder['agent']} "
          f"(mode={result.build_receipt.builder['mode']})")


def demo_scope_filtering() -> None:
    """Apply team_scope vs public_scope; print the diff."""
    section("2. Scope filtering: team_scope vs public_scope")
    public_src = _src_dict("src_" + "a" * 24, privacy="public")
    public_span = _span_dict("span_" + "a" * 24, public_src["id"], privacy="public")
    public_fact = _fact_dict("fact_" + "a" * 24, public_src["id"], public_span["id"], privacy="public")
    internal_src = _src_dict("src_" + "b" * 24, privacy="internal")
    internal_span = _span_dict("span_" + "b" * 24, internal_src["id"], privacy="internal")
    internal_fact = _fact_dict("fact_" + "b" * 24, internal_src["id"], internal_span["id"], privacy="internal")
    state = _state_dict()
    bundle = [public_src, public_span, public_fact, internal_src, internal_span, internal_fact, state]
    task = ContextPackTask(
        task_id="t2", task_title="show me the team-relevant memories",
        task_type="research", task_summary="User wants team-shared context.",
        project_id="agent-memory-contracts",
        risk_class="low", sensitivity="internal",
    )
    team_result = compile_context_pack(bundle, task=task, scope=team_scope())
    public_result = compile_context_pack(bundle, task=task, scope=public_scope())
    print(f"team_scope selected:  {len(team_result.selected_record_ids)} records")
    print(f"  ids: {[rid[:24] + '...' for rid in team_result.selected_record_ids]}")
    print(f"public_scope selected: {len(public_result.selected_record_ids)} records")
    print(f"  ids: {[rid[:24] + '...' for rid in public_result.selected_record_ids]}")
    print(f"diff: {len(team_result.selected_record_ids) - len(public_result.selected_record_ids)} records excluded by public_scope")


def demo_source_coverage() -> None:
    """A claim with no source backing is excluded."""
    section("3. Source-coverage enforcement: an unsupported claim is excluded")
    src = _src_dict("src_" + "a" * 24)
    span = _span_dict("span_" + "a" * 24, src["id"])
    fact = _fact_dict("fact_" + "a" * 24, src["id"], span["id"])
    # An unsupported fact: cites a span that's not in the bundle.
    unsupported = _fact_dict("fact_" + "b" * 24, "src_" + "z" * 24, "span_" + "z" * 23)
    state = _state_dict()
    bundle = [src, span, fact, unsupported, state]
    task = ContextPackTask(
        task_id="t3", task_title="what is the spec?",
        task_type="research", task_summary="User asked about the spec.",
        project_id="agent-memory-contracts",
        risk_class="low", sensitivity="internal",
    )
    result = compile_context_pack(bundle, task=task)
    print(f"selected: {[rid[:24] + '...' for rid in result.selected_record_ids]}")
    print(f"excluded:")
    for rid in result.excluded_record_ids:
        print(f"  {rid[:24] + '...':<32} (in BuildReceipt.excluded)")


def main(argv: list[str] | None = None) -> int:
    demo_basic_compilation()
    demo_scope_filtering()
    demo_source_coverage()
    print()
    print("=" * 70)
    print("ContextPack compiler example complete.")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
