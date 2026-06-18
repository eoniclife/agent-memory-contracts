"""Tests for the audit pack generator (audit.py).

Covers: the happy path (full authorization chains), missing-chain
detection (INCOMPLETE flags instead of crashes), rejections,
supersession changelog, id derivation, round-trip, Markdown golden
checks, and the ``audit`` CLI subcommand's exit codes.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_memory_contracts.audit import (
    CHAIN_COMPLETE,
    CHAIN_INCOMPLETE,
    DEFAULT_AUDIT_TITLE,
    AuditPack,
    audit_pack_to_markdown,
    compute_audit_pack,
)


REPO_ROOT = Path(__file__).parent.parent

T_AS_OF = "2026-06-15T12:00:00Z"


# --- Test fixtures: a small, internally consistent chain ---

def _source(id_str: str = "src_aaaa", *, title: str = "Rate policy v4") -> dict:
    return {
        "id": id_str,
        "schema_version": "1.0.0",
        "source_type": "manual_note",
        "title": title,
        "origin_uri": None,
        "raw_ref": {"kind": "local_path", "value": "policies/v4.md"},
        "content_hash_sha256": "a" * 64,
        "captured_at": "2026-05-18T00:00:00Z",
        "observed_at": "2026-05-18T00:00:00Z",
        "author_or_sender": "cfo@example.com",
        "participants": [],
        "privacy_class": "internal",
        "custody_status": "external_pointer",
        "parser_version": "v1",
        "metadata": {},
    }


def _span(id_str: str = "span_aaaa", *, source_id: str = "src_aaaa",
          excerpt: str = "Grade B rate is 12.75%") -> dict:
    return {
        "id": id_str,
        "schema_version": "1.0.0",
        "source_id": source_id,
        "episode_id": None,
        "locator": {"kind": "line_range", "value": "7-7"},
        "text_excerpt": excerpt,
        "excerpt_policy": "short_quote_allowed",
        "span_hash_sha256": "b" * 64,
        "privacy_class": "internal",
        "metadata": {},
    }


def _candidate(id_str: str = "cand_claim_aaaa") -> dict:
    return {
        "id": id_str,
        "schema_version": "1.0.0",
        "candidate_type": "claim",
        "evidence_span_ids": ["span_aaaa"],
        "claim_text": "Grade B rate is 12.75%",
        "confidence": "high",
        "status": "candidate",
        "metadata": {},
    }


def _decision(id_str: str = "redmem_aaaa", *,
              decision_type: str = "promote",
              target_candidate_ids: list[str] | None = None,
              target_ledger_entry_ids: list[str] | None = None,
              rationale: str = "chain verified",
              checks: dict | None = None,
              metadata: dict | None = None) -> dict:
    return {
        "id": id_str,
        "schema_version": "1.0.0",
        "decision_type": decision_type,
        "target_candidate_ids": (target_candidate_ids
                                 if target_candidate_ids is not None
                                 else ["cand_claim_aaaa"]),
        "target_ledger_entry_ids": (target_ledger_entry_ids
                                    if target_ledger_entry_ids is not None
                                    else ["fact_aaaa"]),
        "evidence_span_ids": ["span_aaaa"],
        "rationale": rationale,
        "decided_by": {"agent": "test-reducer", "model": "deterministic",
                       "tool": None, "prompt_ref": None},
        "decided_at": "2026-06-01T10:00:00Z",
        "confidence": "high",
        "risk_class": "low",
        "checks": checks if checks is not None else {
            "provenance": "pass", "temporal_validity": "pass",
            "contradiction_scan": "pass", "privacy": "pass",
            "usefulness": "pass",
        },
        "metadata": metadata if metadata is not None else {},
    }


def _fact(id_str: str = "fact_aaaa", *,
          reducer_decision_id: str = "redmem_aaaa",
          candidate_ids: list[str] | None = None,
          evidence_span_ids: list[str] | None = None,
          fact_text: str = "Grade B card rate is 12.75% under v4",
          status: str = "active",
          valid_from: str = "2026-05-18T00:00:00Z",
          valid_until: str | None = None,
          supersedes: list[str] | None = None,
          superseded_by: list[str] | None = None,
          **extra) -> dict:
    return {
        "id": id_str,
        "schema_version": "1.0.0",
        "ledger_type": "fact",
        "status": status,
        "confidence": "high",
        "scope": "company",
        "subject": "lap.grade_b.rate",
        "predicate": "rate_is",
        "object": "12.75%",
        "fact_text": fact_text,
        "source_record_ids": ["src_aaaa"],
        "evidence_span_ids": (evidence_span_ids
                              if evidence_span_ids is not None
                              else ["span_aaaa"]),
        "candidate_ids": (candidate_ids if candidate_ids is not None
                          else ["cand_claim_aaaa"]),
        "reducer_decision_id": reducer_decision_id,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "stale_after": None,
        "supersedes": supersedes if supersedes is not None else [],
        "superseded_by": superseded_by if superseded_by is not None else [],
        "metadata": {},
        **extra,
    }


def _full_chain_bundle() -> list[dict]:
    """Source -> span -> candidate -> decision -> fact, complete."""
    return [
        _source(),
        _span(),
        _candidate(),
        _decision(),
        _fact(),
    ]


# --- compute_audit_pack: happy path ---

class AuditPackHappyPathTests(unittest.TestCase):
    def test_complete_chain(self):
        pack = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        self.assertEqual(pack.ledger_entry_count, 1)
        self.assertEqual(pack.complete_chain_count, 1)
        self.assertEqual(pack.incomplete_chain_count, 0)
        entry = pack.entries[0]
        self.assertEqual(entry.chain_status, CHAIN_COMPLETE)
        self.assertEqual(entry.missing, [])
        self.assertEqual(entry.ledger_entry_id, "fact_aaaa")
        self.assertEqual(entry.reducer_decision_id, "redmem_aaaa")
        self.assertEqual(entry.decision_type, "promote")
        self.assertEqual(entry.decided_by_agent, "test-reducer")
        self.assertEqual(entry.rationale, "chain verified")
        self.assertEqual(entry.candidate_ids, ["cand_claim_aaaa"])
        self.assertEqual(len(entry.evidence), 1)
        ev = entry.evidence[0]
        self.assertTrue(ev.resolved)
        self.assertEqual(ev.span_id, "span_aaaa")
        self.assertEqual(ev.source_id, "src_aaaa")
        self.assertEqual(ev.source_title, "Rate policy v4")
        self.assertEqual(ev.locator, "line_range 7-7")
        self.assertEqual(ev.excerpt, "Grade B rate is 12.75%")

    def test_counts_by_plane(self):
        pack = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        self.assertEqual(pack.total_records, 5)
        self.assertEqual(pack.source_count, 1)
        self.assertEqual(pack.evidence_span_count, 1)
        self.assertEqual(pack.candidate_count, 1)
        self.assertEqual(pack.reducer_decision_count, 1)
        self.assertEqual(pack.rejected_count, 0)
        self.assertEqual(pack.supersession_count, 0)

    def test_default_title(self):
        pack = compute_audit_pack([], as_of=T_AS_OF)
        self.assertEqual(pack.title, DEFAULT_AUDIT_TITLE)
        self.assertIn("Every fact your AI relies on", pack.title)

    def test_custom_title(self):
        pack = compute_audit_pack([], as_of=T_AS_OF, title="Q2 audit")
        self.assertEqual(pack.title, "Q2 audit")

    def test_bundle_fingerprint_matches_input(self):
        from agent_memory_contracts import bundle_fingerprint
        bundle = _full_chain_bundle()
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        self.assertEqual(pack.bundle_fingerprint,
                         bundle_fingerprint(bundle))

    def test_rejections_collected(self):
        bundle = _full_chain_bundle() + [
            _candidate("cand_claim_bad"),
            _decision("redmem_reject", decision_type="reject",
                      target_candidate_ids=["cand_claim_bad"],
                      target_ledger_entry_ids=[],
                      rationale="untrusted_source: spoofed sender",
                      checks={"provenance": "fail",
                              "temporal_validity": "pass",
                              "contradiction_scan": "pass",
                              "privacy": "pass", "usefulness": "pass"},
                      metadata={"auto_rejected": True}),
        ]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        self.assertEqual(pack.rejected_count, 1)
        rejection = pack.rejections[0]
        self.assertEqual(rejection.reducer_decision_id, "redmem_reject")
        self.assertEqual(rejection.target_candidate_ids, ["cand_claim_bad"])
        self.assertIn("untrusted_source", rejection.rationale)
        self.assertEqual(rejection.failed_checks, ["provenance"])
        self.assertTrue(rejection.auto_rejected)
        # The promote decision is NOT a rejection.
        self.assertEqual(pack.reducer_decision_count, 2)

    def test_supersession_changelog(self):
        old = _fact("fact_old",
                    reducer_decision_id="redmem_old",
                    fact_text="Grade B card rate is 13.00% under v3",
                    status="superseded",
                    valid_from="2026-01-19T00:00:00Z",
                    valid_until="2026-05-18T00:00:00Z",
                    superseded_by=["fact_aaaa"])
        new = _fact(supersedes=["fact_old"])
        bundle = [_source(), _span(), _candidate(), _decision(), new, old]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        self.assertEqual(pack.supersession_count, 1)
        event = pack.supersessions[0]
        self.assertEqual(event.superseded_entry_id, "fact_old")
        self.assertEqual(event.successor_entry_id, "fact_aaaa")
        self.assertEqual(event.superseded_valid_until,
                         "2026-05-18T00:00:00Z")
        self.assertEqual(event.successor_valid_from,
                         "2026-05-18T00:00:00Z")
        self.assertIn("13.00%", event.statement_before)
        self.assertIn("12.75%", event.statement_after)

    def test_entries_sorted_deterministically(self):
        bundle = _full_chain_bundle() + [
            _fact("fact_zzzz", valid_from="2026-01-01T00:00:00Z"),
            _fact("fact_mmmm", valid_from="2026-01-01T00:00:00Z"),
        ]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        ids = [e.ledger_entry_id for e in pack.entries]
        # Earlier valid_from first; ties broken by id.
        self.assertEqual(ids, ["fact_mmmm", "fact_zzzz", "fact_aaaa"])

    def test_empty_bundle(self):
        pack = compute_audit_pack([], as_of=T_AS_OF)
        self.assertEqual(pack.total_records, 0)
        self.assertEqual(pack.ledger_entry_count, 0)
        self.assertEqual(pack.entries, [])
        self.assertEqual(pack.rejections, [])
        self.assertEqual(pack.supersessions, [])


# --- compute_audit_pack: missing-chain detection ---

class AuditPackMissingChainTests(unittest.TestCase):
    def test_missing_decision_flags_incomplete(self):
        bundle = [_source(), _span(), _candidate(), _fact()]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        entry = pack.entries[0]
        self.assertEqual(entry.chain_status, CHAIN_INCOMPLETE)
        self.assertEqual(pack.incomplete_chain_count, 1)
        self.assertTrue(any("redmem_aaaa" in m and "not in records" in m
                            for m in entry.missing))

    def test_no_reducer_decision_id_flags_incomplete(self):
        entry_dict = _fact()
        entry_dict["reducer_decision_id"] = ""
        bundle = [_source(), _span(), _candidate(), entry_dict]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        entry = pack.entries[0]
        self.assertEqual(entry.chain_status, CHAIN_INCOMPLETE)
        self.assertTrue(any("no reducer_decision_id" in m
                            for m in entry.missing))

    def test_decision_not_targeting_entry_flags_incomplete(self):
        decision = _decision(target_ledger_entry_ids=["fact_other"])
        bundle = [_source(), _span(), _candidate(), decision, _fact()]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        entry = pack.entries[0]
        self.assertEqual(entry.chain_status, CHAIN_INCOMPLETE)
        self.assertTrue(any("target_ledger_entry_ids" in m
                            for m in entry.missing))

    def test_dangling_candidate_flags_incomplete(self):
        bundle = [_source(), _span(), _decision(), _fact()]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        entry = pack.entries[0]
        self.assertEqual(entry.chain_status, CHAIN_INCOMPLETE)
        self.assertEqual(entry.missing_candidate_ids, ["cand_claim_aaaa"])
        self.assertTrue(any("cand_claim_aaaa" in m for m in entry.missing))

    def test_dangling_span_flags_incomplete_and_unresolved(self):
        bundle = [_source(), _candidate(), _decision(), _fact()]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        entry = pack.entries[0]
        self.assertEqual(entry.chain_status, CHAIN_INCOMPLETE)
        self.assertEqual(len(entry.evidence), 1)
        self.assertFalse(entry.evidence[0].resolved)
        self.assertTrue(any("span_aaaa" in m for m in entry.missing))

    def test_missing_source_for_span_flags_incomplete(self):
        bundle = [_span(), _candidate(), _decision(), _fact()]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        entry = pack.entries[0]
        self.assertEqual(entry.chain_status, CHAIN_INCOMPLETE)
        self.assertTrue(any("source src_aaaa" in m for m in entry.missing))
        # The span itself resolved; only the source is missing.
        self.assertTrue(entry.evidence[0].resolved)
        self.assertEqual(entry.evidence[0].source_title, "")

    def test_no_evidence_spans_flags_incomplete(self):
        entry_dict = _fact(evidence_span_ids=[])
        decision = _decision()
        bundle = [_source(), _candidate(), decision, entry_dict]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        entry = pack.entries[0]
        self.assertEqual(entry.chain_status, CHAIN_INCOMPLETE)
        self.assertTrue(any("no evidence_span_ids" in m
                            for m in entry.missing))

    def test_orphan_entry_does_not_crash(self):
        # A bare ledger entry, nothing else in the bundle: the
        # pack reports everything missing instead of raising.
        pack = compute_audit_pack([_fact()], as_of=T_AS_OF)
        entry = pack.entries[0]
        self.assertEqual(entry.chain_status, CHAIN_INCOMPLETE)
        self.assertGreaterEqual(len(entry.missing), 3)


# --- input validation ---

class AuditPackInputValidationTests(unittest.TestCase):
    def test_non_list_records_raises(self):
        with self.assertRaises(TypeError):
            compute_audit_pack("not a list")  # type: ignore[arg-type]

    def test_non_dict_record_raises(self):
        with self.assertRaises(TypeError) as cm:
            compute_audit_pack([1, 2])  # type: ignore[list-item]
        self.assertIn("each record must be a dict", str(cm.exception))
        self.assertIn("index 0", str(cm.exception))

    def test_malformed_as_of_raises(self):
        with self.assertRaises(ValueError) as cm:
            compute_audit_pack([], as_of="not-iso")
        self.assertIn("as_of must be ISO 8601", str(cm.exception))

    def test_empty_title_raises(self):
        with self.assertRaises(ValueError):
            compute_audit_pack([], as_of=T_AS_OF, title="")

    def test_default_as_of_is_now(self):
        pack = compute_audit_pack([])
        self.assertTrue(pack.as_of.endswith("Z"))


# --- id derivation ---

class AuditPackIdTests(unittest.TestCase):
    def test_id_is_content_derived(self):
        bundle = _full_chain_bundle()
        p1 = compute_audit_pack(bundle, as_of=T_AS_OF)
        p2 = compute_audit_pack(bundle, as_of=T_AS_OF)
        self.assertEqual(p1.id, p2.id)

    def test_id_changes_with_as_of(self):
        bundle = _full_chain_bundle()
        p1 = compute_audit_pack(bundle, as_of=T_AS_OF)
        p2 = compute_audit_pack(bundle, as_of="2026-06-16T12:00:00Z")
        self.assertNotEqual(p1.id, p2.id)

    def test_id_changes_with_bundle(self):
        p1 = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        p2 = compute_audit_pack([], as_of=T_AS_OF)
        self.assertNotEqual(p1.id, p2.id)

    def test_id_prefix_and_shape(self):
        pack = compute_audit_pack([], as_of=T_AS_OF)
        self.assertTrue(pack.id.startswith("audit_"))
        hex_part = pack.id[len("audit_"):]
        self.assertEqual(len(hex_part), 24)
        self.assertTrue(all(c in "0123456789abcdef" for c in hex_part))


# --- from_dict / to_dict round-trip ---

class AuditPackRoundTripTests(unittest.TestCase):
    def test_round_trip(self):
        bundle = _full_chain_bundle() + [
            _decision("redmem_reject", decision_type="reject",
                      target_ledger_entry_ids=[],
                      rationale="bad source"),
        ]
        original = compute_audit_pack(bundle, as_of=T_AS_OF)
        d = original.to_dict()
        roundtripped = AuditPack.from_dict(d)
        self.assertEqual(original.to_dict(), roundtripped.to_dict())

    def test_id_recomputed_on_round_trip(self):
        original = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        d = original.to_dict()
        d["id"] = "audit_DEADBEEF"  # deliberately wrong
        roundtripped = AuditPack.from_dict(d)
        self.assertEqual(roundtripped.id, original.id)
        self.assertNotEqual(roundtripped.id, "audit_DEADBEEF")

    def test_to_dict_is_json_serializable(self):
        pack = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        # Must not raise.
        json.dumps(pack.to_dict())


# --- audit_pack_to_markdown ---

class AuditMarkdownTests(unittest.TestCase):
    def test_title_is_h1(self):
        pack = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        md = audit_pack_to_markdown(pack)
        self.assertTrue(md.startswith(f"# {DEFAULT_AUDIT_TITLE}\n"))

    def test_header_block(self):
        pack = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        md = audit_pack_to_markdown(pack)
        self.assertIn(f"**As of:** {T_AS_OF}", md)
        self.assertIn(f"`{pack.bundle_fingerprint}`", md)
        self.assertIn(f"`{pack.id}`", md)
        self.assertIn("1 trusted ledger entries", md)

    def test_complete_chain_rendering(self):
        pack = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        md = audit_pack_to_markdown(pack)
        self.assertIn("## Trusted ledger — authorization chains", md)
        self.assertIn("### 1. Grade B card rate is 12.75% under v4", md)
        self.assertIn("`fact_aaaa`", md)
        self.assertIn("`redmem_aaaa` (promote)", md)
        self.assertIn("test-reducer", md)
        self.assertIn("**Rationale:** chain verified", md)
        self.assertIn('"Grade B rate is 12.75%"', md)
        self.assertIn("Rate policy v4", md)
        self.assertIn(f"- **Chain:** {CHAIN_COMPLETE}", md)
        self.assertNotIn(CHAIN_INCOMPLETE, md)

    def test_incomplete_chain_rendering(self):
        bundle = [_fact()]  # nothing else: every link missing
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        md = audit_pack_to_markdown(pack)
        self.assertIn(f"**{CHAIN_INCOMPLETE}**", md)
        self.assertIn("- missing:", md)
        self.assertIn("not in records", md)

    def test_rejections_rendered(self):
        bundle = _full_chain_bundle() + [
            _decision("redmem_reject", decision_type="reject",
                      target_candidate_ids=["cand_claim_bad"],
                      target_ledger_entry_ids=[],
                      rationale="untrusted_source: spoofed sender",
                      checks={"provenance": "fail",
                              "temporal_validity": "pass",
                              "contradiction_scan": "pass",
                              "privacy": "pass", "usefulness": "pass"},
                      metadata={"auto_rejected": True}),
        ]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        md = audit_pack_to_markdown(pack)
        self.assertIn("## Rejected in this period", md)
        self.assertIn("`redmem_reject` (auto-rejected)", md)
        self.assertIn("untrusted_source: spoofed sender", md)
        self.assertIn("failed checks: provenance", md)
        self.assertNotIn("## Rejected in this period\n\nNone.", md)

    def test_rejections_none_rendered_when_empty(self):
        pack = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        md = audit_pack_to_markdown(pack)
        self.assertIn("## Rejected in this period\n\nNone.\n", md)

    def test_supersession_changelog_rendered(self):
        old = _fact("fact_old",
                    fact_text="Grade B card rate is 13.00% under v3",
                    status="superseded",
                    valid_from="2026-01-19T00:00:00Z",
                    valid_until="2026-05-18T00:00:00Z",
                    superseded_by=["fact_aaaa"])
        new = _fact(supersedes=["fact_old"])
        bundle = [_source(), _span(), _candidate(), _decision(), new, old]
        pack = compute_audit_pack(bundle, as_of=T_AS_OF)
        md = audit_pack_to_markdown(pack)
        self.assertIn("## Supersession changelog", md)
        self.assertIn("`fact_old` → `fact_aaaa`", md)
        self.assertIn("13.00%", md)

    def test_supersession_none_rendered_when_empty(self):
        pack = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        md = audit_pack_to_markdown(pack)
        self.assertIn("## Supersession changelog\n\nNone.\n", md)

    def test_markdown_is_pure_function(self):
        pack = compute_audit_pack(_full_chain_bundle(), as_of=T_AS_OF)
        self.assertEqual(audit_pack_to_markdown(pack),
                         audit_pack_to_markdown(pack))


# --- public API surface ---

class AuditPublicApiTests(unittest.TestCase):
    def test_exported_from_package_root(self):
        import agent_memory_contracts as amc
        for name in ("AuditPack", "AuditChainEntry", "AuditEvidenceRef",
                     "AuditRejection", "AuditSupersession",
                     "compute_audit_pack", "audit_pack_to_markdown",
                     "DEFAULT_AUDIT_TITLE"):
            self.assertTrue(hasattr(amc, name), name)
            self.assertIn(name, amc.__all__)


# --- CLI: the `audit` subcommand ---

def _env() -> dict:
    return {**os.environ, "PYTHONPATH": "src"}


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", "-m", "agent_memory_contracts", *args],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=REPO_ROOT,
    )


class AuditCLITests(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.bundle_path = self.tmpdir / "bundle.jsonl"
        with self.bundle_path.open("w") as fh:
            for r in _full_chain_bundle():
                fh.write(json.dumps(r) + "\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_audit_basic_markdown(self):
        r = _run(["audit", str(self.bundle_path), "--as-of", T_AS_OF])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"# {DEFAULT_AUDIT_TITLE}", r.stdout)
        self.assertIn("authorization chains", r.stdout)
        self.assertIn("fact_aaaa", r.stdout)
        self.assertIn(CHAIN_COMPLETE, r.stdout)

    def test_audit_json_mode(self):
        r = _run(["--json", "audit", str(self.bundle_path),
                  "--as-of", T_AS_OF])
        self.assertEqual(r.returncode, 0, r.stderr)
        payload = json.loads(r.stdout)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["ledger_entry_count"], 1)
        self.assertEqual(payload["complete_chain_count"], 1)
        self.assertEqual(payload["schema_version"], "1.0.0")
        self.assertTrue(payload["id"].startswith("audit_"))
        self.assertEqual(len(payload["entries"]), 1)
        self.assertEqual(payload["entries"][0]["chain_status"],
                         CHAIN_COMPLETE)

    def test_audit_custom_title(self):
        r = _run(["audit", str(self.bundle_path), "--title", "Q2 audit"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("# Q2 audit\n"))

    def test_audit_missing_file_exits_1(self):
        r = _run(["audit", str(self.tmpdir / "does_not_exist.jsonl")])
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr)

    def test_audit_missing_file_json_mode(self):
        r = _run(["--json", "audit",
                  str(self.tmpdir / "does_not_exist.jsonl")])
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        payload = json.loads(r.stderr)
        self.assertEqual(payload["ok"], False)
        self.assertIn("not found", payload["error"])

    def test_audit_bad_as_of_exits_1(self):
        r = _run(["audit", str(self.bundle_path), "--as-of", "not-iso"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("as_of must be ISO 8601", r.stderr)

    def test_audit_unparseable_file_exits_1(self):
        bad = self.tmpdir / "bad.jsonl"
        bad.write_text("not json\n", encoding="utf-8")
        r = _run(["audit", str(bad)])
        self.assertEqual(r.returncode, 1)
        self.assertIn("failed to parse", r.stderr)

    def test_audit_in_help(self):
        r = _run(["--help"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("audit", r.stdout)


if __name__ == "__main__":
    unittest.main()
