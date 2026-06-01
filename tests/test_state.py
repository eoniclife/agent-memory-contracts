"""Tests for the state plane: ProjectStateSnapshot, CoreStateSnapshot,
StateReducerDecision, and the temporal query helpers."""

from __future__ import annotations

import unittest
from dataclasses import asdict

from agent_memory_contracts import (
    ProjectStateSnapshot,
    make_project_state_id,
    make_state_reducer_decision_id,
    project_state_from_dict,
    project_state_for_project,
    project_state_supersession_chain,
    current_project_states,
    is_project_state_active_at,
    StateReducerDecision,
)

from .fixtures import T_DECIDED, build_source_and_span


def _project_state(project_id: str, span_id: str, as_of: str, summary: str) -> ProjectStateSnapshot:
    # The id is content-derived from the same fields the validator uses
    # in semantic_payload(): 14 fields including sorted evidence_span_ids.
    pid = make_project_state_id(project_id, as_of, [span_id], {
        "project_id": project_id,
        "project_name": f"Project {project_id}",
        "project_status": "active",
        "as_of": as_of,
        "current_objective": "Ship the memory contracts library",
        "current_strategy": "Schema packs as sidecars to upstream memory substrates",
        "current_priorities": ["schemas", "validators", "tests"],
        "active_blockers": [],
        "open_questions": [],
        "active_fact_ids": [],
        "active_preference_ids": [],
        "active_decision_ids": [],
        "active_taste_card_ids": [],
        "evidence_span_ids": [span_id],
    })
    return ProjectStateSnapshot.from_dict({
        "id": pid,
        "schema_version": "1.0.0",
        "state_type": "project_state",
        "status": "active",
        "project_id": project_id,
        "project_name": f"Project {project_id}",
        "project_status": "active",
        "as_of": as_of,
        "summary": summary,
        "current_objective": "Ship the memory contracts library",
        "current_strategy": "Schema packs as sidecars to upstream memory substrates",
        "current_priorities": ["schemas", "validators", "tests"],
        "active_blockers": [],
        "open_questions": [],
        "next_actions": ["open PR for Codex for OSS"],
        "active_fact_ids": [],
        "active_preference_ids": [],
        "active_decision_ids": [],
        "active_taste_card_ids": [],
        "candidate_task_ids": [],
        "source_record_ids": [],
        "episode_record_ids": [],
        "evidence_span_ids": [span_id],
        "reducer_decision_id": "redstate_" + "a" * 24,
        "valid_from": as_of,
        "valid_until": None,
        "stale_after": None,
        "created_at": T_DECIDED,
        "updated_at": T_DECIDED,
        "supersedes": [],
        "superseded_by": [],
        "metadata": {"human_asserted": True},
    })


class StateIdTests(unittest.TestCase):
    def test_id_is_content_derived(self):
        a = make_project_state_id("p1", "2026-05-30T00:00:00Z", ["span_x"], {"summary": "x"})
        b = make_project_state_id("p1", "2026-05-30T00:00:00Z", ["span_x"], {"summary": "x"})
        c = make_project_state_id("p1", "2026-05-30T00:00:00Z", ["span_x"], {"summary": "y"})
        self.assertEqual(a, b)
        self.assertNotEqual(a, c)
        self.assertTrue(a.startswith("projstate_"))


class ProjectStateTests(unittest.TestCase):
    def test_validates(self):
        _, span = build_source_and_span()
        s = _project_state("p1", span.id, "2026-05-30T13:00:00Z", "Working on schema extraction")
        self.assertEqual(s.project_id, "p1")
        self.assertEqual(s.state_type, "project_state")

    def test_factory_roundtrip(self):
        _, span = build_source_and_span()
        s = _project_state("p1", span.id, "2026-05-30T13:00:00Z", "x")
        s2 = project_state_from_dict(asdict(s))
        self.assertEqual(s.id, s2.id)


class StateQueryTests(unittest.TestCase):
    def test_current_project_states_filters_inactive(self):
        _, span = build_source_and_span()
        s1 = _project_state("p1", span.id, "2026-05-30T13:00:00Z", "early")
        s2 = _project_state("p1", span.id, "2026-05-30T18:00:00Z", "later")
        # Force s1 to be superseded by s2; supersession requires status=superseded
        # and valid_until set.
        d1 = asdict(s1)
        d1["superseded_by"] = [s2.id]
        d1["status"] = "superseded"
        d1["valid_until"] = "2026-05-30T19:00:00Z"
        s1 = project_state_from_dict(d1)
        current = current_project_states([asdict(s1), asdict(s2)], "2026-05-31T00:00:00Z")
        self.assertEqual([c["id"] for c in current], [s2.id])

    def test_is_project_state_active_at(self):
        _, span = build_source_and_span()
        s = _project_state("p1", span.id, "2026-05-30T13:00:00Z", "x")
        self.assertTrue(is_project_state_active_at(asdict(s), "2026-05-30T18:00:00Z"))
        self.assertFalse(is_project_state_active_at(asdict(s), "2026-05-29T18:00:00Z"))

    def test_project_state_for_project(self):
        _, span = build_source_and_span()
        s1 = _project_state("p1", span.id, "2026-05-30T13:00:00Z", "x")
        s2 = _project_state("p2", span.id, "2026-05-30T13:00:00Z", "y")
        got = project_state_for_project("p1", [asdict(s1), asdict(s2)], "2026-05-30T18:00:00Z")
        self.assertEqual(got["id"], s1.id)
        self.assertIsNone(project_state_for_project("p3", [asdict(s1), asdict(s2)], "2026-05-30T18:00:00Z"))

    def test_project_state_supersession_chain(self):
        _, span = build_source_and_span()
        s1 = _project_state("p1", span.id, "2026-05-30T13:00:00Z", "v1")
        s2 = _project_state("p1", span.id, "2026-05-30T18:00:00Z", "v2")
        d1 = asdict(s1)
        d1["superseded_by"] = [s2.id]
        d1["status"] = "superseded"
        d1["valid_until"] = "2026-05-30T19:00:00Z"
        s1 = project_state_from_dict(d1)
        chain = project_state_supersession_chain(s1.id, [asdict(s1), asdict(s2)])
        self.assertEqual(chain, [s1.id, s2.id])


class StateReducerTests(unittest.TestCase):
    def test_reducer_decision_validates(self):
        rid = make_state_reducer_decision_id(
            "promote", ["projstate_x"], ["corestate_y"], ["span_z"],
            ["fact_1"], ["taste_1"], "ok",
        )
        srd = StateReducerDecision.from_dict({
            "id": rid,
            "schema_version": "1.0.0",
            "decision_type": "promote",
            "target_project_state_ids": ["projstate_x"],
            "target_core_state_ids": ["corestate_y"],
            "source_record_ids": [],
            "episode_record_ids": [],
            "evidence_span_ids": ["span_z"],
            "ledger_entry_ids": ["fact_1"],
            "taste_card_ids": ["taste_1"],
            "rationale": "ok",
            "decided_by": {"agent": "state-reducer", "model": "gpt-5.5", "tool": None, "prompt_ref": None},
            "decided_at": T_DECIDED,
            "confidence": "high",
            "risk_class": "low",
            "checks": {
                "provenance": "pass", "temporal_validity": "pass",
                "state_consistency": "pass", "privacy": "pass", "usefulness": "pass",
            },
            "metadata": {},
        })
        self.assertEqual(srd.decision_type, "promote")


if __name__ == "__main__":
    unittest.main()
