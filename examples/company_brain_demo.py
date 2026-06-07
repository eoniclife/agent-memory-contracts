"""End-to-end company brain demo.

Run from the repository root::

    PYTHONPATH=src python examples/company_brain_demo.py

This is the single-script story of the library: the full
pipeline from raw sources to a task-ready ContextPack.
It is what an accelerator partner sees in the 5-minute
demo. The library's public API is exercised end-to-end
in 7 stages:

1. Ingest   — 3 raw sources -> 3 SourceRecord + 5 EvidenceSpan
2. Extract  — Simulated LLM -> 8 candidate claims
3. Reduce   — Promote 5 to trusted ledger, reject 3
4. Cite     — CitationGraph: all promoted claims have source chains
5. Access   — team_scope: 5 of 8 records allowed
6. Embed    — 5 EmbeddingInputs (the input boundary)
7. Compile  — ContextPack for the task, with BuildReceipt + ValidationReport

The demo is deterministic. Two runs produce the same
output (modulo timestamp fields). No LLM calls, no
embedding model calls, no vector storage. The library
stops at the input boundaries; the product owns the rest.

.. versionadded:: 1.0.0-alpha.4
"""

from __future__ import annotations

import sys
from typing import Any

from agent_memory_contracts import (
    CitationGraph,
    ContextPackTask,
    EmbeddingInput,
    record_to_embedding_input,
    scope_bundle,
    summarize_access,
    team_scope,
    compile_context_pack,
)


# ---------------------------------------------------------------------------
# Stage 0: simulated raw sources
# ---------------------------------------------------------------------------

T_CAPTURED = "2026-06-06T12:00:00Z"
T_DECIDED = "2026-06-06T13:00:00Z"
T_EXTRACTED = "2026-06-06T12:30:00Z"

# Three raw sources: a chat, a doc, an email. Each is a
# dict that we'll turn into a SourceRecord + EvidenceSpan
# in stage 1.
RAW_SOURCES = [
    {
        "kind": "chatgpt_conversation",
        "title": "Memory kernel design review",
        "author": "alice@example.com",
        "captured_at": T_CAPTURED,
        "uri": "https://example.com/transcript/42",
        "content_hash": "a" * 64,
    },
    {
        "kind": "web_article",
        "title": "Spec-first beats no spec",
        "author": "bob@example.com",
        "captured_at": T_CAPTURED,
        "uri": "https://example.com/article/spec-first",
        "content_hash": "b" * 64,
    },
    {
        "kind": "email_thread",
        "title": "Re: Memory kernel roadmap",
        "author": "carol@example.com",
        "captured_at": T_CAPTURED,
        "uri": "https://example.com/email/123",
        "content_hash": "c" * 64,
    },
]


# ---------------------------------------------------------------------------
# Stage 1: Ingest (raw source -> SourceRecord + EvidenceSpan)
# ---------------------------------------------------------------------------

# We use the library's public API to construct the records
# via the existing fixture helpers. The demo doesn't
# directly call make_*_id; the public constructors
# (from_dict) derive the ids for us.

