"""Tests for the LangChain integration.

The integration is optional; tests are gated on whether
``langchain_classic`` is importable. When it is not, the
tests skip with a clear message.
"""

from __future__ import annotations

import unittest
from typing import Any

import pytest

pytest.importorskip("langchain_classic.base_memory")

from langchain_classic.base_memory import BaseMemory  # noqa: E402

from agent_memory_contracts import (  # noqa: E402
    EpisodeRecord,
    EvidenceSpan,
    SourceRecord,
)
from agent_memory_contracts.integrations.langchain import (  # noqa: E402
    ContractsMemory,
    ContractsMemoryConfig,
    MemoryStore,
)


class TestContractsMemoryIsBaseMemory(unittest.TestCase):
    """``ContractsMemory`` is a real ``BaseMemory`` subclass."""

    def test_subclass_of_basememory(self) -> None:
        self.assertTrue(issubclass(ContractsMemory, BaseMemory))

    def test_memory_variables_returns_context_pack(self) -> None:
        m = ContractsMemory(session_id="s1")
        self.assertEqual(m.memory_variables, ["context_pack"])

    def test_session_id_defaults_to_uuid(self) -> None:
        m = ContractsMemory()
        self.assertTrue(m.session_id.startswith("sess_"))
        self.assertGreater(len(m.session_id), 5)

    def test_session_id_propagates(self) -> None:
        m = ContractsMemory(session_id="my-session")
        self.assertEqual(m.session_id, "my-session")


class TestSaveAndLoad(unittest.TestCase):
    """save_context records; load_memory_variables returns the context_pack."""

    @staticmethod
    def _validate_trace_records(records: list[dict[str, Any]]) -> None:
        for record in records:
            if record.get("source_type") is not None:
                SourceRecord.from_dict(record)
            elif record.get("episode_type") is not None:
                EpisodeRecord.from_dict(record)
            elif record.get("span_hash_sha256") is not None:
                EvidenceSpan.from_dict(record)

    def test_empty_session_returns_empty_context_pack(self) -> None:
        m = ContractsMemory(session_id="empty")
        result = m.load_memory_variables({"input": "x"})
        self.assertIn("context_pack", result)
        self.assertIsNone(result["context_pack"]["context_pack_id"])

    def test_save_then_load_returns_one_record(self) -> None:
        m = ContractsMemory(session_id="sess1")
        m.save_context({"input": "Hi"}, {"response": "Hello!"})
        result = m.load_memory_variables({"input": "followup"})
        cp = result["context_pack"]
        self.assertEqual(cp["session_id"], "sess1")
        self.assertEqual(len(cp["records"]), 1)
        self.assertEqual(len(cp["evidence"]), 2)  # input + output
        self.assertEqual(len(cp["sources"]), 1)

    def test_save_context_records_session_trace_not_trusted_facts(self) -> None:
        m = ContractsMemory(session_id="sess-trace")
        m.save_context({"input": "Hi"}, {"response": "Hello!"})

        records = m.store.get_merged(m.session_id)
        self._validate_trace_records(records)

        episodes = [r for r in records if r.get("episode_type") == "conversation_segment"]
        spans = [r for r in records if r.get("episode_id") is not None]
        sources = [
            r for r in records
            if r.get("metadata", {}).get("integration") == "langchain"
        ]
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(episodes), 1)
        self.assertEqual(len(spans), 2)
        self.assertEqual(
            set(episodes[0]["evidence_span_ids"]),
            {span["id"] for span in spans},
        )
        self.assertEqual(
            sum(1 for r in records if r.get("episode_id") is not None),
            2,
        )
        self.assertFalse(any("ledger_type" in r for r in records))
        self.assertFalse(any("reducer_decision_id" in r for r in records))
        self.assertFalse(any("decision_type" in r for r in records))

    def test_load_returns_trace_envelope_not_context_pack_record(self) -> None:
        m = ContractsMemory(session_id="sess-envelope")
        m.save_context({"input": "Hi"}, {"response": "Hello!"})

        cp = m.load_memory_variables({"input": "followup"})["context_pack"]

        self.assertEqual(cp["metadata"]["envelope_type"], "langchain_session_trace")
        self.assertEqual(cp["session_id"], "sess-envelope")
        for forbidden in (
            "id",
            "pack_type",
            "task",
            "authority",
            "state",
            "trusted_memory",
            "constraints",
            "retrieval_trace",
            "build_receipt",
            "validation_report",
        ):
            self.assertNotIn(forbidden, cp)

    def test_configured_privacy_class_applies_to_generated_trace(self) -> None:
        cfg = ContractsMemoryConfig(privacy_class="private")
        m = ContractsMemory(session_id="sess-private", config=cfg)
        m.save_context({"input": "Hi"}, {"response": "Hello!"})

        records = m.store.get_merged(m.session_id)
        self._validate_trace_records(records)
        classified = [
            r["privacy_class"]
            for r in records
            if r.get("metadata", {}).get("integration") == "langchain"
            or r.get("episode_id") is not None
        ]

        self.assertEqual(classified, ["private", "private", "private"])

    def test_two_turns_yield_two_records(self) -> None:
        m = ContractsMemory(session_id="sess2")
        m.save_context({"input": "Q1"}, {"response": "A1"})
        m.save_context({"input": "Q2"}, {"response": "A2"})
        result = m.load_memory_variables({"input": "followup"})
        self.assertEqual(len(result["context_pack"]["records"]), 2)

    def test_max_records_per_load_caps_episodes(self) -> None:
        cfg = ContractsMemoryConfig(max_records_per_load=1)
        m = ContractsMemory(session_id="sess3", config=cfg)
        for i in range(3):
            m.save_context({"input": f"Q{i}"}, {"response": f"A{i}"})
        result = m.load_memory_variables({"input": "followup"})
        self.assertEqual(len(result["context_pack"]["records"]), 1)

    def test_clear_empties_session(self) -> None:
        m = ContractsMemory(session_id="sess4")
        m.save_context({"input": "Hi"}, {"response": "Hello!"})
        m.clear()
        result = m.load_memory_variables({"input": "x"})
        self.assertIsNone(result["context_pack"]["context_pack_id"])


