"""Tests for the access control + bundle scope primitives.

Coverage targets (per docs/specs/sprint_23_access_control.md):

1. PRIVACY_CLASS_ORDER is the canonical linear order.
2. BundleScope validates the max_privacy_class at construction.
3. check_access returns allow / drop based on the privacy
   class and the scope's max_privacy_class.
4. check_access respects allowed_record_types when set.
5. check_access raises on unknown privacy_class.
6. scope_bundle returns a filtered bundle and a per-record
   decisions list, in the same order as the input.
7. summarize_access aggregates decisions into counts.
8. The four scope factories (public / team / customer / private)
   produce the expected scopes.
9. Dict-form records are accepted.
10. The public API is exported from agent_memory_contracts.
"""

from __future__ import annotations

import unittest
from typing import Any

from agent_memory_contracts import (
    AccessDecision,
    AccessSummary,
    BundleScope,
    PRIVACY_CLASS_ORDER,
    customer_scope,
    private_scope,
    public_scope,
    scope_bundle,
    summarize_access,
    team_scope,
    check_access,
)

from .fixtures import T_CAPTURED, T_DECIDED, build_source_and_span


def _build_source(privacy_class: str, suffix: str) -> Any:
    """Build a SourceRecord at the given privacy class."""
    from agent_memory_contracts import SourceRecord, make_source_id
    # 64-hex content hash; suffix is a single hex char.
    suffix = suffix[0]
    content_hash = (suffix * 64)[:64]
    source_id = make_source_id("chatgpt_conversation", f"https://example.com/{suffix}", content_hash)
    return SourceRecord.from_dict({
        "id": source_id, "schema_version": "1.0.0",
        "source_type": "chatgpt_conversation",
        "title": f"Title {suffix}", "origin_uri": f"https://example.com/{suffix}",
        "raw_ref": {"kind": "external_uri", "value": f"https://example.com/{suffix}"},
        "content_hash_sha256": content_hash,
        "captured_at": T_CAPTURED, "observed_at": None,
        "author_or_sender": None, "participants": [],
        "privacy_class": privacy_class, "custody_status": "synthetic",
        "parser_version": "v1", "metadata": {},
    })


def _build_all_classes_bundle() -> list[Any]:
    """Build a bundle with one record at each privacy class."""
    # Use distinct hex chars so the hashes are distinct.
    return [
        _build_source("public", "1"),
        _build_source("internal", "2"),
        _build_source("private", "3"),
        _build_source("sensitive", "4"),
        _build_source("highly_sensitive", "5"),
    ]


class TestPrivacyClassOrder(unittest.TestCase):
    """PRIVACY_CLASS_ORDER is the canonical linear order."""

    def test_order_has_five_classes(self) -> None:
        self.assertEqual(len(PRIVACY_CLASS_ORDER), 5)

    def test_order_strictly_increasing(self) -> None:
        # public is least restricted, highly_sensitive is most.
        self.assertEqual(PRIVACY_CLASS_ORDER[0], "public")
        self.assertEqual(PRIVACY_CLASS_ORDER[-1], "highly_sensitive")

    def test_order_matches_set(self) -> None:
        from agent_memory_contracts.evidence_contracts import PRIVACY_CLASSES
        self.assertEqual(set(PRIVACY_CLASS_ORDER), PRIVACY_CLASSES)


class TestBundleScopeConstruction(unittest.TestCase):
    """BundleScope validates the max_privacy_class at construction."""

    def test_valid_scope(self) -> None:
        s = BundleScope(max_privacy_class="internal", name="team")
        self.assertEqual(s.max_privacy_class, "internal")
        self.assertEqual(s.name, "team")
        self.assertIsNone(s.allowed_record_types)

    def test_invalid_max_raises(self) -> None:
        with self.assertRaises(ValueError):
            BundleScope(max_privacy_class="classified")

    def test_allowed_record_types_coerced_to_frozenset(self) -> None:
        s = BundleScope(allowed_record_types={"source_record", "fact_ledger_entry"})
        self.assertIsInstance(s.allowed_record_types, frozenset)
        self.assertEqual(
            s.allowed_record_types,
            frozenset({"source_record", "fact_ledger_entry"}),
        )


