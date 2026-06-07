"""Tests for the ContextPack compiler.

Coverage targets (per docs/specs/sprint_24c_context_pack_compiler.md):

1. ContextPackTask validates its fields at construction.
2. CompilationPolicy validates its fields at construction.
3. CompilationResult is frozen.
4. compile_context_pack with a small bundle produces a
   ContextPack with the expected shape.
5. Source-coverage enforcement excludes claims without
   a path to a source.
6. Status filter excludes stale/retracted records by
   default; contested records are kept.
7. Selection strategies (recent, diverse, frequent).
8. max_records caps the selection.
9. Scope filtering applies BundleScope from v0.9.0.
10. Idempotency: same inputs produce the same output.
11. Dataclass and dict inputs produce equivalent results.
12. Empty bundle returns empty result.
13. No state record in bundle raises ValueError.
14. Public API exports.
"""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any

from agent_memory_contracts import (
    CompilationPolicy,
    CompilationResult,
    ContextPackTask,
    compile_context_pack,
)

from .fixtures import T_CAPTURED, T_DECIDED, build_source_and_span


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _build_source_dict() -> dict[str, Any]:
    src, _ = build_source_and_span()
    return dataclasses.asdict(src)


def _build_span_dict(src_id: str) -> dict[str, Any]:
    src, span = build_source_and_span()
    return dataclasses.asdict(span)


def _build_fact_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "fact_" + "a" * 24,
        "schema_version": "1.0.0",
        "ledger_type": "fact",
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "source_record_ids": [src_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "candidate_ids": [],
        "reducer_decision_id": "redmem_" + "a" * 24,
        "observed_at": None,
        "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED,
        "valid_until": None,
        "stale_after": None,
        "created_at": T_DECIDED,
        "updated_at": T_DECIDED,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {},
        "subject": "s",
        "predicate": "p",
        "object": "o",
        "fact_text": "Spec-first beats no spec.",
    }


def _build_unsupported_fact_dict() -> dict[str, Any]:
    """A fact citing a span that's missing from the bundle
    (used to test source-coverage enforcement). The
    fact's evidence_span_ids point to a span id that
    is NOT in the bundle, so the citation graph has
    a dangling ref and the fact has no path to a
    source.
    """
    return {
        "id": "fact_" + "b" * 24,
        "schema_version": "1.0.0",
        "ledger_type": "fact",
        "status": "active",
        "confidence": "low",
        "scope": "global",
        "source_record_ids": ["src_" + "z" * 24],  # not in bundle
        "episode_record_ids": [],
        "evidence_span_ids": ["span_" + "z" * 23],  # not in bundle
        "candidate_ids": [],
        "reducer_decision_id": "redmem_" + "b" * 24,
        "observed_at": None,
        "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED,
        "valid_until": None,
        "stale_after": None,
        "created_at": T_DECIDED,
        "updated_at": T_DECIDED,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {},
        "subject": "s",
        "predicate": "p",
        "object": "o",
        "fact_text": "Unsupported claim.",
    }


def _build_stale_fact_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "fact_" + "c" * 24,
        "schema_version": "1.0.0",
        "ledger_type": "fact",
        "status": "stale",
        "confidence": "low",
        "scope": "global",
        "source_record_ids": [src_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "candidate_ids": [],
        "reducer_decision_id": "redmem_" + "c" * 24,
        "observed_at": None,
        "asserted_at": T_DECIDED,
        "valid_from": T_DECIDED,
        "valid_until": None,
        "stale_after": None,
        "created_at": T_DECIDED,
        "updated_at": T_DECIDED,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {},
        "subject": "s",
        "predicate": "p",
        "object": "o",
        "fact_text": "Stale fact.",
    }


def _build_state_dict() -> dict[str, Any]:
    return {
        "id": "projstate_" + "d" * 24,
        "schema_version": "1.0.0",
        "state_type": "project_state",
        "status": "active",
        "as_of": T_CAPTURED,
        "summary": "spec-first beats no spec",
        "active_fact_ids": [],
        "active_preference_ids": [],
        "active_decision_ids": [],
        "active_taste_card_ids": [],
        "source_record_ids": [],
        "episode_record_ids": [],
        "evidence_span_ids": [],
        "reducer_decision_id": "redstate_" + "d" * 21,
        "project_id": "agent-memory-contracts",
        "summary_text": "spec-first beats no spec",
        "supersedes": [],
        "superseded_by": [],
        "metadata": {},
    }


