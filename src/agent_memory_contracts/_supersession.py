"""Internal supersession graph helpers."""

from __future__ import annotations

from typing import Mapping, Protocol, Sequence


class SupersessionRecord(Protocol):
    """Small structural shape shared by trusted supersession records."""

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
    def successors(record_id: str) -> tuple[str, ...]:
        record = records_by_id[record_id]
        return tuple(
            sorted(
                next_id
                for next_id in record.superseded_by
                if next_id in records_by_id
            )
        )

    def canonical_cycle(cycle: list[str]) -> list[str]:
        if len(cycle) <= 2:
            return cycle
        body = cycle[:-1]
        best = min(range(len(body)), key=body.__getitem__)
        rotated = body[best:] + body[:best]
        return rotated + [rotated[0]]

    # 0/absent = unseen, 1 = visiting, 2 = visited.
    state: dict[str, int] = {}
    path: list[str] = []
    path_position: dict[str, int] = {}

    def raise_cycle(record_id: str) -> None:
        cycle_start = path_position[record_id]
        cycle = canonical_cycle(path[cycle_start:] + [record_id])
        raise ValueError(
            f"{record_kind} supersession cycle detected: "
            + " -> ".join(cycle)
        )

    for record_id in sorted(records_by_id):
        if state.get(record_id) == 2:
            continue

        state[record_id] = 1
        path_position[record_id] = len(path)
        path.append(record_id)
        stack: list[tuple[str, tuple[str, ...], int]] = [
            (record_id, successors(record_id), 0)
        ]

        while stack:
            current_id, current_successors, index = stack[-1]
            if index >= len(current_successors):
                stack.pop()
                state[current_id] = 2
                path.pop()
                del path_position[current_id]
                continue

            next_id = current_successors[index]
            stack[-1] = (current_id, current_successors, index + 1)
            next_state = state.get(next_id, 0)
            if next_state == 2:
                continue
            if next_state == 1:
                raise_cycle(next_id)

            state[next_id] = 1
            path_position[next_id] = len(path)
            path.append(next_id)
            stack.append((next_id, successors(next_id), 0))
