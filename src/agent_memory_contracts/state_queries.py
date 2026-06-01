"""Temporal query helpers for state snapshots."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable


def parse_iso8601(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("expected non-empty ISO-8601 string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _active_at(record: dict[str, Any], query_time: str) -> bool:
    if record["status"] != "active":
        return False
    if record.get("superseded_by"):
        return False
    query = parse_iso8601(query_time)
    valid_from = record.get("valid_from")
    valid_until = record.get("valid_until")
    stale_after = record.get("stale_after")
    if valid_from is not None and query < parse_iso8601(valid_from):
        return False
    if valid_until is not None and query >= parse_iso8601(valid_until):
        return False
    if stale_after is not None and query >= parse_iso8601(stale_after):
        return False
    return True


def _valid_as_of(record: dict[str, Any], query_time: str) -> bool:
    if record["status"] in {"retracted", "archived"}:
        return False
    query = parse_iso8601(query_time)
    valid_from = record.get("valid_from")
    valid_until = record.get("valid_until")
    if valid_from is not None and query < parse_iso8601(valid_from):
        return False
    if valid_until is not None and query >= parse_iso8601(valid_until):
        return False
    return True


def is_project_state_active_at(state: dict[str, Any], query_time: str) -> bool:
    return _active_at(state, query_time)


def current_project_states(states: Iterable[dict[str, Any]], query_time: str) -> list[dict[str, Any]]:
    return [state for state in states if is_project_state_active_at(state, query_time)]


def project_state_for_project(project_id: str, states: Iterable[dict[str, Any]], query_time: str) -> dict[str, Any] | None:
    matches = [state for state in current_project_states(states, query_time) if state["project_id"] == project_id]
    if len(matches) > 1:
        raise ValueError(f"multiple active ProjectStateSnapshots for project_id: {project_id}")
    return matches[0] if matches else None


def project_states_as_of(states: Iterable[dict[str, Any]], query_time: str) -> list[dict[str, Any]]:
    return [state for state in states if _valid_as_of(state, query_time)]


def project_state_supersession_chain(state_id: str, states: Iterable[dict[str, Any]]) -> list[str]:
    return _supersession_chain(state_id, states)


def is_core_state_active_at(state: dict[str, Any], query_time: str) -> bool:
    return _active_at(state, query_time)


def current_core_states(states: Iterable[dict[str, Any]], query_time: str) -> list[dict[str, Any]]:
    return [state for state in states if is_core_state_active_at(state, query_time)]


def current_core_state(states: Iterable[dict[str, Any]], query_time: str) -> dict[str, Any] | None:
    matches = current_core_states(states, query_time)
    if len(matches) > 1:
        raise ValueError("multiple active CoreStateSnapshots")
    return matches[0] if matches else None


def core_states_as_of(states: Iterable[dict[str, Any]], query_time: str) -> list[dict[str, Any]]:
    return [state for state in states if _valid_as_of(state, query_time)]


def core_state_supersession_chain(state_id: str, states: Iterable[dict[str, Any]]) -> list[str]:
    return _supersession_chain(state_id, states)


def _supersession_chain(state_id: str, states: Iterable[dict[str, Any]]) -> list[str]:
    by_id = {state["id"]: state for state in states}
    chain = [state_id]
    current = by_id.get(state_id)
    while current and current.get("superseded_by"):
        next_id = current["superseded_by"][0]
        chain.append(next_id)
        current = by_id.get(next_id)
    return chain