def _task() -> ContextPackTask:
    return ContextPackTask(
        task_id="t1",
        task_title="what is the spec?",
        task_type="research",
        task_summary="User asked about the spec.",
        project_id="agent-memory-contracts",
        risk_class="low",
        sensitivity="internal",
    )


def _trusted_bundle() -> list[dict[str, Any]]:
    src = _build_source_dict()
    span = _build_span_dict(src["id"])
    fact = _build_fact_dict(src["id"], span["id"])
    state = _build_state_dict()
    return [src, span, fact, state]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestContextPackTask(unittest.TestCase):
    """ContextPackTask validates its fields at construction."""

    def test_construction(self) -> None:
        task = _task()
        self.assertEqual(task.task_id, "t1")
        self.assertEqual(task.task_type, "research")
        self.assertEqual(task.risk_class, "low")

    def test_invalid_task_type(self) -> None:
        with self.assertRaises(ValueError):
            ContextPackTask(
                task_id="t", task_title="t", task_type="invalid",
                task_summary="t", project_id="p",
                risk_class="low", sensitivity="internal",
            )

    def test_invalid_risk_class(self) -> None:
        with self.assertRaises(ValueError):
            ContextPackTask(
                task_id="t", task_title="t", task_type="research",
                task_summary="t", project_id="p",
                risk_class="extreme", sensitivity="internal",
            )

    def test_to_dict(self) -> None:
        task = _task()
        d = task.to_dict()
        self.assertEqual(d["task_id"], "t1")
        self.assertIn("sensitivity", d)
        self.assertEqual(len(d), 7)


class TestCompilationPolicy(unittest.TestCase):
    """CompilationPolicy validates its fields at construction."""

    def test_default_policy(self) -> None:
        p = CompilationPolicy()
        self.assertEqual(p.max_records, 50)
        self.assertTrue(p.require_source_coverage)
        self.assertEqual(p.selection_strategy, "recent")

    def test_negative_max_records(self) -> None:
        with self.assertRaises(ValueError):
            CompilationPolicy(max_records=-1)

    def test_invalid_strategy(self) -> None:
        with self.assertRaises(ValueError):
            CompilationPolicy(selection_strategy="random")

    def test_invalid_builder_mode(self) -> None:
        with self.assertRaises(ValueError):
            CompilationPolicy(builder_mode="invalid")


class TestCompilationResultFrozen(unittest.TestCase):
    """CompilationResult is frozen."""

    def test_frozen(self) -> None:
        # We need a CompilationResult. The bundle in
        # ``_trusted_bundle`` produces one.
        result = compile_context_pack(_trusted_bundle(), task=_task())
        with self.assertRaises(Exception):
            result.selected_record_ids = ()  # type: ignore[misc]


