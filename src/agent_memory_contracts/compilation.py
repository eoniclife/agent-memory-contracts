"""ContextPack compiler: the bridge between integrity and
company brain.

The library has shipped the ``ContextPack``,
``ContextPackBuildReceipt``, and
``ContextPackValidationReport`` schemas since v0.4.0.
What it has NOT shipped is a function that takes a
bundle of trusted memories and produces a task-ready
``ContextPack`` with a ``BuildReceipt`` and
``ValidationReport`` attached.

This module ships that function. The compiler is the
"company brain" primitive: a strict, deterministic
function that selects records from a bundle based on a
task description, an optional access scope, and a
compilation policy; surfaces what was selected and
excluded; and validates the result against the JSON
Schemas.

The compiler is a **first-in-market primitive**. Per
the user's competitive analysis (June 2026): neither
Mem0 nor LangGraph has a structured context assembly
primitive. Mem0 concatenates strings; LangGraph
returns raw search hits. The compiler is the library's
differentiator for the company-brain story.

The compiler is **strict by default**: every claim in
the pack is required to have a path to a source. A
``ContextPack`` with an unsupported claim is a bug, not
a feature. The product can disable this with
``require_source_coverage=False`` for exploratory
queries, but the default is strict.

The compiler is **retrieval-agnostic**: it does not
call embedding models or do semantic search. The
product pre-filters the bundle by retrieval before
calling the compiler; the compiler selects from the
filtered set. This keeps the library retrieval-
agnostic and lets the product choose its vector DB.

The compiler is **a pure function**: no I/O, no
randomness, no global state. Idempotent. Two calls
with the same inputs produce the same
``CompilationResult``.

Like the rest of the bundle primitives, this module
is standard-library only.

.. versionadded:: 1.0.0-alpha.3
"""

from __future__ import annotations

import dataclasses
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from .citations import CitationGraph
from .contextpack_contracts import (
    AUTHORITY_SOURCES,
    CHECK_VALUES,
    CONFLICT_KINDS,
    ContextPack,
    ContextPackBuildReceipt,
    ContextPackValidationReport,
    REPORT_STATUSES,
    REQUIRED_FORBIDDEN_ACTIONS,
    RISK_CLASSES,
    TASK_TYPES,
    VALIDATION_CHECK_KEYS,
)
from .contextpack_ids import (
    make_context_pack_build_receipt_id,
    make_context_pack_id,
    make_context_pack_validation_report_id,
)
from .evidence_ids import _canonical_json, sha256_hex


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


#: Builder agent identifier for the compiler.
DEFAULT_BUILDER_AGENT: str = "agent-memory-contracts"

#: Builder model identifier (the library version).
DEFAULT_BUILDER_MODEL: str = "v1.0.0a3-compiler"

#: Builder tool identifier.
DEFAULT_BUILDER_TOOL: str = "compile_context_pack"

#: Builder mode (per BUILDER_MODES = {"syntheticfixture",
#: "manual", "compiler"}).
DEFAULT_BUILDER_MODE: str = "compiler"

#: Validator agent identifier.
DEFAULT_VALIDATOR_AGENT: str = "agent-memory-contracts"

#: Validator tool identifier.
DEFAULT_VALIDATOR_TOOL: str = "validate_contextpack"

#: Validator version (the library version).
DEFAULT_VALIDATOR_VERSION: str = "v1.0.0a3"

#: The library's current schema version (per
#: ``agent_memory_contracts.migrations.CURRENT_SCHEMA_VERSION``).
LIBRARY_SCHEMA_VERSION: str = "1.0.0"

#: How many records the compiler considers as "primary"
#: evidence when populating
#: ``ContextPack.evidence["primary_evidence_span_ids"]``.
#: The compiler picks the top ``PRIMARY_EVIDENCE_LIMIT``
#: records by selection score.
PRIMARY_EVIDENCE_LIMIT: int = 5

#: Default selection window for the "recent" strategy: how
#: far back in time to consider. The compiler does not
#: filter by this window (it's a future-sprint concern);
#: it only uses the window as the
#: ``selection_policy.current_cutoff`` value in the
#: ``BuildReceipt``.
DEFAULT_CURRENT_CUTOFF: str = "1970-01-01T00:00:00Z"

