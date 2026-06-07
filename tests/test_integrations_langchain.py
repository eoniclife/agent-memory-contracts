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

    def test_shared_store_across_memory_instances(self) -> None:
        store = MemoryStore()
        m1 = ContractsMemory(session_id="shared", store=store)
        m2 = ContractsMemory(session_id="shared", store=store)
        m1.save_context({"input": "Q"}, {"response": "A"})
        # m2 sees m1's records through the shared store
        result = m2.load_memory_variables({"input": "followup"})
        self.assertEqual(len(result["context_pack"]["records"]), 1)

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
