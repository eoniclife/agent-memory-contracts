"""Internal supersession graph helpers."""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence


class SupersessionRecord(Protocol):
    """Small structural shape shared by trusted supersession records."""

    @property
    def id(self) -> str:
        """Stable record id."""
        ...

    @property
    def superseded_by(self) -> Sequence[str]:
        """Successors that supersede this record."""
        ...


def validate_acyclic_supersession_graph(
    records_by_id: Mapping[str, SupersessionRecord],
    *,
    record_kind: str,
) -> None:
    """Reject cycles in a supersession graph.

    Existence, reciprocity, and temporal handoff checks live in the
    plane-specific validators. This helper only enforces the graph property
    that supersession forms a directed acyclic history.
    """
    visiting: set[str] = set()
    visited: set[str] = set()
    path: list[str] = []

    def visit(record_id: str) -> None:
        if record_id in visited:
            return
        if record_id in visiting:
            cycle_start = path.index(record_id)
            cycle = path[cycle_start:] + [record_id]
            raise ValueError(
                f"{record_kind} supersession cycle detected: "
                + " -> ".join(cycle)
            )

        record = records_by_id.get(record_id)
        if record is None:
            return

        visiting.add(record_id)
        path.append(record_id)
        for next_id in record.superseded_by:
            if next_id in records_by_id:
                visit(next_id)
        path.pop()
        visiting.remove(record_id)
        visited.add(record_id)

    for record_id in sorted(records_by_id):
        visit(record_id)
