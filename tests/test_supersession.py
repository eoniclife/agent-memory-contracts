"""Tests for shared supersession graph invariants."""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from agent_memory_contracts._supersession import (
    validate_acyclic_supersession_graph,
)


@dataclass(frozen=True)
class _Record:
    superseded_by: tuple[str, ...] = ()


def _chain(length: int, *, cycle: bool = False) -> dict[str, _Record]:
    records: dict[str, _Record] = {}
    for index in range(length):
        record_id = f"node_{index:05d}"
        if index + 1 < length:
            successors = (f"node_{index + 1:05d}",)
        elif cycle:
            successors = ("node_00000",)
        else:
            successors = ()
        records[record_id] = _Record(successors)
    return records


class SupersessionGraphTests(unittest.TestCase):
    def test_long_acyclic_chain_does_not_recurse(self):
        validate_acyclic_supersession_graph(
            _chain(5_000),
            record_kind="record",
        )

    def test_long_cycle_raises_contract_error_not_recursion_error(self):
        with self.assertRaisesRegex(
            ValueError,
            "record supersession cycle detected",
        ):
            validate_acyclic_supersession_graph(
                _chain(5_000, cycle=True),
                record_kind="record",
            )

    def test_self_loop_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "record supersession cycle detected: node_a -> node_a",
        ):
            validate_acyclic_supersession_graph(
                {"node_a": _Record(("node_a",))},
                record_kind="record",
            )

    def test_cycle_error_is_canonical_across_successor_order(self):
        graph_ab_first = {
            "node_a": _Record(("node_b", "node_c")),
            "node_b": _Record(("node_a",)),
            "node_c": _Record(("node_a",)),
        }
        graph_ac_first = {
            "node_a": _Record(("node_c", "node_b")),
            "node_b": _Record(("node_a",)),
            "node_c": _Record(("node_a",)),
        }

        errors: list[str] = []
        for graph in (graph_ab_first, graph_ac_first):
            with self.assertRaises(ValueError) as ctx:
                validate_acyclic_supersession_graph(
                    graph,
                    record_kind="record",
                )
            errors.append(str(ctx.exception))

        self.assertEqual(errors[0], errors[1])
        self.assertEqual(
            errors[0],
            "record supersession cycle detected: node_a -> node_b -> node_a",
        )


if __name__ == "__main__":
    unittest.main()