def _ingest_source(raw: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Convert a raw source dict into a SourceRecord dict
    plus a list of EvidenceSpan dicts. The id derivation
    uses the library's make_*_id helpers via the
    SourceRecord / EvidenceSpan constructors.
    """
    from agent_memory_contracts import (
        EvidenceSpan, SourceRecord, make_source_id, make_span_id, sha256_hex,
    )
    source_id = make_source_id(raw["kind"], raw["uri"], raw["content_hash"])
    src = SourceRecord.from_dict({
        "id": source_id, "schema_version": "1.0.0",
        "source_type": raw["kind"], "title": raw["title"],
        "origin_uri": raw["uri"],
        "raw_ref": {"kind": "external_uri", "value": raw["uri"]},
        "content_hash_sha256": raw["content_hash"],
        "captured_at": raw["captured_at"], "observed_at": raw["captured_at"],
        "author_or_sender": raw["author"], "participants": [raw["author"]],
        "privacy_class": "internal", "custody_status": "synthetic",
        "parser_version": "v1", "metadata": {},
    })
    # Two evidence spans per source: the title and the
    # first 200 chars of a synthesized body. (The "body"
    # is a placeholder; the demo doesn't parse real text.)
    spans: list[dict[str, Any]] = []
    for i, locator_value in enumerate(["1-1", "2-2"], start=1):
        span_id = make_span_id(source_id, "line_range", locator_value)
        span = EvidenceSpan.from_dict({
            "id": span_id, "schema_version": "1.0.0",
            "source_id": source_id, "episode_id": None,
            "locator": {"kind": "line_range", "value": locator_value},
            "text_excerpt": f"{raw['title']} (chunk {i})",
            "excerpt_policy": "short_quote_allowed",
            "span_hash_sha256": sha256_hex(f"{source_id}{locator_value}"),
            "privacy_class": "internal", "metadata": {},
        })
        spans.append(span.to_dict() if hasattr(span, "to_dict") else {
            "id": span.id, "schema_version": span.schema_version,
            "source_id": span.source_id, "episode_id": span.episode_id,
            "locator": span.locator, "text_excerpt": span.text_excerpt,
            "excerpt_policy": span.excerpt_policy,
            "span_hash_sha256": span.span_hash_sha256,
            "privacy_class": span.privacy_class, "metadata": dict(span.metadata),
        })
    src_dict = {
        "id": src.id, "schema_version": src.schema_version,
        "source_type": src.source_type, "title": src.title,
        "origin_uri": src.origin_uri, "raw_ref": dict(src.raw_ref),
        "content_hash_sha256": src.content_hash_sha256,
        "captured_at": src.captured_at, "observed_at": src.observed_at,
        "author_or_sender": src.author_or_sender,
        "participants": list(src.participants),
        "privacy_class": src.privacy_class,
        "custody_status": src.custody_status,
        "parser_version": src.parser_version, "metadata": dict(src.metadata),
    }
    return src_dict, spans


def stage_1_ingest() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sources: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    for raw in RAW_SOURCES:
        src, src_spans = _ingest_source(raw)
        sources.append(src)
        spans.extend(src_spans)
    return sources, spans


# ---------------------------------------------------------------------------
# Stage 2: Extract (simulated LLM -> CandidateClaim)
# ---------------------------------------------------------------------------

# Simulated LLM output. 8 candidates: 2 facts from each
# of 3 sources, plus 2 candidates that will be rejected
# in stage 3. The candidates are dicts shaped like
# CandidateClaim but constructed without the dataclass
# validator (so the demo doesn't need to compute the
# content-derived id).

SIMULATED_CANDIDATES: list[dict[str, Any]] = [
    {
        "id": "cand_" + "a" * 24,
        "candidate_type": "claim",
        "subject": "memory",
        "predicate": "approach",
        "object": "spec-first",
        "claim_text": "Spec-first beats no spec.",
        "claim_scope": "global",
        "confidence": "high",
        "extracted_at": T_EXTRACTED,
        "extracted_by": {"agent": "demo-llm", "model": "gpt-5.5"},
        "review": {"reviewed_by": None, "reviewed_at": None},
        "metadata": {},
    },
    {
        "id": "cand_" + "b" * 24,
        "candidate_type": "claim",
        "subject": "memory",
        "predicate": "invariant",
        "object": "reducer-authority",
        "claim_text": "Only the reducer can promote memory to trusted.",
        "claim_scope": "global",
        "confidence": "high",
        "extracted_at": T_EXTRACTED,
        "extracted_by": {"agent": "demo-llm", "model": "gpt-5.5"},
        "review": {"reviewed_by": None, "reviewed_at": None},
        "metadata": {},
    },
    {
        "id": "cand_" + "c" * 24,
        "candidate_type": "claim",
        "subject": "schemas",
        "predicate": "version",
        "object": "1.0.0",
        "claim_text": "Schemas are at 1.0.0 and treated as stable.",
        "claim_scope": "global",
        "confidence": "high",
        "extracted_at": T_EXTRACTED,
        "extracted_by": {"agent": "demo-llm", "model": "gpt-5.5"},
        "review": {"reviewed_by": None, "reviewed_at": None},
        "metadata": {},
    },
    {
        "id": "cand_" + "d" * 24,
        "candidate_type": "claim",
        "subject": "library",
        "predicate": "feature",
        "object": "citation-graph",
        "claim_text": "The citation graph is first-in-market.",
        "claim_scope": "global",
        "confidence": "medium",
        "extracted_at": T_EXTRACTED,
        "extracted_by": {"agent": "demo-llm", "model": "gpt-5.5"},
        "review": {"reviewed_by": None, "reviewed_at": None},
        "metadata": {},
    },
    {
        "id": "cand_" + "e" * 24,
        "candidate_type": "claim",
        "subject": "library",
        "predicate": "feature",
        "object": "contextpack-compiler",
        "claim_text": "The ContextPack compiler is the bridge to company brain.",
        "claim_scope": "global",
        "confidence": "high",
        "extracted_at": T_EXTRACTED,
        "extracted_by": {"agent": "demo-llm", "model": "gpt-5.5"},
        "review": {"reviewed_by": None, "reviewed_at": None},
        "metadata": {},
    },
    # These two will be rejected in stage 3 (low
    # confidence / stale).
    {
        "id": "cand_" + "f" * 24,
        "candidate_type": "claim",
        "subject": "library",
        "predicate": "feature",
        "object": "vibe-code",
        "claim_text": "Vibe-code is a valid approach for prototypes.",
        "claim_scope": "global",
        "confidence": "low",
        "extracted_at": T_EXTRACTED,
        "extracted_by": {"agent": "demo-llm", "model": "gpt-5.5"},
        "review": {"reviewed_by": None, "reviewed_at": None},
        "metadata": {},
    },
    {
        "id": "cand_" + "g" * 24,
        "candidate_type": "claim",
        "subject": "library",
        "predicate": "feature",
        "object": "stale-claim",
        "claim_text": "This claim is stale and should be rejected.",
        "claim_scope": "global",
        "confidence": "high",
        "extracted_at": T_EXTRACTED,
        "extracted_by": {"agent": "demo-llm", "model": "gpt-5.5"},
        "review": {"reviewed_by": None, "reviewed_at": None},
        "metadata": {},
        "stale": True,  # custom marker for the demo
    },
    {
        "id": "cand_" + "h" * 24,
        "candidate_type": "claim",
        "subject": "library",
        "predicate": "feature",
        "object": "sensitive",
        "claim_text": "A sensitive claim that should be filtered by access control.",
        "claim_scope": "global",
        "confidence": "high",
        "extracted_at": T_EXTRACTED,
        "extracted_by": {"agent": "demo-llm", "model": "gpt-5.5"},
        "review": {"reviewed_by": None, "reviewed_at": None},
        "metadata": {},
    },
]


# ---------------------------------------------------------------------------
# Stage 3: Reduce (candidates -> FactLedgerEntry)
# ---------------------------------------------------------------------------

def _reduce(candidates: list[dict[str, Any]], sources: list[dict[str, Any]],
            spans: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Simulated reducer. Promotes high-confidence
    candidates to trusted ledger; rejects low-confidence
    and stale candidates. Returns (promoted, rejected).
    """
    from agent_memory_contracts import (
        FactLedgerEntry, make_ledger_entry_id, make_reducer_decision_id,
    )
    # Map candidates to evidence spans by id ordering:
    # cand_1 -> span_1, cand_2 -> span_2, etc. (Round-robin.)
    promoted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for i, cand in enumerate(candidates):
        if cand.get("confidence") == "low":
            rejected.append(cand)
            continue
        if cand.get("stale"):
            rejected.append(cand)
            continue
        # Pick an evidence span: round-robin through
        # the available spans.
        span = spans[i % len(spans)]
        source = next(s for s in sources if s["id"] == span["source_id"])
        red_id = make_reducer_decision_id("fact", [], [], [span["id"]], "ok")
        fact_id = make_ledger_entry_id(
            "fact", [span["id"]],
            {
                "ledger_type": "fact",
                "subject": cand["subject"],
                "predicate": cand["predicate"],
                "object": cand["object"],
                "scope": "global",
                "valid_from": T_DECIDED,
                "evidence_span_ids": [span["id"]],
            },
        )
        fact = FactLedgerEntry.from_dict({
            "id": fact_id, "schema_version": "1.0.0",
            "ledger_type": "fact", "status": "active",
            "confidence": cand["confidence"], "scope": "global",
            "source_record_ids": [source["id"]], "episode_record_ids": [],
            "evidence_span_ids": [span["id"]], "candidate_ids": [],
            "reducer_decision_id": red_id,
            "observed_at": None, "asserted_at": T_DECIDED,
            "valid_from": T_DECIDED, "valid_until": None, "stale_after": None,
            "created_at": T_DECIDED, "updated_at": T_DECIDED,
            "supersedes": [], "superseded_by": [], "metadata": {},
            "subject": cand["subject"], "predicate": cand["predicate"],
            "object": cand["object"], "fact_text": cand["claim_text"],
        })
        promoted.append({
            "id": fact.id, "schema_version": fact.schema_version,
            "ledger_type": fact.ledger_type, "status": fact.status,
            "confidence": fact.confidence, "scope": fact.scope,
            "source_record_ids": list(fact.source_record_ids),
            "episode_record_ids": list(fact.episode_record_ids),
            "evidence_span_ids": list(fact.evidence_span_ids),
            "candidate_ids": list(fact.candidate_ids),
            "reducer_decision_id": fact.reducer_decision_id,
            "observed_at": fact.observed_at, "asserted_at": fact.asserted_at,
            "valid_from": fact.valid_from, "valid_until": fact.valid_until,
            "stale_after": fact.stale_after,
            "created_at": fact.created_at, "updated_at": fact.updated_at,
            "supersedes": list(fact.supersedes),
            "superseded_by": list(fact.superseded_by),
            "metadata": dict(fact.metadata),
            "subject": fact.subject, "predicate": fact.predicate,
            "object": fact.object, "fact_text": fact.fact_text,
        })
    return promoted, rejected


# ---------------------------------------------------------------------------
# Stage 5: Access control
# ---------------------------------------------------------------------------

def _with_privacy_class(record: dict[str, Any], privacy_class: str) -> dict[str, Any]:
    """Annotate a record with a privacy_class for the demo."""
    r = dict(record)
    r["privacy_class"] = privacy_class
    return r


def _state_dict() -> dict[str, Any]:
    """A minimal ProjectStateSnapshot for the compiler."""
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


# ---------------------------------------------------------------------------
# Main: the 7-stage pipeline
# ---------------------------------------------------------------------------


def section(title: str) -> None:
    print()
    print("=" * 70)
    print(title)
    print("=" * 70)


def main(argv: list[str] | None = None) -> int:
    section("Company Brain Demo: From Raw Sources to Trusted Context")
    print("v0.7.0 / v0.8.0 / v0.9.0 / v1.0.0-alpha.1 / v1.0.0-alpha.2 / v1.0.0-alpha.3")
    print()

    # Stage 1: Ingest.
    section("Stage 1: INGEST (3 raw sources -> 3 SourceRecord + 6 EvidenceSpan)")
    sources, spans = stage_1_ingest()
    print(f"  SourceRecord:  {len(sources)}")
    print(f"  EvidenceSpan:  {len(spans)}")
    for s in sources:
        print(f"    - {s['id'][:24]}...  {s['source_type']:25s}  {s['title']!r}")

    # Stage 2: Extract.
    section("Stage 2: EXTRACT (simulated LLM -> 8 CandidateClaim)")
    candidates = [dict(c) for c in SIMULATED_CANDIDATES]
    print(f"  candidates:    {len(candidates)}")
    for c in candidates:
        print(f"    - {c['id'][:24]}...  conf={c['confidence']:6s}  {c['claim_text'][:60]!r}")

    # Stage 3: Reduce.
    section("Stage 3: REDUCE (candidates -> FactLedgerEntry)")
    promoted, rejected = _reduce(candidates, sources, spans)
    print(f"  promoted:      {len(promoted)} (high or medium confidence)")
    print(f"  rejected:      {len(rejected)} (low confidence or stale)")

    # Stage 4: Cite.
    section("Stage 4: CITE (CitationGraph: all promoted claims have source chains)")
    # Build the graph from the full trusted bundle
    # (sources + spans + facts). Building from just
    # the facts would leave the spans as dangling refs.
    trusted_bundle = sources + spans + promoted
    graph = CitationGraph.from_bundle(trusted_bundle)
    print(f"  graph size:    {graph.size()} nodes")
    print(f"  by kind:       {graph.node_count_by_kind()}")
    print(f"  dangling:      {len(graph.dangling_refs)}")
    # Verify every promoted fact has a supported path.
    unsupported = 0
    for fact in promoted:
        paths = graph.traverse(fact["id"], direction="forward")
        if not any(p.is_supported() for p in paths):
            unsupported += 1
    print(f"  unsupported:   {unsupported}")

    # Stage 5: Access.
    section("Stage 5: ACCESS (team_scope filters the bundle)")
    # Annotate one promoted fact as sensitive.
    promoted_with_privacy = [_with_privacy_class(p, "internal") for p in promoted]
    if promoted_with_privacy:
        promoted_with_privacy[0]["privacy_class"] = "highly_sensitive"
    filtered, decisions = scope_bundle(promoted_with_privacy, team_scope())
    summary = summarize_access(decisions)
    print(f"  team_scope:    {len(filtered)} of {len(promoted_with_privacy)} records allowed")
    print(f"  by action:     {dict(summary.by_action)}")
    for d in decisions:
        if d.action != "allow":
            print(f"    - {d.record_id[:24]}...  dropped: {d.reason}")

    # Stage 6: Embed.
    section("Stage 6: EMBED (EmbeddingInput per filtered record)")
    bundle_for_compile = list(filtered) + list(spans) + list(sources)
    embeddings: list[EmbeddingInput] = [
        record_to_embedding_input(r) for r in filtered
    ]
    print(f"  embeddings:    {len(embeddings)}")
    for ei in embeddings[:3]:
        first_line = ei.text.split("\n", 1)[0]
        print(f"    - {ei.record_type:25s}  hash={ei.content_hash_sha256[:12]}...  text: {first_line!r}")

    # Stage 7: Compile.
    section("Stage 7: COMPILE (ContextPack for the task)")
    task = ContextPackTask(
        task_id="t1",
        task_title="what is the spec-first approach?",
        task_type="research",
        task_summary="User asked about the spec-first approach.",
        project_id="agent-memory-contracts",
        risk_class="low", sensitivity="internal",
    )
    # Add a state record so the compiler has a state
    # reference.
    bundle_for_compile_with_state = bundle_for_compile + [_state_dict()]
    result = compile_context_pack(bundle_for_compile_with_state, task=task)
    print(f"  pack_id:           {result.context_pack.id}")
    print(f"  pack_hash:         {result.context_pack.pack_hash_sha256[:24]}...")
    print(f"  selected:          {len(result.selected_record_ids)} records")
    print(f"  excluded:          {len(result.excluded_record_ids)} records")
    print(f"  validation status: {result.validation_report.status}")
    print(f"  build_receipt:     {result.build_receipt.builder['agent']} "
          f"(mode={result.build_receipt.builder['mode']})")
    print(f"  receipt input_refs: {dict(result.build_receipt.input_refs)}")

    section("RESULT: task-ready ContextPack with full audit trail")
    print("  - 3 SourceRecord (raw inputs)")
    print("  - 6 EvidenceSpan (slice references)")
    print(f"  - {len(promoted)} FactLedgerEntry (trusted memory)")
    print(f"  - {len(filtered)} records passed team_scope")
    print(f"  - {len(embeddings)} EmbeddingInput (text + metadata)")
    print(f"  - 1 ContextPack (task-ready, with BuildReceipt + ValidationReport)")
    print()
    print("  This is the v1.0.0 story: the integrity layer + the company-brain primitive.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