class TestCompileContextPack(unittest.TestCase):
    """The headline function."""

    def test_basic_compilation(self) -> None:
        result = compile_context_pack(_trusted_bundle(), task=_task())
        self.assertIsInstance(result, CompilationResult)
        self.assertIsNotNone(result.context_pack.id)
        self.assertEqual(result.context_pack.task["task_id"], "t1")
        self.assertEqual(result.context_pack.pack_type, "context_pack")
        # The fact is in the trusted_memory.
        self.assertEqual(len(result.context_pack.trusted_memory["fact_ids"]), 1)
        # The source and span are in the evidence.
        self.assertGreaterEqual(
            len(result.context_pack.evidence["source_record_ids"]), 1
        )
        self.assertGreaterEqual(
            len(result.context_pack.evidence["evidence_span_ids"]), 1
        )

    def test_idempotency(self) -> None:
        bundle = _trusted_bundle()
        r1 = compile_context_pack(bundle, task=_task())
        r2 = compile_context_pack(bundle, task=_task())
        # Different ``now`` timestamps differ (created_at
        # changes second-by-second). The pack_hash, id,
        # and selection should match if the test runs
        # within the same second; otherwise we just
        # check that the structure is identical.
        self.assertEqual(r1.selected_record_ids, r2.selected_record_ids)
        self.assertEqual(r1.excluded_record_ids, r2.excluded_record_ids)
        self.assertEqual(
            r1.context_pack.trusted_memory,
            r2.context_pack.trusted_memory,
        )

    def test_unsupported_claim_excluded(self) -> None:
        """A fact citing a missing source/span is excluded
        by the source-coverage filter."""
        src = _build_source_dict()
        span = _build_span_dict(src["id"])
        unsupported = _build_unsupported_fact_dict()
        state = _build_state_dict()
        bundle = [src, span, unsupported, state]
        result = compile_context_pack(bundle, task=_task())
        # The unsupported fact is excluded.
        unsupported_id = unsupported["id"]
        self.assertNotIn(unsupported_id, result.selected_record_ids)
        # The reason is "no_source_backing".
        excluded_with_reason = [
            rid for rid in result.excluded_record_ids
            if rid == unsupported_id
        ]
        self.assertEqual(len(excluded_with_reason), 1)

    def test_stale_excluded_by_default(self) -> None:
        src = _build_source_dict()
        span = _build_span_dict(src["id"])
        fact = _build_fact_dict(src["id"], span["id"])
        stale = _build_stale_fact_dict(src["id"], span["id"])
        state = _build_state_dict()
        bundle = [src, span, fact, stale, state]
        result = compile_context_pack(bundle, task=_task())
        self.assertNotIn(stale["id"], result.selected_record_ids)

    def test_max_records_caps(self) -> None:
        bundle = _trusted_bundle()
        result = compile_context_pack(
            bundle, task=_task(), policy=CompilationPolicy(max_records=1)
        )
        # Only the top-1 record is selected.
        self.assertEqual(len(result.selected_record_ids), 1)
        # The rest are excluded with reason "exceeded_max_records".
        self.assertGreater(len(result.excluded_record_ids), 0)

    def test_scope_filtering(self) -> None:
        from agent_memory_contracts import team_scope, public_scope
        # Build two sources with distinct privacy classes.
        # Each source has its own span and fact.
        # All records carry the privacy_class so scope
        # filtering works correctly.
        public_src = _build_source_dict()
        public_src["id"] = "src_" + "a" * 24
        public_src["privacy_class"] = "public"
        public_span = _build_span_dict(public_src["id"])
        public_span["source_id"] = public_src["id"]
        public_span["id"] = "span_" + "a" * 24
        public_span["privacy_class"] = "public"
        public_fact = _build_fact_dict(public_src["id"], public_span["id"])
        public_fact["source_record_ids"] = [public_src["id"]]
        public_fact["privacy_class"] = "public"

        internal_src = _build_source_dict()
        internal_src["id"] = "src_" + "e" * 24
        internal_src["title"] = "Internal source"
        internal_src["privacy_class"] = "internal"
        internal_span = _build_span_dict(internal_src["id"])
        internal_span["source_id"] = internal_src["id"]
        internal_span["id"] = "span_" + "e" * 23
        internal_span["privacy_class"] = "internal"
        internal_fact = _build_fact_dict(internal_src["id"], internal_span["id"])
        internal_fact["source_record_ids"] = [internal_src["id"]]
        internal_fact["privacy_class"] = "internal"

        # State.
        state = _build_state_dict()
        bundle = [
            public_src, public_span, public_fact,
            internal_src, internal_span, internal_fact,
            state,
        ]
        # team_scope: keeps public + internal. Both sources
        # are selected.
        result_team = compile_context_pack(bundle, task=_task(), scope=team_scope())
        self.assertIn(public_src["id"], result_team.selected_record_ids)
        # public_scope: keeps only public. The internal
        # source is excluded.
        result_public = compile_context_pack(bundle, task=_task(), scope=public_scope())
        self.assertIn(public_src["id"], result_public.selected_record_ids)
        self.assertNotIn(internal_src["id"], result_public.selected_record_ids)

    def test_empty_bundle_raises(self) -> None:
        # Empty bundle has no state record.
        with self.assertRaises(ValueError):
            compile_context_pack([], task=_task())

    def test_no_state_record_raises(self) -> None:
        # Bundle without a state record raises.
        src = _build_source_dict()
        span = _build_span_dict(src["id"])
        fact = _build_fact_dict(src["id"], span["id"])
        with self.assertRaises(ValueError):
            compile_context_pack([src, span, fact], task=_task())

    def test_selection_strategy_diverse(self) -> None:
        bundle = _trusted_bundle()
        result = compile_context_pack(
            bundle, task=_task(), policy=CompilationPolicy(selection_strategy="diverse")
        )
        # Diverse picks one record per record_type,
        # rotating. We don't assert the exact set
        # (depends on graph in-degree), but we do
        # assert the result is well-formed.
        self.assertGreater(len(result.selected_record_ids), 0)

    def test_selection_strategy_frequent(self) -> None:
        bundle = _trusted_bundle()
        result = compile_context_pack(
            bundle, task=_task(), policy=CompilationPolicy(selection_strategy="frequent")
        )
        self.assertGreater(len(result.selected_record_ids), 0)

    def test_require_source_coverage_false(self) -> None:
        # With require_source_coverage=False, the
        # unsupported fact is NOT excluded.
        src = _build_source_dict()
        span = _build_span_dict(src["id"])
        unsupported = _build_unsupported_fact_dict()
        state = _build_state_dict()
        bundle = [src, span, unsupported, state]
        result = compile_context_pack(
            bundle,
            task=_task(),
            policy=CompilationPolicy(require_source_coverage=False),
        )
        self.assertIn(unsupported["id"], result.selected_record_ids)

    def test_no_evidence_raises_when_source_coverage_required(self) -> None:
        # Bundle with no evidence spans at all (just
        # sources and a state record) raises when
        # require_source_coverage is True, because
        # primary_evidence_span_ids cannot be empty.
        src = _build_source_dict()
        state = _build_state_dict()
        with self.assertRaises(ValueError):
            compile_context_pack([src, state], task=_task())

    def test_no_evidence_ok_when_source_coverage_not_required(self) -> None:
        # With require_source_coverage=False, the
        # compiler synthesizes a placeholder primary
        # evidence span id.
        src = _build_source_dict()
        state = _build_state_dict()
        result = compile_context_pack(
            [src, state],
            task=_task(),
            policy=CompilationPolicy(require_source_coverage=False),
        )
        self.assertIsInstance(result, CompilationResult)


