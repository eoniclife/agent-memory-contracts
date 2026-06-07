"""Tests for the embedding input primitive.

Coverage targets (per docs/specs/sprint_24a_embedding_input.md):

1. EmbeddingInput is frozen; invariants are checked at
   construction.
2. record_to_embedding_input renders each of the 12
   per-type record shapes correctly.
3. Dict-form records produce the same text as the
   equivalent dataclass record.
4. content_hash_sha256 is deterministic across
   dataclass and dict forms.
5. Truncation: long text is cut at the sentence boundary
   and marked truncated=True.
6. Privacy class is surfaced (defaulting to "internal").
7. Metadata structure is flat primitive values.
8. Round-trip via to_dict / from_dict.
9. Generic renderer for unknown record types.
10. Public API exports.

Note: most tests use *dict-form* records rather than
constructing real dataclass records. This is the same
pattern v0.8.0 / v0.9.0 used: the embedding module
accepts both shapes, and the dict path keeps the tests
focused on the embedding-input behavior rather than the
library's per-type record validation.
"""

from __future__ import annotations

import dataclasses
import json
import unittest
from typing import Any

from agent_memory_contracts import (
    DEFAULT_MAX_CHARS,
    EmbeddingInput,
    embedding_input_from_dict,
    embedding_input_to_dict,
    record_to_embedding_input,
    text_for_record_type,
)

from .fixtures import T_CAPTURED, T_DECIDED, build_source_and_span


# ---------------------------------------------------------------------------
# Fixture builders (dict-form, to avoid per-type dataclass validation)
# ---------------------------------------------------------------------------


def _build_source_dict() -> dict[str, Any]:
    src, _ = build_source_and_span()
    return dataclasses.asdict(src)


def _build_episode_dict(src_id: str) -> dict[str, Any]:
    return {
        "id": "ep_" + "a" * 24,
        "schema_version": "1.0.0",
        "source_id": src_id,
        "episode_type": "conversation_segment",
        "episode_locator": {"kind": "ordinal", "value": "1"},
        "title": "Test episode",
        "summary": "A conversation segment.",
        "event_time_start": T_CAPTURED,
        "event_time_end": None,
        "actors": ["alice", "bob"],
        "topics": ["memory"],
        "project_refs": [],
        "evidence_span_ids": [],
        "metadata": {},
    }


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
        "reducer_decision_id": "redmem_aaaa" + "a" * 18,
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


