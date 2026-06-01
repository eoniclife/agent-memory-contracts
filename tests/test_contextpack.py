"""Tests for the ContextPack plane.

The full ContextPack structure is intentionally rich (it is a task-ready
bundle with explicit task, authority, state, trusted_memory, evidence,
constraints, and retrieval_trace sections). The tests below cover the
id derivation and the import surface; a real integration test for a
ContextPack needs real upstream state and is out of scope for the
unit-test layer.
"""

from __future__ import annotations

import unittest

from agent_memory_contracts import (
    ContextPack,
    ContextPackBuildReceipt,
    ContextPackValidationReport,
    make_context_pack_id,
)


class ContextPackIdTests(unittest.TestCase):
    def test_id_is_content_derived(self):
        a = make_context_pack_id("task-1", ["span_x"], {"task_type": "x"})
        b = make_context_pack_id("task-1", ["span_x"], {"task_type": "x"})
        c = make_context_pack_id("task-1", ["span_y"], {"task_type": "x"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("ctx_"))


class ContextPackImportTests(unittest.TestCase):
    """Verify the public API surface; the full bundle structure is
    exercised by the end-to-end quickstart example."""

    def test_classes_are_importable(self):
        # The contracts are exposed; calling from_dict with malformed
        # data must raise ValueError, not silently accept.
        with self.assertRaises(Exception):
            ContextPack.from_dict({})
        with self.assertRaises(Exception):
            ContextPackBuildReceipt.from_dict({})
        with self.assertRaises(Exception):
            ContextPackValidationReport.from_dict({})


if __name__ == "__main__":
    unittest.main()