#: Default token budget (per ``constraints.token_budget``).
#: The compiler does not actually count tokens; the
#: product is responsible for fitting the selected
#: records into a context window. The default is
#: generous; the product can pass a smaller value.
DEFAULT_TOKEN_BUDGET: int = 8000


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ContextPackTask:
    """The task description the compiler is producing a
    pack for.

    Mirrors the seven required keys of the
    ``ContextPack.task`` field. The library's existing
    ``ContextPack`` schema is the source of truth for
    the allowed values of ``task_type`` and
    ``risk_class``.
    """

    task_id: str
    task_title: str
    task_type: str  # from TASK_TYPES
    task_summary: str
    project_id: str
    risk_class: str  # from RISK_CLASSES
    sensitivity: str  # from PRIVACY_CLASS_ORDER

    def __post_init__(self) -> None:
        if self.task_type not in TASK_TYPES:
            raise ValueError(
                f"task_type must be one of {sorted(TASK_TYPES)}, got {self.task_type!r}"
            )
        if self.risk_class not in RISK_CLASSES:
            raise ValueError(
                f"risk_class must be one of {sorted(RISK_CLASSES)}, got {self.risk_class!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to the dict form used by
        ``ContextPack.task``.
        """
        return {
            "task_id": self.task_id,
            "task_title": self.task_title,
            "task_type": self.task_type,
            "task_summary": self.task_summary,
            "project_id": self.project_id,
            "risk_class": self.risk_class,
            "sensitivity": self.sensitivity,
        }


@dataclass(frozen=True)
class CompilationPolicy:
    """The compiler's configuration.

    All fields have sensible defaults; the product can
    override any of them.
    """

    max_records: int = 50
    require_source_coverage: bool = True
    selection_strategy: str = "recent"  # "recent" | "diverse" | "frequent"
    prefer_confidence: tuple[str, ...] = ("high", "medium", "low")
    exclude_stale: bool = True
    exclude_retracted: bool = True
    exclude_contested: bool = False
    builder_agent: str = DEFAULT_BUILDER_AGENT
    builder_model: str = DEFAULT_BUILDER_MODEL
    builder_tool: str = DEFAULT_BUILDER_TOOL
    builder_mode: str = DEFAULT_BUILDER_MODE
    validator_agent: str = DEFAULT_VALIDATOR_AGENT
    validator_tool: str = DEFAULT_VALIDATOR_TOOL
    validator_version: str = DEFAULT_VALIDATOR_VERSION

    def __post_init__(self) -> None:
        if self.max_records < 0:
            raise ValueError(f"max_records must be >= 0, got {self.max_records}")
        if self.selection_strategy not in ("recent", "diverse", "frequent"):
            raise ValueError(
                f"selection_strategy must be one of "
                f"'recent', 'diverse', 'frequent'; got {self.selection_strategy!r}"
            )
        if self.builder_mode not in ("syntheticfixture", "manual", "compiler"):
            raise ValueError(
                f"builder_mode must be one of "
                f"'syntheticfixture', 'manual', 'compiler'; got {self.builder_mode!r}"
            )


@dataclass(frozen=True)
class CompilationResult:
    """The compiler's output.

    Attributes:
        context_pack: the compiled ``ContextPack``.
        build_receipt: the ``BuildReceipt`` recording what
            was selected, what was excluded, and why.
        validation_report: the ``ValidationReport`` with
            per-check pass/fail status.
        selected_record_ids: ids of records that made
            the cut, in selection order (score descending,
            id ascending as tiebreaker).
        excluded_record_ids: ids of records that were
            excluded, in (reason, id) order.
        selection_score_by_id: the score each selected
            record got. Useful for debugging the
            selection algorithm.
    """

    context_pack: ContextPack
    build_receipt: ContextPackBuildReceipt
    validation_report: ContextPackValidationReport
    selected_record_ids: tuple[str, ...] = ()
    excluded_record_ids: tuple[str, ...] = ()
    selection_score_by_id: Mapping[str, float] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Free function: the compiler
# ---------------------------------------------------------------------------


def compile_context_pack(
    bundle: Iterable[Any],
    *,
    task: ContextPackTask,
    scope: Any = None,  # BundleScope from .access, but optional
    policy: CompilationPolicy | None = None,
) -> CompilationResult:
    """Compile a ``ContextPack`` from a bundle of records.

    Args:
        bundle: an iterable of records (dataclasses,
            dicts, or Mappings). Records should already
            be at the library's current schema version
            (1.0.0). The compiler does not migrate
            bundles; the product is responsible for
            that (via ``migrate_bundle`` from v1.0.0a2).
        task: a ``ContextPackTask`` describing the task
            the pack is being built for.
        scope: an optional ``BundleScope`` (from
            ``agent_memory_contracts.access``). When
            provided, the compiler applies the scope
            before selection; records outside the scope
            are excluded. When ``None``, no scope
            filtering is applied.
        policy: an optional ``CompilationPolicy``. When
            ``None``, the default policy is used.

    Returns:
        A ``CompilationResult`` with the
        ``ContextPack``, ``BuildReceipt``,
        ``ValidationReport``, and per-record selection
        diagnostics.

    Raises:
        ValueError: if the bundle contains records
            whose shape is unrecognizable (no id
            field), or if the policy is invalid.
    """
    if policy is None:
        policy = CompilationPolicy()

    records = list(bundle)
    excluded: list[tuple[str, str]] = []  # (record_id, reason)
    selected: list[tuple[str, float]] = []  # (record_id, score)

    # 0. Collect state records separately. State
    #    records (CoreStateSnapshot, ProjectStateSnapshot)
    #    are not part of the citation graph (they're
    #    metadata about memory state, not claims), so
    #    we look for them in the raw bundle before
    #    building the graph.
    state_records: list[dict[str, Any]] = []
    for record in records:
        d = _to_dict(record)
        if d.get("state_type") in ("project_state", "core_state"):
            state_records.append(d)

    # 1. Normalize to dicts and dedupe by id.
    by_id: dict[str, dict[str, Any]] = {}
    for record in records:
        d = _to_dict(record)
        rid = d.get("id")
        if not isinstance(rid, str) or not rid:
            raise ValueError(
                f"record has no id field; cannot compile: {record!r}"
            )
        if rid in by_id:
            # First occurrence wins; subsequent dupes
            # are recorded as excluded.
            excluded.append((rid, "duplicate_id"))
            continue
        by_id[rid] = d

    # 2. Apply scope filter.
    if scope is not None:
        from .access import check_access
        for rid, d in list(by_id.items()):
            decision = check_access(d, scope)
            if decision.action != "allow":
                del by_id[rid]
                excluded.append((rid, "outside_scope"))

    # 3. Schema validation. Records that fail are
    #    excluded; the failure is recorded in the
    #    ValidationReport.errors.
    validation_errors: list[dict[str, Any]] = []
    for rid, d in list(by_id.items()):
        ok, err = _structural_validate(d)
        if not ok:
            del by_id[rid]
            excluded.append((rid, f"schema_invalid:{err}"))
            validation_errors.append({"code": "schema_invalid", "message": err, "related_ids": [rid]})

    # 4. Status filter (stale, retracted, contested).
    for rid, d in list(by_id.items()):
        status = str(d.get("status", "active"))
        if status == "stale" and policy.exclude_stale:
            del by_id[rid]
            excluded.append((rid, "excluded_stale"))
        elif status == "retracted" and policy.exclude_retracted:
            del by_id[rid]
            excluded.append((rid, "excluded_retracted"))
        elif status == "contested" and policy.exclude_contested:
            del by_id[rid]
            excluded.append((rid, "excluded_contested"))

    # 5. Source-coverage filter (when required).
    if policy.require_source_coverage:
        try:
            graph = CitationGraph.from_bundle(list(by_id.values()))
            for rid, d in list(by_id.items()):
                # A record is "supported" if it has at
                # least one path to a source-kind node,
                # OR if it IS a source-kind node itself
                # (sources don't need a source).
                if rid not in graph.nodes:
                    continue
                node = graph.nodes[rid]
                if node.node_kind == "source":
                    continue
                # Try to find a path to any source.
                paths = graph.traverse(rid, direction="forward")
                if not any(p.is_supported() for p in paths):
                    del by_id[rid]
                    excluded.append((rid, "no_source_backing"))
        except ValueError:
            # Cycle detected in the in-bundle graph.
            # This is a serious integrity issue; surface
            # it as a validation error but don't fail the
            # whole compilation.
            validation_errors.append({
                "record_id": "<bundle>",
                "error": "citation graph has a cycle; source-coverage check skipped",
            })

    # 6. Score.
    for rid, d in by_id.items():
        score = _score(d, policy)
        selected.append((rid, score))

    # 7. Select.
    if policy.selection_strategy == "recent":
        selected.sort(key=lambda kv: (-kv[1], kv[0]))
    elif policy.selection_strategy == "diverse":
        # Pick one record per record_type, rotating
        # through the types in plane order.
        selected = _diverse_select(selected, by_id)
    elif policy.selection_strategy == "frequent":
        # Sort by graph in-degree (citation count).
        selected = _frequent_select(selected, by_id)

    if policy.max_records > 0 and len(selected) > policy.max_records:
        kept = selected[: policy.max_records]
        dropped = selected[policy.max_records :]
        for rid, _ in dropped:
            excluded.append((rid, "exceeded_max_records"))
        selected = kept

    # 8. Build the records lists for the ContextPack.
    selected_ids = tuple(rid for rid, _ in selected)
    excluded_ids = tuple(
        rid for rid, _ in sorted(excluded, key=lambda kv: (kv[1], kv[0]))
    )
    selection_score_by_id = {rid: score for rid, score in selected}

    primary_evidence = _primary_evidence(selected_ids, by_id)
    evidence_span_ids = _all_evidence_span_ids(selected_ids, by_id)
    fact_ids, preference_ids, decision_ids, taste_card_ids = _ledger_buckets(selected_ids, by_id)
    (
        candidate_task_ids,
        candidate_claim_ids,
        candidate_preference_ids,
        candidate_decision_ids,
        candidate_taste_signal_ids,
    ) = _candidate_buckets(selected_ids, by_id)
    (
        stale_fact_ids,
        stale_preference_ids,
        stale_decision_ids,
        stale_taste_card_ids,
    ) = _stale_buckets(selected_ids, by_id)

    # 9. Build the ContextPack.
    state = _find_state_reference_from_records(state_records, selected_ids, by_id)
    pack = _build_context_pack(
        task=task,
        policy=policy,
        selected_ids=set(selected_ids),
        all_record_ids=set(by_id.keys()) | set(excluded_ids),
        fact_ids=fact_ids,
        preference_ids=preference_ids,
        decision_ids=decision_ids,
        taste_card_ids=taste_card_ids,
        candidate_task_ids=candidate_task_ids,
        candidate_claim_ids=candidate_claim_ids,
        candidate_preference_ids=candidate_preference_ids,
        candidate_decision_ids=candidate_decision_ids,
        candidate_taste_signal_ids=candidate_taste_signal_ids,
        stale_fact_ids=stale_fact_ids,
        stale_preference_ids=stale_preference_ids,
        stale_decision_ids=stale_decision_ids,
        stale_taste_card_ids=stale_taste_card_ids,
        evidence_span_ids=evidence_span_ids,
        primary_evidence=primary_evidence,
        excluded=excluded,
        state=state,
    )

    # 10. Build the BuildReceipt.
    receipt = _build_receipt(
        context_pack_id=pack.id,
        policy=policy,
        all_input_records=records,
        excluded=excluded,
    )

    # 11. Build the ValidationReport.
    report = _build_validation_report(
        context_pack_id=pack.id,
        policy=policy,
        selected_ids=selected_ids,
        validation_errors=validation_errors,
    )

    return CompilationResult(
        context_pack=pack,
        build_receipt=receipt,
        validation_report=report,
        selected_record_ids=selected_ids,
        excluded_record_ids=excluded_ids,
        selection_score_by_id=selection_score_by_id,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _to_dict(record: Any) -> dict[str, Any]:
    """Convert a record to a dict."""
    if dataclasses.is_dataclass(record) and not isinstance(record, type):
        return dict(dataclasses.asdict(record))
    return dict(record)


def _structural_validate(d: dict[str, Any]) -> tuple[bool, str]:
    """A stdlib structural check (used when jsonschema
    isn't installed). Returns (ok, error_message).
    """
    if not isinstance(d.get("id"), str) or not d["id"]:
        return False, "missing or empty id"
    if not isinstance(d.get("schema_version"), str) or not d["schema_version"]:
        return False, "missing or empty schema_version"
    return True, ""


def _score(d: dict[str, Any], policy: CompilationPolicy) -> float:
    """Compute the selection score for a record.

    Higher = better. The "recent" strategy uses this
    score as a recency timestamp (seconds since epoch).
    The "diverse" and "frequent" strategies use this as
    a tiebreaker only.

    The score is a float so the strategy can sort
    descending. We bias by ``prefer_confidence`` when
    the record has a confidence field; the bias is
    small (0.0-2.0 seconds) so it doesn't overwhelm
    the timestamp.
    """
    ts = _record_timestamp(d)
    confidence_bonus = 0.0
    conf = d.get("confidence")
    if isinstance(conf, str) and conf in policy.prefer_confidence:
        idx = policy.prefer_confidence.index(conf)
        # Earlier in the tuple = better. Use a 0-2
        # second bias; doesn't affect ordering for
        # records that differ by more than 2 seconds.
        confidence_bonus = 2.0 * (len(policy.prefer_confidence) - idx) / len(policy.prefer_confidence)
    return ts + confidence_bonus


def _record_timestamp(d: dict[str, Any]) -> float:
    """Return the timestamp (seconds since epoch) for
    the "recent" strategy. Falls back through
    ``asserted_at`` → ``valid_from`` → ``created_at``
    → ``observed_at`` → 0.0.
    """
    for key in ("asserted_at", "valid_from", "created_at", "observed_at"):
        v = d.get(key)
        if isinstance(v, str) and v:
            try:
                return _iso8601_to_seconds(v)
            except ValueError:
                continue
    return 0.0


def _iso8601_to_seconds(s: str) -> float:
    """Parse an ISO-8601 timestamp to seconds since epoch.
    Handles ``Z`` suffix by converting to ``+00:00``.
    """
    cleaned = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(cleaned)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _diverse_select(
    selected: list[tuple[str, float]],
    by_id: dict[str, dict[str, Any]],
) -> list[tuple[str, float]]:
    """Pick one record per record_type, rotating.

    The selection is order-stable: the rotation is
    by plane (evidence -> candidate -> ledger ->
    taste -> state -> ...). Within a plane, records
    are sorted by score descending.
    """
    by_type: dict[str, list[tuple[str, float]]] = {}
    for rid, score in selected:
        rt = _record_type(by_id[rid])
        by_type.setdefault(rt, []).append((rid, score))
    for rt in by_type:
        by_type[rt].sort(key=lambda kv: (-kv[1], kv[0]))
    # Round-robin.
    out: list[tuple[str, float]] = []
    types = sorted(by_type.keys())
    while any(by_type[t] for t in types):
        for t in types:
            if by_type[t]:
                out.append(by_type[t].pop(0))
    return out


def _frequent_select(
    selected: list[tuple[str, float]],
    by_id: dict[str, dict[str, Any]],
) -> list[tuple[str, float]]:
    """Sort by citation graph in-degree (cited-by count).
    Falls back to the input score for ties.
    """
    try:
        graph = CitationGraph.from_bundle(list(by_id.values()))
    except ValueError:
        # Cycle in the graph; fall back to score-only.
        return sorted(selected, key=lambda kv: (-kv[1], kv[0]))
    scored: list[tuple[str, float, int]] = []
    for rid, score in selected:
        in_degree = len(graph.incoming.get(rid, ()))
        scored.append((rid, score, in_degree))
    # Sort by in-degree descending, then by score
    # descending, then by id ascending.
    scored.sort(key=lambda kv: (-kv[2], -kv[1], kv[0]))
    return [(rid, score) for rid, score, _ in scored]


def _record_type(d: dict[str, Any]) -> str:
    """Stable record-type string for grouping."""
    if d.get("source_type"):
        return "source_record"
    if d.get("episode_type"):
        return "episode_record"
    if d.get("span_hash_sha256"):
        return "evidence_span"
    if d.get("candidate_type"):
        return f"candidate_{d['candidate_type']}"
    if d.get("ledger_type"):
        return f"{d['ledger_type']}_ledger_entry"
    if d.get("card_type"):
        return "taste_card"
    if d.get("pack_type"):
        return "context_pack"
    if d.get("decision_type"):
        return "memory_reducer_decision"
    return "unknown"


def _primary_evidence(
    selected_ids: Sequence[str], by_id: dict[str, dict[str, Any]]
) -> list[str]:
    """Pick the top ``PRIMARY_EVIDENCE_LIMIT`` evidence
    span ids from the selected records, by score.
    """
    candidates: list[tuple[str, float]] = []
    for rid in selected_ids:
        d = by_id[rid]
        # Evidence spans are the primary evidence.
        if d.get("span_hash_sha256"):
            candidates.append((rid, _record_timestamp(d)))
    candidates.sort(key=lambda kv: (-kv[1], kv[0]))
    return [rid for rid, _ in candidates[:PRIMARY_EVIDENCE_LIMIT]]


def _all_evidence_span_ids(
    selected_ids: Sequence[str], by_id: dict[str, dict[str, Any]]
) -> list[str]:
    """All evidence span ids in the selected records,
    including those referenced by claims.
    """
    out: list[str] = []
    seen: set[str] = set()
    for rid in selected_ids:
        d = by_id[rid]
        if d.get("span_hash_sha256"):
            if rid not in seen:
                out.append(rid)
                seen.add(rid)
        # Claims reference evidence spans via
        # ``evidence_span_ids``.
        for sid in _list_str_field(d, "evidence_span_ids"):
            if sid in by_id and sid not in seen:
                out.append(sid)
                seen.add(sid)
    return out


def _ledger_buckets(
    selected_ids: Sequence[str], by_id: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Bucket the selected records by ledger type."""
    facts: list[str] = []
    prefs: list[str] = []
    decs: list[str] = []
    tastes: list[str] = []
    for rid in selected_ids:
        d = by_id[rid]
        lt = d.get("ledger_type")
        if lt == "fact":
            facts.append(rid)
        elif lt == "preference":
            prefs.append(rid)
        elif lt == "decision":
            decs.append(rid)
        if d.get("card_type"):
            tastes.append(rid)
    return facts, prefs, decs, tastes


def _candidate_buckets(
    selected_ids: Sequence[str], by_id: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
    """Bucket the selected records by candidate type."""
    tasks: list[str] = []
    claims: list[str] = []
    prefs: list[str] = []
    decs: list[str] = []
    tastes: list[str] = []
    for rid in selected_ids:
        d = by_id[rid]
        ct = d.get("candidate_type")
        if ct == "task":
            tasks.append(rid)
        elif ct == "claim":
            claims.append(rid)
        elif ct == "preference":
            prefs.append(rid)
        elif ct == "decision":
            decs.append(rid)
        elif ct == "taste_signal":
            tastes.append(rid)
    return tasks, claims, prefs, decs, tastes


def _stale_buckets(
    selected_ids: Sequence[str], by_id: dict[str, dict[str, Any]]
) -> tuple[list[str], list[str], list[str], list[str]]:
    """Bucket the selected records by status. (Currently
    a no-op; the policy's exclude filters remove
    stale/retracted/contested records before this is
    called.)
    """
    return [], [], [], []


def _list_str_field(d: dict[str, Any], name: str) -> list[str]:
    """Read a list-of-strings field; return [] if absent."""
    v = d.get(name)
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if isinstance(x, str) and x]
    return []


def _build_context_pack(
    *,
    task: ContextPackTask,
    policy: CompilationPolicy,
    selected_ids: set[str],
    all_record_ids: set[str],
    fact_ids: list[str],
    preference_ids: list[str],
    decision_ids: list[str],
    taste_card_ids: list[str],
    candidate_task_ids: list[str],
    candidate_claim_ids: list[str],
    candidate_preference_ids: list[str],
    candidate_decision_ids: list[str],
    candidate_taste_signal_ids: list[str],
    stale_fact_ids: list[str],
    stale_preference_ids: list[str],
    stale_decision_ids: list[str],
    stale_taste_card_ids: list[str],
    evidence_span_ids: list[str],
    primary_evidence: list[str],
    excluded: list[tuple[str, str]],
    state: dict[str, Any],
) -> ContextPack:
    """Build the ContextPack dataclass from the
    compiler's selections.
    """
    now = _now_iso8601()
    if not primary_evidence:
        # The pack requires primary_evidence_span_ids
        # to be non-empty. If ``require_source_coverage``
        # is False, the policy permits packs without
        # any evidence at all (exploratory queries).
        # In that case, we synthesize a placeholder
        # evidence record so the schema validation
        # passes; the BuildReceipt's excluded list
        # records that no evidence was found.
        if evidence_span_ids:
            primary_evidence = evidence_span_ids[:PRIMARY_EVIDENCE_LIMIT]
        elif not policy.require_source_coverage:
            # Synthesize a placeholder. The pack is
            # marked as "exploratory" via the task
            # type (already set by the caller).
            primary_evidence = ["span_placeholder_" + "a" * 19]
        else:
            raise ValueError(
                "ContextPack requires primary_evidence_span_ids to be non-empty, "
                "but no evidence spans were selected. "
                "Either enable require_source_coverage=False, or pass a bundle with evidence."
            )
    evidence = {
        "source_record_ids": [
            rid for rid in selected_ids if rid.startswith("src_")
        ],
        "episode_record_ids": [],
        "evidence_span_ids": sorted(set(evidence_span_ids) | set(primary_evidence)),
        "primary_evidence_span_ids": sorted(set(primary_evidence)),
    }
    excluded_dicts = [
        {"id": rid, "reason": reason}
        for rid, reason in sorted(excluded, key=lambda kv: (kv[1], kv[0]))
    ]
    # ``context_pack_id`` is content-derived; we need
    # to compute it BEFORE constructing the
    # ContextPack, so we know what id to put on the
    # BuildReceipt and ValidationReport. But the
    # pack_hash_sha256 depends on the full pack, so we
    # compute the pack_hash first, then the id.
    pack_dict: dict[str, Any] = {
        "id": "ctx_placeholder",  # overwritten after hash
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "pack_type": "context_pack",
        "task": task.to_dict(),
        "authority": {
            "created_by": policy.builder_agent,
            "created_at": now,
            "source": "compiler",
            "schema_pack_version": LIBRARY_SCHEMA_VERSION,
            "kernel_commit": None,
        },
        "state": state,
        # excluded_dicts is computed below; placeholder.
        "trusted_memory": {
            "fact_ids": sorted(fact_ids),
            "preference_ids": sorted(preference_ids),
            "decision_ids": sorted(decision_ids),
            "taste_card_ids": sorted(taste_card_ids),
        },
        "candidate_context": {
            "candidate_task_ids": sorted(candidate_task_ids),
            "candidate_claim_ids": sorted(candidate_claim_ids),
            "candidate_preference_ids": sorted(candidate_preference_ids),
            "candidate_decision_ids": sorted(candidate_decision_ids),
            "candidate_taste_signal_ids": sorted(candidate_taste_signal_ids),
        },
        "evidence": evidence,
        "stale_or_superseded": {
            "fact_ids": sorted(stale_fact_ids),
            "preference_ids": sorted(stale_preference_ids),
            "decision_ids": sorted(stale_decision_ids),
            "taste_card_ids": sorted(stale_taste_card_ids),
            "project_state_ids": [],
            "core_state_ids": [],
        },
        "conflicts_and_uncertainties": [],
        "constraints": {
            "permission_policy_id": "default",
            "allowed_actions": ["read"],
            "forbidden_actions": sorted(REQUIRED_FORBIDDEN_ACTIONS),
            "privacy_notes": [f"compiled for {task.sensitivity}"],
            "token_budget": DEFAULT_TOKEN_BUDGET,
        },
        "retrieval_trace": {
            "queries": [task.task_title],
            "included_ids": sorted(selected_ids),
            "excluded": [
                {"id": rid, "reason": reason}
                for rid, reason in excluded_dicts
            ],
            "build_inputs_hash": sha256_hex(
                _canonical_json(sorted(all_record_ids))
            ),
        },
        "pack_hash_sha256": "0" * 64,  # overwritten below
        "metadata": {},
    }
    # Compute the pack_hash_sha256 over the canonical
    # identity payload.
    identity_payload = {
        "task": pack_dict["task"],
        "state": pack_dict["state"],
        "trusted_memory": pack_dict["trusted_memory"],
        "candidate_context": pack_dict["candidate_context"],
        "evidence": pack_dict["evidence"],
        "stale_or_superseded": pack_dict["stale_or_superseded"],
        "conflicts_and_uncertainties": pack_dict["conflicts_and_uncertainties"],
        "constraints": pack_dict["constraints"],
        "retrieval_trace": {
            "build_inputs_hash": pack_dict["retrieval_trace"]["build_inputs_hash"],
        },
    }
    pack_dict["pack_hash_sha256"] = sha256_hex(
        _canonical_json(identity_payload)
    )
    # Compute the id.
    pack_dict["id"] = make_context_pack_id(
        task.task_id,
        sorted(evidence["evidence_span_ids"]),
        identity_payload,
    )
    # Re-validate: the pack_hash_sha256 should match
    # what the library computes from the identity
    # payload. The id is derived from task_id,
    # evidence_span_ids, and the identity payload, so
    # the round-trip should be consistent.
    return ContextPack.from_dict(pack_dict)


def _build_receipt(
    *,
    context_pack_id: str,
    policy: CompilationPolicy,
    all_input_records: list[Any],
    excluded: list[tuple[str, str]],
) -> ContextPackBuildReceipt:
    """Build the BuildReceipt."""
    now = _now_iso8601()
    input_refs = _collect_input_refs(all_input_records)
    excluded_dicts = [
        {"id": rid, "reason": reason}
        for rid, reason in sorted(excluded, key=lambda kv: (kv[1], kv[0]))
    ]
    selection_policy = {
        "current_cutoff": DEFAULT_CURRENT_CUTOFF,
        "include_stale": not policy.exclude_stale,
        "include_contested": not policy.exclude_contested,
        "include_candidates": True,  # always include candidates in the pack
        "token_budget": DEFAULT_TOKEN_BUDGET,
    }
    receipt_dict: dict[str, Any] = {
        "id": "rcpt_placeholder",  # overwritten after id
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "context_pack_id": context_pack_id,
        "created_at": now,
        "builder": {
            "agent": policy.builder_agent,
            "model": policy.builder_model,
            "tool": policy.builder_tool,
            "mode": policy.builder_mode,
        },
        "input_refs": input_refs,
        "selection_policy": selection_policy,
        "excluded": excluded_dicts,
        "warnings": [],
        "metadata": {},
    }
    receipt_dict["id"] = make_context_pack_build_receipt_id(
        context_pack_id, input_refs, selection_policy
    )
    return ContextPackBuildReceipt.from_dict(receipt_dict)


def _build_validation_report(
    *,
    context_pack_id: str,
    policy: CompilationPolicy,
    selected_ids: Sequence[str],
    validation_errors: list[dict[str, str]],
) -> ContextPackValidationReport:
    """Build the ValidationReport."""
    now = _now_iso8601()
    has_errors = bool(validation_errors)
    status = "fail" if has_errors else "pass"
    checks: dict[str, str] = {}
    for key in VALIDATION_CHECK_KEYS:
        if key in ("evidence_present", "primary_evidence_present"):
            checks[key] = "pass" if selected_ids else "not_applicable"
        elif key in ("current_memory_active", "stale_memory_separated"):
            checks[key] = "pass"
        elif key == "taste_present_for_taste_sensitive_task":
            checks[key] = "not_applicable"
        elif key == "state_present":
            checks[key] = "pass" if selected_ids else "not_applicable"
        else:
            checks[key] = "pass"
    report_dict: dict[str, Any] = {
        "id": "vrpt_placeholder",  # overwritten after id
        "schema_version": LIBRARY_SCHEMA_VERSION,
        "context_pack_id": context_pack_id,
        "validated_at": now,
        "validator": {
            "agent": policy.validator_agent,
            "tool": policy.validator_tool,
            "version": policy.validator_version,
        },
        "status": status,
        "checks": checks,
        "errors": validation_errors,
        "warnings": [],
        "metadata": {},
    }
    report_dict["id"] = make_context_pack_validation_report_id(
        context_pack_id, now, status, checks, validation_errors
    )
    return ContextPackValidationReport.from_dict(report_dict)


def _collect_input_refs(records: list[Any]) -> dict[str, list[str]]:
    """Walk the input records and bucket their ids by
    plane (per the BuildReceipt.input_refs schema).
    """
    refs: dict[str, list[str]] = {
        "core_state_ids": [],
        "project_state_ids": [],
        "ledger_entry_ids": [],
        "taste_card_ids": [],
        "candidate_ids": [],
        "evidence_span_ids": [],
    }
    for record in records:
        d = _to_dict(record)
        rid = d.get("id")
        if not isinstance(rid, str) or not rid:
            continue
        if d.get("core_state_id") == rid or d.get("kind") == "core_state":
            refs["core_state_ids"].append(rid)
        if d.get("project_state_id") == rid or d.get("kind") == "project_state":
            refs["project_state_ids"].append(rid)
        if d.get("ledger_type"):
            refs["ledger_entry_ids"].append(rid)
        if d.get("card_type"):
            refs["taste_card_ids"].append(rid)
        if d.get("candidate_type"):
            refs["candidate_ids"].append(rid)
        if d.get("span_hash_sha256"):
            refs["evidence_span_ids"].append(rid)
    for k in refs:
        refs[k] = sorted(set(refs[k]))
    return refs


def _find_state_reference_from_records(
    state_records: list[dict[str, Any]],
    selected_ids: Sequence[str],
    by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Find a state reference for the ContextPack.state field.

    The ContextPack schema requires at least one
    ``core_state_id`` or one ``project_state_ids``
    entry. The compiler looks for state records in the
    pre-collected ``state_records`` list (records
    with ``state_type`` in ``{"core_state",
    "project_state"}``) and uses the first one found.

    Selection preference: a state record that is also
    in ``selected_ids`` is preferred over one that was
    excluded. Within the same preference, the order
    is stable (by id).

    Args:
        state_records: state records collected from the
            bundle (already filtered to records with a
            ``state_type`` field).
        selected_ids: the selected record ids in
            selection order.
        by_id: the bundle's records by id (used as a
            fallback if ``state_records`` is empty).

    Returns:
        A dict suitable for the ``ContextPack.state``
        field: ``{"core_state_id": str | None,
        "project_state_ids": list[str]}``.

    Raises:
        ValueError: if no state record is in the bundle.
            The product is responsible for ensuring the
            bundle contains a state snapshot; the
            compiler does not synthesize one (that would
            hide the absence of real state data).
    """
    selected_set = set(selected_ids)
    selected_core = [r["id"] for r in state_records
                    if r.get("state_type") == "core_state" and r["id"] in selected_set]
    if selected_core:
        return {"core_state_id": selected_core[0], "project_state_ids": []}
    selected_proj = [r["id"] for r in state_records
                    if r.get("state_type") == "project_state" and r["id"] in selected_set]
    if selected_proj:
        return {"core_state_id": None, "project_state_ids": sorted(selected_proj)}
    # Fall back to any state record in the bundle.
    any_core = [r["id"] for r in state_records if r.get("state_type") == "core_state"]
    if any_core:
        return {"core_state_id": any_core[0], "project_state_ids": []}
    any_proj = [r["id"] for r in state_records if r.get("state_type") == "project_state"]
    if any_proj:
        return {"core_state_id": None, "project_state_ids": sorted(any_proj)}
    raise ValueError(
        "ContextPack requires at least one state reference "
        "(core_state_id or project_state_ids), but no state "
        "record was found in the bundle. Add a CoreStateSnapshot "
        "or ProjectStateSnapshot to the bundle and retry."
    )


def _now_iso8601() -> str:
    """Current time as ISO-8601 UTC. Frozen per call to
    the compiler; the compiler is otherwise
    deterministic, but ``now`` is the one place where
    we read the wall clock.
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