class TestMemoryStore(unittest.TestCase):
    """The bundle store: put, get, evict, session isolation."""

    @staticmethod
    def _bundle(*record_ids: str) -> dict[str, Any]:
        """Build a bundle dict with a single source_record per id."""
        return {"source_records": [{"id": rid, "source_type": "x"} for rid in record_ids]}

    def test_put_and_get_all(self) -> None:
        store = MemoryStore()
        store.put("s1", self._bundle("a", "b"))
        store.put("s1", self._bundle("c"))
        all_bundles = store.get_all("s1")
        self.assertEqual(len(all_bundles), 2)

    def test_get_merged_dedupes_by_id(self) -> None:
        store = MemoryStore()
        store.put("s1", self._bundle("a"))
        store.put("s1", self._bundle("a", "b"))
        merged = store.get_merged("s1")
        ids = [r["id"] for r in merged]
        self.assertEqual(sorted(ids), ["a", "b"])

    def test_session_isolation(self) -> None:
        store = MemoryStore()
        store.put("s1", self._bundle("a"))
        store.put("s2", self._bundle("b"))
        self.assertEqual([r["id"] for r in store.get_merged("s1")], ["a"])
        self.assertEqual([r["id"] for r in store.get_merged("s2")], ["b"])

    def test_clear_session(self) -> None:
        store = MemoryStore()
        store.put("s1", self._bundle("a"))
        store.clear_session("s1")
        self.assertEqual(store.get_merged("s1"), [])

    def test_max_bundles_evicts_oldest(self) -> None:
        store = MemoryStore(max_bundles=2)
        store.put("s1", self._bundle("a"))
        store.put("s1", self._bundle("b"))
        store.put("s1", self._bundle("c"))
        all_bundles = store.get_all("s1")
        self.assertEqual(len(all_bundles), 2)

    def test_max_bundles_must_be_positive(self) -> None:
        for value in (0, -1, False, "2"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "max_bundles"):
                    MemoryStore(max_bundles=value)  # type: ignore[arg-type]

    def test_shared_store_across_memory_instances(self) -> None:
        store = MemoryStore()
        m1 = ContractsMemory(session_id="shared", store=store)
        m2 = ContractsMemory(session_id="shared", store=store)
        m1.save_context({"input": "Q"}, {"response": "A"})
        # m2 sees m1's records through the shared store
        result = m2.load_memory_variables({"input": "followup"})
        self.assertEqual(len(result["context_pack"]["records"]), 1)

    def test_shared_store_writes_allocate_distinct_turns(self) -> None:
        store = MemoryStore()
        m1 = ContractsMemory(session_id="shared-turns", store=store)
        m2 = ContractsMemory(session_id="shared-turns", store=store)

        m1.save_context({"input": "Q1"}, {"response": "A1"})
        m2.save_context({"input": "Q2"}, {"response": "A2"})

        cp = m1.load_memory_variables({"input": "followup"})["context_pack"]
        self.assertEqual(len(cp["records"]), 2)
        self.assertEqual(len(cp["evidence"]), 4)
        self.assertEqual(
            [record["metadata"]["turn_index"] for record in cp["records"]],
            [0, 1],
        )
        self.assertEqual(
            [span["text_excerpt"] for span in cp["evidence"]],
            ["Q1", "{'response': 'A1'}", "Q2", "{'response': 'A2'}"],
        )

    def test_shared_store_rejects_privacy_class_conflict(self) -> None:
        store = MemoryStore()
        m1 = ContractsMemory(
            session_id="shared-privacy",
            config=ContractsMemoryConfig(privacy_class="private"),
            store=store,
        )
        m2 = ContractsMemory(
            session_id="shared-privacy",
            config=ContractsMemoryConfig(privacy_class="public"),
            store=store,
        )
        m1.save_context({"input": "Q"}, {"response": "A"})

        with self.assertRaisesRegex(ValueError, "privacy_class"):
            m2.save_context({"input": "Q2"}, {"response": "A2"})

    def test_session_count(self) -> None:
        store = MemoryStore()
        store.put("s1", [{"id": "a"}])
        store.put("s2", [{"id": "b"}])
        self.assertEqual(store.session_count(), 2)