def _build_claim_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "cand_claim_aaaa" + "a" * 14,
        "schema_version": "1.0.0",
        "candidate_type": "claim",
        "source_record_ids": [src_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": "A claim.",
        "extracted_by": {"agent": "ext", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
        "extracted_at": "2026-05-30T12:30:00Z",
        "confidence": "high",
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "subject": "s",
        "predicate": "p",
        "object": "o",
        "claim_text": "Spec-first beats no spec.",
        "claim_scope": "global",
        "temporal_hint": {},
    }


def _build_decision_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "cand_decision_aaaa" + "a" * 12,
        "schema_version": "1.0.0",
        "candidate_type": "decision",
        "source_record_ids": [src_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": "A decision.",
        "extracted_by": {"agent": "ext", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
        "extracted_at": "2026-05-30T12:30:00Z",
        "confidence": "high",
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "decision_text": "We will use the spec-first approach.",
        "decision_scope": "global",
        "alternatives_mentioned": ["vibe-code", "no-spec"],
        "rationale_text": "Falsifiable specs catch drift.",
        "decision_time_hint": "now",
        "owner_hint": "team",
        "reversibility": "reversible",
    }


def _build_preference_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "cand_preference_aaaa" + "a" * 11,
        "schema_version": "1.0.0",
        "candidate_type": "preference",
        "source_record_ids": [src_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": "A preference.",
        "extracted_by": {"agent": "ext", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
        "extracted_at": "2026-05-30T12:30:00Z",
        "confidence": "high",
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "subject": "memory architecture",
        "preference_text": "Spec-text drift is worse than no spec.",
        "domain": "architecture",
        "scope": "global",
        "strength_hint": "strong",
        "counterevidence_span_ids": [],
    }


def _build_task_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "cand_task_aaaa" + "a" * 15,
        "schema_version": "1.0.0",
        "candidate_type": "task",
        "source_record_ids": [src_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": "A task.",
        "extracted_by": {"agent": "ext", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
        "extracted_at": "2026-05-30T12:30:00Z",
        "confidence": "high",
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "task_text": "Write the embedding input spec.",
        "task_kind": "spec",
        "project_refs": ["agent-memory-contracts"],
        "owner_hint": "team",
        "due_at_hint": "2026-06-15",
        "urgency_hint": "high",
        "safety_lane": "library",
        "autostart_eligible": True,
    }


def _build_taste_signal_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "cand_taste_aaaa" + "a" * 14,
        "schema_version": "1.0.0",
        "candidate_type": "taste_signal",
        "source_record_ids": [src_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "natural_language_summary": "A taste signal.",
        "extracted_by": {"agent": "ext", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
        "extracted_at": "2026-05-30T12:30:00Z",
        "confidence": "high",
        "risk_class": "low",
        "status": "candidate",
        "review": {"reviewed_by": None, "reviewed_at": None, "review_notes": None},
        "metadata": {},
        "domain": "architecture",
        "signal_kind": "principle",
        "taste_text": "use the spec",
        "example_span_ids": [span_id],
        "contrast_span_ids": [],
        "strength_hint": "strong",
    }


def _build_taste_card_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "taste_aaaa" + "a" * 19,
        "schema_version": "1.0.0",
        "card_type": "taste_card",
        "status": "active",
        "subject": "taste",
        "domain": "architecture",
        "scope": "global",
        "project_refs": [],
        "principle": "Spec-first beats no spec.",
        "rationale": "Falsifiable specs catch drift.",
        "strength": "strong",
        "confidence": "high",
        "taste_kind": "principle",
        "source_record_ids": [src_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "candidate_taste_signal_ids": [],
        "positive_example_span_ids": [span_id],
        "negative_example_span_ids": [],
        "contrast_pairs": [],
        "objection_patterns": [],
        "application_notes": [],
        "reducer_decision_id": "redtaste_aaaa" + "a" * 17,
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
    }


def _build_context_pack_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "cp_aaaa" + "a" * 20,
        "schema_version": "1.0.0",
        "pack_type": "context_pack",
        "task": {
            "task_id": "t1", "task_title": "what is the spec?",
            "task_type": "question", "task_summary": "User asked about the spec.",
            "project_id": "agent-memory-contracts",
            "risk_class": "low", "sensitivity": "internal",
        },
        "authority": "self",
        "state": {"project_states": [], "core_states": []},
        "trusted_memory": [src_id],
        "candidate_context": [],
        "evidence": [span_id],
        "stale_or_superseded": [],
        "conflicts_and_uncertainties": [],
        "constraints": [],
        "retrieval_trace": ["step 1: lookup", "step 2: render"],
        "pack_hash_sha256": "1" * 64,
        "metadata": {},
    }


def _build_decision_ledger_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "decision_" + "a" * 24,
        "schema_version": "1.0.0",
        "ledger_type": "decision",
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "source_record_ids": [src_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "candidate_ids": [],
        "reducer_decision_id": "redmem_aaaa" + "a" * 18,
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
        "decision_text": "Adopt spec-first sprints.",
        "decision_scope": "global",
        "alternatives_considered": ["vibe-code", "no-spec"],
        "rationale_text": "Falsifiable specs catch drift.",
        "owner": "team",
        "reversibility": "reversible",
    }


def _build_preference_ledger_dict(src_id: str, span_id: str) -> dict[str, Any]:
    return {
        "id": "preference_" + "a" * 22,
        "schema_version": "1.0.0",
        "ledger_type": "preference",
        "status": "active",
        "confidence": "high",
        "scope": "global",
        "source_record_ids": [src_id],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "candidate_ids": [],
        "reducer_decision_id": "redmem_aaaa" + "a" * 18,
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
        "subject": "memory architecture",
        "preference_text": "Spec-text drift is worse than no spec.",
        "domain": "architecture",
        "strength": "strong",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestEmbeddingInputDataclass(unittest.TestCase):
    """EmbeddingInput is frozen; invariants are checked."""

    def test_required_text(self) -> None:
        with self.assertRaises(ValueError):
            EmbeddingInput(
                record_id="x", record_type="unknown", text="",
                privacy_class="internal", content_hash_sha256="0" * 64,
                char_count=0, metadata={}, plane="audit",
            )

    def test_required_record_id(self) -> None:
        with self.assertRaises(ValueError):
            EmbeddingInput(
                record_id="", record_type="unknown", text="hello",
                privacy_class="internal", content_hash_sha256="0" * 64,
                char_count=5, metadata={}, plane="audit",
            )

    def test_char_count_must_match(self) -> None:
        with self.assertRaises(ValueError):
            EmbeddingInput(
                record_id="x", record_type="unknown", text="hello",
                privacy_class="internal", content_hash_sha256="0" * 64,
                char_count=99, metadata={}, plane="audit",
            )

    def test_content_hash_must_be_64_hex(self) -> None:
        with self.assertRaises(ValueError):
            EmbeddingInput(
                record_id="x", record_type="unknown", text="hello",
                privacy_class="internal", content_hash_sha256="abc",
                char_count=5, metadata={}, plane="audit",
            )


class TestPerTypeRenderers(unittest.TestCase):
    """Each of the 12 per-type renderers produces sensible text."""

    def setUp(self) -> None:
        self.src_dict = _build_source_dict()
        self.src_id = self.src_dict["id"]
        self.span_id = self.src_dict.get("id", "")  # placeholder; replaced below
        # Build a span that references the source.
        self.span_dict = _build_span_dict(self.src_id)
        self.span_id = self.span_dict["id"]
        self.fact = _build_fact_dict(self.src_id, self.span_id)
        self.claim = _build_claim_dict(self.src_id, self.span_id)
        self.decision = _build_decision_dict(self.src_id, self.span_id)
        self.preference = _build_preference_dict(self.src_id, self.span_id)
        self.task = _build_task_dict(self.src_id, self.span_id)
        self.taste_signal = _build_taste_signal_dict(self.src_id, self.span_id)
        self.taste_card = _build_taste_card_dict(self.src_id, self.span_id)
        self.context_pack = _build_context_pack_dict(self.src_id, self.span_id)
        self.decision_ledger = _build_decision_ledger_dict(self.src_id, self.span_id)
        self.preference_ledger = _build_preference_ledger_dict(self.src_id, self.span_id)
        self.episode = _build_episode_dict(self.src_id)

    def test_source_record_renderer(self) -> None:
        ei = record_to_embedding_input(self.src_dict)
        self.assertEqual(ei.record_type, "source_record")
        self.assertEqual(ei.plane, "evidence")
        self.assertIn("Title:", ei.text)
        self.assertIn("Author:", ei.text)
        self.assertIn("URI:", ei.text)

    def test_episode_record_renderer(self) -> None:
        ei = record_to_embedding_input(self.episode)
        self.assertEqual(ei.record_type, "episode_record")
        self.assertIn("Episode: Test episode", ei.text)
        self.assertIn("Actors: alice, bob", ei.text)

    def test_evidence_span_renderer(self) -> None:
        ei = record_to_embedding_input(self.span_dict)
        self.assertEqual(ei.record_type, "evidence_span")
        self.assertIn("Locator: line_range=10-15", ei.text)

    def test_fact_ledger_entry_renderer(self) -> None:
        ei = record_to_embedding_input(self.fact)
        self.assertEqual(ei.record_type, "fact_ledger_entry")
        self.assertEqual(ei.plane, "ledger")
        self.assertIn("Spec-first beats no spec.", ei.text)
        self.assertIn("Subject: s p o", ei.text)
        self.assertIn(f"Evidence: {self.span_id}", ei.text)

    def test_decision_ledger_entry_renderer(self) -> None:
        ei = record_to_embedding_input(self.decision_ledger)
        self.assertEqual(ei.record_type, "decision_ledger_entry")
        self.assertIn("Decision: Adopt spec-first sprints.", ei.text)
        self.assertIn("Owner: team", ei.text)

    def test_preference_ledger_entry_renderer(self) -> None:
        ei = record_to_embedding_input(self.preference_ledger)
        self.assertEqual(ei.record_type, "preference_ledger_entry")
        self.assertIn("Preference: Spec-text drift is worse than no spec.", ei.text)

    def test_candidate_claim_renderer(self) -> None:
        ei = record_to_embedding_input(self.claim)
        self.assertEqual(ei.record_type, "candidate_claim")
        self.assertEqual(ei.plane, "candidate")
        self.assertIn("Claim: Spec-first beats no spec.", ei.text)

    def test_candidate_decision_renderer(self) -> None:
        ei = record_to_embedding_input(self.decision)
        self.assertEqual(ei.record_type, "candidate_decision")
        self.assertIn("Decision: We will use the spec-first approach.", ei.text)
        self.assertIn("Alternatives: vibe-code, no-spec", ei.text)

    def test_candidate_preference_renderer(self) -> None:
        ei = record_to_embedding_input(self.preference)
        self.assertEqual(ei.record_type, "candidate_preference")
        self.assertIn("Preference: Spec-text drift is worse than no spec.", ei.text)

    def test_candidate_task_renderer(self) -> None:
        ei = record_to_embedding_input(self.task)
        self.assertEqual(ei.record_type, "candidate_task")
        self.assertIn("Task: Write the embedding input spec.", ei.text)
        self.assertIn("Projects: agent-memory-contracts", ei.text)

    def test_candidate_taste_signal_renderer(self) -> None:
        ei = record_to_embedding_input(self.taste_signal)
        self.assertEqual(ei.record_type, "candidate_taste_signal")
        self.assertIn("Taste: use the spec", ei.text)

    def test_taste_card_renderer(self) -> None:
        ei = record_to_embedding_input(self.taste_card)
        self.assertEqual(ei.record_type, "taste_card")
        self.assertEqual(ei.plane, "taste")
        self.assertIn("Principle: Spec-first beats no spec.", ei.text)

    def test_context_pack_renderer(self) -> None:
        ei = record_to_embedding_input(self.context_pack)
        self.assertEqual(ei.record_type, "context_pack")
        self.assertEqual(ei.plane, "contextpack")
        self.assertIn("ContextPack: context_pack", ei.text)
        # The task field is structured; the renderer surfaces
        # the most useful text (summary > title > type).
        # We check for "Task:" + at least one of the fields.
        self.assertIn("Task:", ei.text)
        self.assertTrue(
            "User asked about the spec." in ei.text
            or "what is the spec?" in ei.text.lower()
            or "question" in ei.text
        )

    def test_generic_renderer(self) -> None:
        unknown = {"id": "x_1", "schema_version": "1.0.0", "foo": "bar", "baz": 42}
        text = text_for_record_type(unknown)
        self.assertIn("foo: bar", text)
        self.assertIn("baz: 42", text)

    def test_audit_record_renderer(self) -> None:
        # MemoryReducerDecision falls back to the generic
        # renderer; it has no natural-text field.
        reducer = {
            "id": "redmem_aaaa" + "a" * 18,
            "schema_version": "1.0.0",
            "decision_type": "archive",
            "target_candidate_ids": [],
            "target_ledger_entry_ids": [],
            "evidence_span_ids": ["span_aaa"],
            "rationale": "noop",
            "decided_by": {"agent": "r", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
            "decided_at": T_DECIDED,
            "confidence": "high", "risk_class": "low",
            "checks": {"provenance": "pass"},
            "metadata": {},
        }
        text = text_for_record_type(reducer)
        self.assertIn("decision_type: archive", text)
        self.assertIn("rationale: noop", text)


class TestDeterminism(unittest.TestCase):
    """Same record content produces the same text and hash."""

    def test_source_record_dict_form_matches_dataclass(self) -> None:
        import dataclasses
        src, _ = build_source_and_span()
        d = dataclasses.asdict(src)
        ei_dc = record_to_embedding_input(src)
        ei_dict = record_to_embedding_input(d)
        self.assertEqual(ei_dc.text, ei_dict.text)
        self.assertEqual(ei_dc.content_hash_sha256, ei_dict.content_hash_sha256)
        self.assertEqual(ei_dc.record_type, ei_dict.record_type)

    def test_same_dict_twice_same_hash(self) -> None:
        d = _build_source_dict()
        e1 = record_to_embedding_input(d)
        e2 = record_to_embedding_input(d)
        self.assertEqual(e1.text, e2.text)
        self.assertEqual(e1.content_hash_sha256, e2.content_hash_sha256)


class TestTruncation(unittest.TestCase):
    """Truncation works at the sentence boundary when possible."""

    def test_short_text_not_truncated(self) -> None:
        ei = record_to_embedding_input(_build_source_dict())
        self.assertFalse(ei.truncated)
        self.assertNotIn("...[truncated]", ei.text)

    def test_long_text_truncated(self) -> None:
        long_text = ". ".join(["This is a long sentence about memory integrity."] * 200)
        d = _build_fact_dict(_build_source_dict()["id"], "span_aaaa" + "a" * 19)
        d["fact_text"] = long_text
        ei = record_to_embedding_input(d, max_chars=500)
        self.assertTrue(ei.truncated)
        self.assertLessEqual(len(ei.text), 500 + len("...[truncated]"))
        self.assertIn("...[truncated]", ei.text)

    def test_truncation_cuts_at_sentence_boundary(self) -> None:
        long_title = ("This is a sentence. " * 50) + "FINAL."
        d = _build_source_dict()
        d["title"] = long_title
        ei = record_to_embedding_input(d, max_chars=200)
        self.assertTrue(ei.truncated)
        body = ei.text[: -len("...[truncated]")].rstrip()
        self.assertTrue(body.endswith((".", "!", "?", "\n")))

    def test_max_chars_must_be_positive(self) -> None:
        with self.assertRaises(ValueError):
            record_to_embedding_input(_build_source_dict(), max_chars=0)


class TestPrivacyClassSurfaced(unittest.TestCase):
    """The privacy class is surfaced on the EmbeddingInput."""

    def test_internal_default(self) -> None:
        # A record with no privacy_class.
        record = {"id": "x_1", "title": "t", "schema_version": "1.0.0"}
        ei = record_to_embedding_input(record)
        self.assertEqual(ei.privacy_class, "internal")

    def test_privacy_class_from_record(self) -> None:
        # The fixture source has privacy_class="internal".
        ei = record_to_embedding_input(_build_source_dict())
        self.assertEqual(ei.privacy_class, "internal")

    def test_highly_sensitive_surfaced(self) -> None:
        d = _build_source_dict()
        d["privacy_class"] = "highly_sensitive"
        ei = record_to_embedding_input(d)
        self.assertEqual(ei.privacy_class, "highly_sensitive")


class TestMetadataStructure(unittest.TestCase):
    """The metadata field is flat with primitive values."""

    def test_metadata_includes_universal_keys(self) -> None:
        ei = record_to_embedding_input(_build_source_dict())
        self.assertIn("record_id", ei.metadata)
        self.assertIn("record_type", ei.metadata)
        self.assertIn("privacy_class", ei.metadata)
        self.assertIn("plane", ei.metadata)

    def test_metadata_includes_per_type_extras(self) -> None:
        ei = record_to_embedding_input(_build_source_dict())
        # source_type is a per-type extra for SourceRecord.
        self.assertIn("source_type", ei.metadata)
        self.assertEqual(ei.metadata["source_type"], "chatgpt_conversation")

    def test_metadata_values_are_primitive(self) -> None:
        ei = record_to_embedding_input(_build_source_dict())
        for v in ei.metadata.values():
            self.assertIsInstance(v, (str, int, float, bool))


class TestRoundTrip(unittest.TestCase):
    """to_dict / from_dict round-trips cleanly."""

    def test_round_trip(self) -> None:
        ei = record_to_embedding_input(_build_source_dict())
        d = embedding_input_to_dict(ei)
        ei2 = embedding_input_from_dict(d)
        self.assertEqual(ei, ei2)

    def test_round_trip_with_truncation(self) -> None:
        d = _build_source_dict()
        d["title"] = "x" * 1000
        ei = record_to_embedding_input(d, max_chars=10)
        d2 = embedding_input_to_dict(ei)
        ei2 = embedding_input_from_dict(d2)
        self.assertEqual(ei, ei2)
        self.assertTrue(ei2.truncated)

    def test_round_trip_is_json_serializable(self) -> None:
        ei = record_to_embedding_input(_build_source_dict())
        d = embedding_input_to_dict(ei)
        # Should serialize cleanly.
        s = json.dumps(d, sort_keys=True)
        self.assertIn("record_id", s)
        d2 = json.loads(s)
        ei2 = embedding_input_from_dict(d2)
        self.assertEqual(ei, ei2)


class TestEmptyRecord(unittest.TestCase):
    """An empty record raises ValueError."""

    def test_empty_dict_raises(self) -> None:
        with self.assertRaises(ValueError):
            record_to_embedding_input({})

    def test_none_record_raises(self) -> None:
        with self.assertRaises(ValueError):
            record_to_embedding_input(None)


class TestPublicApi(unittest.TestCase):
    """All v1.0.0-alpha.1 names are exported."""

    def test_v100a1_exports_present(self) -> None:
        import agent_memory_contracts as a
        for name in (
            "EmbeddingInput",
            "record_to_embedding_input",
            "text_for_record_type",
            "embedding_input_to_dict",
            "embedding_input_from_dict",
            "DEFAULT_MAX_CHARS",
        ):
            self.assertTrue(hasattr(a, name), f"missing export: {name}")

    def test_version_is_stable(self) -> None:
        import agent_memory_contracts as a
        from packaging.version import Version
        v = Version(a.__version__)
        # v1.0.0 is the first stable release.
        self.assertEqual(v.major, 1)
        self.assertEqual(v.minor, 0)
        self.assertEqual(v.micro, 0)
        self.assertFalse(v.is_prerelease)
        self.assertFalse(v.is_devrelease)


class TestIntegrationWithAccess(unittest.TestCase):
    """The privacy-class surfacing composes with BundleScope from v0.9.0."""

    def test_scope_filter_then_embed(self) -> None:
        from agent_memory_contracts import scope_bundle, team_scope
        # A bundle with one public source and one highly_sensitive
        # source. After team_scope (max=internal), only the
        # public source remains, and its EmbeddingInput
        # surfaces the privacy class.
        public = _build_source_dict()
        sensitive = _build_source_dict()
        sensitive["id"] = "src_" + "b" * 24
        sensitive["title"] = "Sensitive"
        sensitive["privacy_class"] = "highly_sensitive"
        bundle = [public, sensitive]
        filtered, _ = scope_bundle(bundle, team_scope())
        self.assertEqual(len(filtered), 1)
        ei = record_to_embedding_input(filtered[0])
        # The filtered record is the public one (with
        # privacy_class=internal, the default in the fixture).
        self.assertIn(ei.privacy_class, ("public", "internal"))


if __name__ == "__main__":
    unittest.main()