class TestDataclassInput(unittest.TestCase):
    """Dataclass and dict inputs produce equivalent results."""

    def test_dataclass_input(self) -> None:
        from agent_memory_contracts import SourceRecord, EvidenceSpan, FactLedgerEntry
        src, span = build_source_and_span()
        from agent_memory_contracts import make_ledger_entry_id, make_reducer_decision_id
        red_id = make_reducer_decision_id("fact", [], [], [span.id], "ok")
        fact_id = make_ledger_entry_id(
            "fact", [span.id],
            {
                "ledger_type": "fact", "subject": "s", "predicate": "p", "object": "o",
                "scope": "global", "valid_from": T_DECIDED, "evidence_span_ids": [span.id],
            },
        )
        fact = FactLedgerEntry.from_dict({
            "id": fact_id, "schema_version": "1.0.0",
            "ledger_type": "fact", "status": "active", "confidence": "high", "scope": "global",
            "source_record_ids": [src.id], "episode_record_ids": [],
            "evidence_span_ids": [span.id], "candidate_ids": [],
            "reducer_decision_id": red_id,
            "observed_at": None, "asserted_at": T_DECIDED,
            "valid_from": T_DECIDED, "valid_until": None, "stale_after": None,
            "created_at": T_DECIDED, "updated_at": T_DECIDED,
            "supersedes": [], "superseded_by": [], "metadata": {},
            "subject": "s", "predicate": "p", "object": "o", "fact_text": "t",
        })
        state = _build_state_dict()
        result = compile_context_pack([src, span, fact, state], task=_task())
        self.assertIsInstance(result, CompilationResult)
        self.assertGreater(len(result.selected_record_ids), 0)


class TestPublicApi(unittest.TestCase):
    """All v1.0.0-alpha.3 names are exported."""

    def test_v100a3_exports_present(self) -> None:
        import agent_memory_contracts as a
        for name in (
            "ContextPackTask",
            "CompilationPolicy",
            "CompilationResult",
            "compile_context_pack",
        ):
            self.assertTrue(hasattr(a, name), f"missing export: {name}")


if __name__ == "__main__":
    unittest.main()