class TestContractsMemoryConfig(unittest.TestCase):
    """The config dataclass has the right defaults."""

    def test_defaults(self) -> None:
        cfg = ContractsMemoryConfig()
        self.assertEqual(cfg.privacy_class, "internal")
        self.assertEqual(cfg.max_bundles, 100)
        self.assertEqual(cfg.max_records_per_load, 20)

    def test_custom_privacy_class(self) -> None:
        cfg = ContractsMemoryConfig(privacy_class="private")
        self.assertEqual(cfg.privacy_class, "private")

    def test_invalid_privacy_class_fails_closed(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid privacy_class"):
            ContractsMemoryConfig(privacy_class="customer")  # type: ignore[arg-type]

    def test_max_bundles_must_be_positive(self) -> None:
        for value in (0, -1, False, "100"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "max_bundles"):
                    ContractsMemoryConfig(max_bundles=value)  # type: ignore[arg-type]

    def test_max_records_per_load_must_be_positive(self) -> None:
        for value in (0, -1, False, "20"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "max_records_per_load"):
                    ContractsMemoryConfig(
                        max_records_per_load=value  # type: ignore[arg-type]
                    )


class TestLangchainCompatibility(unittest.TestCase):
    """The integration is drop-in for LangChain chains.

    This is a smoke test using a minimal in-memory chain
    that uses ``memory_variables`` and ``save_context``.
    """

    def test_chain_pattern(self) -> None:
        # A minimal stand-in for a LangChain chain.
        class _MiniChain:
            def __init__(self, memory: BaseMemory) -> None:
                self.memory = memory

            def step(self, user_input: str) -> str:
                mem = self.memory.load_memory_variables({"input": user_input})
                response = f"ack: {user_input} (saw {len(mem['context_pack'].get('records', []))} turns)"
                self.memory.save_context(
                    {"input": user_input}, {"response": response}
                )
                return response

        m = ContractsMemory(session_id="chain-test")
        chain = _MiniChain(m)
        chain.step("hello")
        chain.step("world")
        # 2 turns recorded
        result = m.load_memory_variables({"input": "summary"})
        self.assertEqual(len(result["context_pack"]["records"]), 2)


if __name__ == "__main__":
    unittest.main()