class TestScopeFactories(unittest.TestCase):
    """The four scope factories produce the expected scopes."""

    def test_public_scope(self) -> None:
        s = public_scope()
        self.assertEqual(s.max_privacy_class, "public")
        self.assertEqual(s.name, "public")

    def test_team_scope(self) -> None:
        s = team_scope()
        self.assertEqual(s.max_privacy_class, "internal")
        self.assertEqual(s.name, "team")

    def test_customer_scope(self) -> None:
        s = customer_scope()
        self.assertEqual(s.max_privacy_class, "private")
        self.assertEqual(s.name, "customer")

    def test_private_scope(self) -> None:
        s = private_scope()
        self.assertEqual(s.max_privacy_class, "highly_sensitive")
        self.assertEqual(s.name, "private")


class TestCheckAccess(unittest.TestCase):
    """check_access returns allow/drop based on the privacy class."""

    def test_internal_record_allowed_at_team_scope(self) -> None:
        from tests.test_citations import _build_fact_ledger_entry
        src, span = build_source_and_span()
        fact = _build_fact_ledger_entry(src.id, [span.id])
        scope = team_scope()
        d = check_access(fact, scope)
        self.assertEqual(d.action, "allow")
        # The fixture's source uses privacy_class="internal";
        # the fact's privacy_class defaults to "internal" too.
        self.assertIn("internal", d.reason)

    def test_highly_sensitive_record_dropped_at_team_scope(self) -> None:
        src = _build_source("highly_sensitive", "5")
        scope = team_scope()
        d = check_access(src, scope)
        self.assertEqual(d.action, "drop")
        self.assertIn("highly_sensitive", d.reason)
        self.assertIn("internal", d.reason)

    def test_public_record_allowed_at_public_scope(self) -> None:
        src = _build_source("public", "1")
        scope = public_scope()
        d = check_access(src, scope)
        self.assertEqual(d.action, "allow")

    def test_internal_record_dropped_at_public_scope(self) -> None:
        src = _build_source("internal", "2")
        scope = public_scope()
        d = check_access(src, scope)
        self.assertEqual(d.action, "drop")

    def test_highly_sensitive_allowed_at_private_scope(self) -> None:
        src = _build_source("highly_sensitive", "5")
        scope = private_scope()
        d = check_access(src, scope)
        self.assertEqual(d.action, "allow")

    def test_unknown_privacy_class_raises(self) -> None:
        src = _build_source("public", "1")
        # Mutate to a non-canonical value to test the contract.
        object.__setattr__(src, "privacy_class", "classified")
        scope = team_scope()
        with self.assertRaises(ValueError):
            check_access(src, scope)

    def test_record_without_privacy_class_defaults_to_internal(self) -> None:
        # A plain dict without privacy_class.
        record = {"id": "x_1", "schema_version": "1.0.0", "kind": "test"}
        scope = team_scope()
        d = check_access(record, scope)
        self.assertEqual(d.action, "allow")

    def test_allowed_record_types_filters_out_disallowed_type(self) -> None:
        # A fact_ledger_entry is not in the allowed set.
        from tests.test_citations import _build_fact_ledger_entry
        src, span = build_source_and_span()
        fact = _build_fact_ledger_entry(src.id, [span.id])
        scope = BundleScope(
            max_privacy_class="highly_sensitive",
            allowed_record_types=frozenset({"source_record"}),
            name="sources-only",
        )
        d = check_access(fact, scope)
        # The fact is "private" by default? No, by default
        # fact_text isn't classified. The fixture uses the
        # default privacy_class from build_source_and_span
        # which is "internal". Internal is <= highly_sensitive,
        # so the privacy class check passes. The record type
        # check then drops it.
        self.assertEqual(d.action, "drop")
        self.assertIn("fact_ledger_entry", d.reason)

    def test_dict_record(self) -> None:
        record = {
            "id": "src_dict_1",
            "schema_version": "1.0.0",
            "privacy_class": "public",
        }
        scope = team_scope()
        d = check_access(record, scope)
        self.assertEqual(d.action, "allow")

    def test_decision_is_access_decision(self) -> None:
        src = _build_source("public", "1")
        scope = team_scope()
        d = check_access(src, scope)
        self.assertIsInstance(d, AccessDecision)


