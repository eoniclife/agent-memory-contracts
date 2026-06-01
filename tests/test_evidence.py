"""Tests for the evidence plane: SourceRecord, EpisodeRecord, EvidenceSpan,
and their id helpers."""

from __future__ import annotations

import unittest

from agent_memory_contracts import (
    EvidenceSpan,
    EpisodeRecord,
    SourceRecord,
    make_episode_id,
    make_source_id,
    make_span_id,
    sha256_hex,
)

from .fixtures import build_source_and_span


class SourceRecordTests(unittest.TestCase):
    def test_id_is_content_derived(self):
        a = make_source_id("chatgpt_conversation", "uri:1", "f" * 64)
        b = make_source_id("chatgpt_conversation", "uri:1", "f" * 64)
        c = make_source_id("chatgpt_conversation", "uri:2", "f" * 64)
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("src_"))

    def test_id_excludes_volatile_fields(self):
        # source_id is deliberately stable across captured_at/parser_version
        a = make_source_id("chatgpt_conversation", "uri:1", "f" * 64)
        # Calling with the same canonical args should produce the same id,
        # because captured_at/parser_version are not part of the payload.
        b = make_source_id("chatgpt_conversation", "uri:1", "f" * 64)
        self.assertEqual(a, b)

    def test_sha256_helper(self):
        self.assertEqual(len(sha256_hex("hello")), 64)
        # Known vector
        self.assertEqual(sha256_hex(""), "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")

    def test_source_record_validates(self):
        source, _ = build_source_and_span()
        self.assertEqual(source.privacy_class, "internal")
        self.assertEqual(source.source_type, "chatgpt_conversation")

    def test_source_record_rejects_bad_privacy_class(self):
        _, span = build_source_and_span()
        bad = {
            "id": span.source_id,
            "schema_version": "1.0.0",
            "source_type": "chatgpt_conversation",
            "title": "x",
            "origin_uri": None,
            "raw_ref": {"kind": "synthetic_fixture", "value": "x"},
            "content_hash_sha256": "a" * 64,
            "captured_at": "2026-05-30T12:00:00Z",
            "observed_at": None,
            "author_or_sender": None,
            "participants": [],
            "privacy_class": "made_up_class",
            "custody_status": "synthetic",
            "parser_version": "v1",
            "metadata": {},
        }
        with self.assertRaises(ValueError):
            SourceRecord.from_dict(bad)


class EpisodeRecordTests(unittest.TestCase):
    def test_id_is_content_derived(self):
        source_id = "src_" + "a" * 24
        a = make_episode_id(source_id, "conversation_segment", "line_range", "1-5")
        b = make_episode_id(source_id, "conversation_segment", "line_range", "1-5")
        c = make_episode_id(source_id, "conversation_segment", "line_range", "1-6")
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)

    def test_episode_record_validates_and_references_source(self):
        source, _ = build_source_and_span()
        episode_id = make_episode_id(
            source.id, "conversation_segment", "line_range", "1-20"
        )
        episode = EpisodeRecord.from_dict({
            "id": episode_id,
            "schema_version": "1.0.0",
            "source_id": source.id,
            "episode_type": "conversation_segment",
            "episode_locator": {"kind": "line_range", "value": "1-20"},
            "title": "Memory kernel design discussion",
            "summary": "Discussed separation of evidence, candidate, and ledger.",
            "event_time_start": "2026-05-30T12:00:00Z",
            "event_time_end": "2026-05-30T12:30:00Z",
            "actors": ["user@example.com"],
            "topics": ["memory", "schemas"],
            "project_refs": [],
            "evidence_span_ids": [],
            "metadata": {},
        })
        self.assertEqual(episode.source_id, source.id)


class EvidenceSpanTests(unittest.TestCase):
    def test_id_is_content_derived(self):
        source_id = "src_" + "a" * 24
        a = make_span_id(source_id, "line_range", "1-5")
        b = make_span_id(source_id, "line_range", "1-5")
        self.assertEqual(a, b)
        self.assertTrue(a.startswith("span_"))

    def test_evidence_span_validates(self):
        source, span = build_source_and_span()
        self.assertEqual(span.source_id, source.id)
        self.assertEqual(span.excerpt_policy, "none")

    def test_evidence_span_with_excerpt_requires_policy(self):
        source, _ = build_source_and_span()
        span_id = make_span_id(source.id, "line_range", "20-25")
        # text_excerpt is set but excerpt_policy is "none" -> should fail
        with self.assertRaises(ValueError):
            EvidenceSpan.from_dict({
                "id": span_id,
                "schema_version": "1.0.0",
                "source_id": source.id,
                "episode_id": None,
                "locator": {"kind": "line_range", "value": "20-25"},
                "text_excerpt": "Some text here",
                "excerpt_policy": "none",
                "span_hash_sha256": "c" * 64,
                "privacy_class": "internal",
                "metadata": {},
            })


if __name__ == "__main__":
    unittest.main()
