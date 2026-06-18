"""Poisoning demo: one attack, two memory stores, two outcomes.

Run from the repository root::

    PYTHONPATH=src python examples/poisoning_demo/run.py
    PYTHONPATH=src python examples/poisoning_demo/run.py --out /tmp/poison-out

Zero API keys, zero network, fully deterministic. Two stores ingest
the same two documents and the same extraction fixtures:

- **Store A ("silent")** -- ``naive_store.py``: extract dict ->
  append list -> keyword retrieve. The architecture most agent
  memory ships with.
- **Store B ("governed")** -- ``governed_store.py``: the same
  extractions routed through the real contracts (SourceRecord /
  EvidenceSpan -> CandidateClaim -> reducer-authorized
  FactLedgerEntry).

The attack is a forged "revised payout" memo from a spoofed sender
(modeled on a classic BEC pattern against a lender). Store A
swallows it and starts answering with the forged rate as truth.
Store B rejects it in the candidate plane -- untrusted source tier,
no authorizing chain -- and emits a rejection receipt (a real
``MemoryReducerDecision``). The trusted ledger's
``bundle_fingerprint`` is byte-identical before and after the
attack.

Outputs: a console table and ``report.md`` + ``summary.json`` in
the output directory (default: ``examples/poisoning_demo/out/``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

try:  # imported as a package (pytest / library callers)
    from examples.poisoning_demo import fixtures
    from examples.poisoning_demo.governed_store import GovernedMemoryStore
    from examples.poisoning_demo.naive_store import NaiveMemoryStore
except ImportError:  # executed directly as a script
    import fixtures  # type: ignore[no-redef]
    from governed_store import GovernedMemoryStore  # type: ignore[no-redef]
    from naive_store import NaiveMemoryStore  # type: ignore[no-redef]


DEFAULT_OUT_DIR = Path(__file__).resolve().parent / "out"


def _naive_answer(fact: dict[str, Any] | None) -> str:
    if fact is None:
        return "(no answer)"
    return f"{fact['object']} — \"{fact['claim_text']}\""


def _governed_answer(entry: Any) -> str:
    if entry is None:
        return "(no answer)"
    return f"{entry.object} — \"{entry.fact_text}\""


def run_demo() -> dict[str, Any]:
    """Run the two-store attack and return a structured result."""
    store_a = NaiveMemoryStore()
    store_b = GovernedMemoryStore(
        trusted_sender_domains=fixtures.TRUSTED_SENDER_DOMAINS,
        known_authorization_refs=fixtures.KNOWN_AUTHORIZATION_REFS,
        decided_at=fixtures.T_DECIDED,
    )

    # Phase 1: both stores ingest the legitimate policy document.
    store_a.ingest(fixtures.DOC_POLICY, fixtures.EXTRACTIONS)
    store_b.ingest(fixtures.DOC_POLICY, fixtures.EXTRACTIONS,
                   extracted_at=fixtures.T_EXTRACTED)

    a_before = _naive_answer(store_a.retrieve(fixtures.QUERY))
    b_before = _governed_answer(store_b.retrieve(fixtures.QUERY))
    a_fp_before = store_a.fingerprint()
    b_fp_before = store_b.trusted_fingerprint()

    # Phase 2: the attack. Both stores ingest the forged memo.
    store_a.ingest(fixtures.DOC_FORGED, fixtures.EXTRACTIONS)
    b_attack = store_b.ingest(fixtures.DOC_FORGED, fixtures.EXTRACTIONS,
                              extracted_at=fixtures.T_EXTRACTED)

    a_after = _naive_answer(store_a.retrieve(fixtures.QUERY))
    b_after = _governed_answer(store_b.retrieve(fixtures.QUERY))
    a_fp_after = store_a.fingerprint()
    b_fp_after = store_b.trusted_fingerprint()

    receipts = [
        {
            "candidate_id": candidate_id,
            "reason_code": reason,
            "decision_id": decision.id,
            "rationale": decision.rationale,
            "failed_checks": sorted(
                k for k, v in decision.checks.items() if v == "fail"),
            "decided_by": decision.decided_by["agent"],
            "decided_at": decision.decided_at,
        }
        for candidate_id, (reason, decision)
        in sorted(store_b.quarantine.items())
    ]

    return {
        "query": fixtures.QUERY,
        "store_a": {
            "answer_before_attack": a_before,
            "answer_after_attack": a_after,
            "fingerprint_before": a_fp_before,
            "fingerprint_after": a_fp_after,
            "memory_size": len(store_a.facts),
            "poisoned": "1.75%" in a_after,
        },
        "store_b": {
            "answer_before_attack": b_before,
            "answer_after_attack": b_after,
            "trusted_fingerprint_before": b_fp_before,
            "trusted_fingerprint_after": b_fp_after,
            "trusted_entries": len(store_b.ledger),
            "quarantined_candidates": len(store_b.quarantine),
            "promoted_during_attack": b_attack["promoted"],
            "rejected_during_attack": b_attack["rejected"],
            "rejection_receipts": receipts,
            "poisoned": "1.75%" in b_after,
        },
    }


def _console_table(result: dict[str, Any]) -> str:
    a = result["store_a"]
    b = result["store_b"]
    rows = [
        ("Query", result["query"], ""),
        ("Answer before attack", a["answer_before_attack"][:46],
         b["answer_before_attack"][:46]),
        ("Answer after attack", a["answer_after_attack"][:46],
         b["answer_after_attack"][:46]),
        ("Poisoned?", "YES" if a["poisoned"] else "no",
         "YES" if b["poisoned"] else "no"),
        ("Memory fingerprint", "CHANGED by attack"
         if a["fingerprint_before"] != a["fingerprint_after"]
         else "unchanged",
         "unchanged" if b["trusted_fingerprint_before"]
         == b["trusted_fingerprint_after"] else "CHANGED by attack"),
        ("Rejection receipts", "none (silent)",
         str(len(b["rejection_receipts"]))),
    ]
    w0 = max(len(r[0]) for r in rows)
    w1 = max(len("Store A (silent)"), max(len(r[1]) for r in rows))
    w2 = max(len("Store B (governed)"), max(len(r[2]) for r in rows))
    sep = f"+-{'-' * w0}-+-{'-' * w1}-+-{'-' * w2}-+"
    lines = [sep,
             f"| {'':{w0}} | {'Store A (silent)':{w1}} | {'Store B (governed)':{w2}} |",
             sep]
    for label, col_a, col_b in rows:
        lines.append(f"| {label:{w0}} | {col_a:{w1}} | {col_b:{w2}} |")
    lines.append(sep)
    return "\n".join(lines)


def _report_markdown(result: dict[str, Any]) -> str:
    a = result["store_a"]
    b = result["store_b"]
    lines: list[str] = []
    lines.append("# Poisoning demo report")
    lines.append("")
    lines.append("One attack, two memory stores. Both ingested the same "
                 "legitimate policy document, then the same forged payout "
                 "memo from a spoofed sender. Deterministic; no model "
                 "calls.")
    lines.append("")
    lines.append(f"**Query:** {result['query']}")
    lines.append("")
    lines.append("| | Store A (silent) | Store B (governed) |")
    lines.append("| --- | --- | --- |")
    lines.append(f"| Answer before attack | {a['answer_before_attack']} "
                 f"| {b['answer_before_attack']} |")
    lines.append(f"| Answer after attack | {a['answer_after_attack']} "
                 f"| {b['answer_after_attack']} |")
    lines.append(f"| Poisoned by the forged memo | "
                 f"{'**YES** — forged rate served as truth' if a['poisoned'] else 'no'} | "
                 f"{'**YES**' if b['poisoned'] else 'no — forgery quarantined'} |")
    lines.append("")
    lines.append("## Store A: what happened")
    lines.append("")
    lines.append("Extract dict, append list, keyword retrieve. The forged "
                 "memo is newer and keyword-denser, so retrieval now "
                 "returns the attacker's number. No receipt, no flag, no "
                 "trace — the store cannot tell you this happened.")
    lines.append("")
    lines.append(f"- Memory fingerprint before attack: `{a['fingerprint_before']}`")
    lines.append(f"- Memory fingerprint after attack:  `{a['fingerprint_after']}` "
                 f"(changed: {a['fingerprint_before'] != a['fingerprint_after']})")
    lines.append("")
    lines.append("## Store B: the rejection receipts")
    lines.append("")
    lines.append("The same extractions became candidates; the reducer "
                 "refused to promote the forged ones. The candidates stay "
                 "quarantined in the candidate plane, and each rejection "
                 "is a real `MemoryReducerDecision`: ")
    lines.append("")
    for receipt in b["rejection_receipts"]:
        lines.append(f"- **{receipt['reason_code']}** — decision "
                     f"`{receipt['decision_id']}`")
        lines.append(f"  - candidate: `{receipt['candidate_id']}` "
                     f"(still in candidate plane, never promoted)")
        lines.append(f"  - rationale: {receipt['rationale']}")
        lines.append(f"  - failed checks: "
                     f"{', '.join(receipt['failed_checks'])}")
        lines.append(f"  - decided by: {receipt['decided_by']} at "
                     f"{receipt['decided_at']}")
    lines.append("")
    lines.append("## Store B: trusted ledger unchanged")
    lines.append("")
    lines.append("`bundle_fingerprint` of the trusted ledger plane, before "
                 "and after the attack:")
    lines.append("")
    lines.append(f"- Before: `{b['trusted_fingerprint_before']}`")
    lines.append(f"- After:  `{b['trusted_fingerprint_after']}`")
    unchanged = (b["trusted_fingerprint_before"]
                 == b["trusted_fingerprint_after"])
    lines.append(f"- Unchanged: **{unchanged}**")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("Same attack. Store A's memory is whatever arrived last; "
                 "Store B's memory is whatever was authorized — and it can "
                 "prove the difference.")
    return "\n".join(lines) + "\n"


def main(out_dir: str | Path | None = None) -> dict[str, Any]:
    """Run the demo, print the console table, write the report.

    Returns the structured result (also written to
    ``summary.json``) so tests can assert on it directly.
    """
    out_path = Path(out_dir) if out_dir is not None else DEFAULT_OUT_DIR
    out_path.mkdir(parents=True, exist_ok=True)

    result = run_demo()

    print("=== Poisoning demo: silent store vs governed contracts ===")
    print()
    print(_console_table(result))
    print()
    for receipt in result["store_b"]["rejection_receipts"]:
        print(f"  Store B receipt: {receipt['reason_code']} -> "
              f"{receipt['decision_id']}")
    report_path = out_path / "report.md"
    report_path.write_text(_report_markdown(result), encoding="utf-8")
    summary_path = out_path / "summary.json"
    summary_path.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print()
    print(f"  report:  {report_path}")
    print(f"  summary: {summary_path}")

    # The demo's own invariants, asserted on every run.
    assert result["store_a"]["poisoned"], "Store A should be poisoned"
    assert not result["store_b"]["poisoned"], "Store B must stay clean"
    assert (result["store_b"]["trusted_fingerprint_before"]
            == result["store_b"]["trusted_fingerprint_after"]), \
        "Store B trusted plane must be unchanged by the attack"
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=None,
                        help="Output directory for report.md + summary.json "
                             "(default: examples/poisoning_demo/out/).")
    args = parser.parse_args()
    main(args.out)
    sys.exit(0)