class TestScopeBundle(unittest.TestCase):
    """scope_bundle filters a bundle and returns per-record decisions."""

    def test_all_classes_team_scope(self) -> None:
        bundle = _build_all_classes_bundle()
        scope = team_scope()
        filtered, decisions = scope_bundle(bundle, scope)
        # team_scope: public + internal allowed; private +
        # sensitive + highly_sensitive dropped.
        self.assertEqual(len(filtered), 2)
        self.assertEqual(len(decisions), 5)
        actions = [d.action for d in decisions]
        self.assertEqual(actions.count("allow"), 2)
        self.assertEqual(actions.count("drop"), 3)

    def test_all_classes_public_scope(self) -> None:
        bundle = _build_all_classes_bundle()
        scope = public_scope()
        filtered, decisions = scope_bundle(bundle, scope)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(decisions[0].action, "allow")
        for d in decisions[1:]:
            self.assertEqual(d.action, "drop")

    def test_all_classes_private_scope(self) -> None:
        bundle = _build_all_classes_bundle()
        scope = private_scope()
        filtered, decisions = scope_bundle(bundle, scope)
        self.assertEqual(len(filtered), 5)
        for d in decisions:
            self.assertEqual(d.action, "allow")

    def test_empty_bundle(self) -> None:
        filtered, decisions = scope_bundle([], team_scope())
        self.assertEqual(filtered, [])
        self.assertEqual(decisions, [])

    def test_order_preserved(self) -> None:
        bundle = _build_all_classes_bundle()
        scope = private_scope()  # allow all
        filtered, _ = scope_bundle(bundle, scope)
        self.assertEqual([r.id for r in filtered], [r.id for r in bundle])

    def test_decisions_in_input_order(self) -> None:
        bundle = _build_all_classes_bundle()
        scope = team_scope()
        _, decisions = scope_bundle(bundle, scope)
        ids = [d.record_id for d in decisions]
        self.assertEqual(ids, [r.id for r in bundle])


class TestSummarizeAccess(unittest.TestCase):
    """summarize_access aggregates decisions into counts."""

    def test_summary_counts(self) -> None:
        bundle = _build_all_classes_bundle()
        scope = team_scope()
        _, decisions = scope_bundle(bundle, scope)
        summary = summarize_access(decisions)
        self.assertIsInstance(summary, AccessSummary)
        self.assertEqual(summary.total, 5)
        self.assertEqual(summary.allowed, 2)
        self.assertEqual(summary.redacted, 0)
        self.assertEqual(summary.dropped, 3)
        self.assertEqual(summary.by_action, {"allow": 2, "drop": 3})

    def test_summary_empty(self) -> None:
        summary = summarize_access([])
        self.assertEqual(summary.total, 0)
        self.assertEqual(summary.allowed, 0)
        self.assertEqual(summary.dropped, 0)
        self.assertEqual(summary.by_action, {})

    def test_summary_by_privacy_class(self) -> None:
        bundle = _build_all_classes_bundle()
        scope = private_scope()
        _, decisions = scope_bundle(bundle, scope)
        summary = summarize_access(decisions)
        # All 5 records contribute one entry to by_privacy_class.
        self.assertEqual(len(summary.by_privacy_class), 5)
        self.assertEqual(summary.by_privacy_class["public"], 1)
        self.assertEqual(summary.by_privacy_class["highly_sensitive"], 1)


class TestDataclassRecordAccess(unittest.TestCase):
    """Real dataclass records (SourceRecord, etc.) are accepted."""

    def test_source_record_at_internal_allowed(self) -> None:
        # build_source_and_span uses privacy_class="internal".
        src, _ = build_source_and_span()
        scope = team_scope()
        d = check_access(src, scope)
        self.assertEqual(d.action, "allow")


class TestPublicApi(unittest.TestCase):
    """All v0.9.0 names are exported from agent_memory_contracts."""

    def test_v090_exports_present(self) -> None:
        import agent_memory_contracts as a
        for name in (
            "PRIVACY_CLASS_ORDER",
            "BundleScope",
            "AccessDecision",
            "AccessSummary",
            "check_access",
            "scope_bundle",
            "summarize_access",
            "public_scope",
            "team_scope",
            "customer_scope",
            "private_scope",
        ):
            self.assertTrue(hasattr(a, name), f"missing export: {name}")

    def test_version_bumped_to_090(self) -> None:
        import agent_memory_contracts as a
        self.assertEqual(a.__version__, "0.9.0")


if __name__ == "__main__":
    unittest.main()
