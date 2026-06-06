"""Access control + bundle scope primitives.

The library's record types carry a ``privacy_class`` field with
one of five values from :data:`agent_memory_contracts.evidence_contracts.PRIVACY_CLASSES`:

- ``"public"``            (least restricted)
- ``"internal"``
- ``"private"``
- ``"sensitive"``
- ``"highly_sensitive"``   (most restricted)

A :class:`BundleScope` is a frozen description of "what subset
of a bundle is allowed at this scope." A :class:`AccessDecision`
is the per-record outcome of a scope check (allow, redact, or
drop). The two free functions :func:`check_access` and
:func:`scope_bundle` are the headline primitives for "given a
record (or a bundle), should it be exposed at this scope?"

This module does not introduce a user/team/role model. It is a
**data-classification** primitive: the product is responsible
for mapping principals (people, teams, customers) to scopes
(``public``, ``team``, ``customer``, ``private``). The library
gives the product the primitive to check and filter; the
product applies it.

In v0.9.0, the ``redact`` action is reserved but never
returned by :func:`check_access` (we don't have field-level
redaction yet). The action is in the public surface so a
future sprint can add a redact step without breaking call
sites.

Like the rest of the bundle primitives, this module is
standard-library only.

.. versionadded:: 0.9.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Mapping


# ---------------------------------------------------------------------------
# Privacy class ordering
# ---------------------------------------------------------------------------


#: Strict linear ordering of privacy classes from least to most
#: restricted. The library's existing
#: :data:`agent_memory_contracts.evidence_contracts.PRIVACY_CLASSES`
#: set is the source of truth for valid values; this tuple is
#: the ordering used by access checks.
PRIVACY_CLASS_ORDER: tuple[str, ...] = (
    "public",
    "internal",
    "private",
    "sensitive",
    "highly_sensitive",
)


#: Default privacy class for records that do not carry a
#: ``privacy_class`` field. Matches the library's working
#: default for un-classified data.
DEFAULT_PRIVACY_CLASS: str = "internal"


def _privacy_class_index(privacy_class: str) -> int:
    """Return the index of ``privacy_class`` in :data:`PRIVACY_CLASS_ORDER`.

    Raises:
        ValueError: if ``privacy_class`` is not in
            :data:`PRIVACY_CLASS_ORDER`. The library's
            ``PRIVACY_CLASSES`` set is the source of truth for
            valid values; an unknown class is a contract
            violation.
    """
    try:
        return PRIVACY_CLASS_ORDER.index(privacy_class)
    except ValueError as exc:
        raise ValueError(
            f"unknown privacy_class: {privacy_class!r}; "
            f"expected one of {PRIVACY_CLASS_ORDER}"
        ) from exc


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BundleScope:
    """A frozen description of the "view" of a bundle.

    The :attr:`max_privacy_class` field is the gate: a record is
    allowed iff its privacy class is at-or-below the gate in
    :data:`PRIVACY_CLASS_ORDER`. The :attr:`allowed_record_types`
    field is an optional whitelist of record-type strings
    (``"source_record"``, ``"fact_ledger_entry"``, etc.). The
    :attr:`name` field is a human-readable label for the
    scope, used in product UIs ("Bundle shared at scope: team").

    The default factory :func:`team_scope` returns the most
    common scope ("share with my team"); the other factories
    cover the other common cases.
    """

    max_privacy_class: str = "internal"
    allowed_record_types: frozenset[str] | None = None
    name: str = "team"

    def __post_init__(self) -> None:
        # Validate the max_privacy_class at construction time so
        # that bad scopes are caught before any record is checked.
        _privacy_class_index(self.max_privacy_class)
        if self.allowed_record_types is not None and not isinstance(
            self.allowed_record_types, frozenset
        ):
            # The dataclass type hint is the source of truth;
            # this check catches a passed-in mutable set.
            object.__setattr__(
                self,
                "allowed_record_types",
                frozenset(self.allowed_record_types),
            )


@dataclass(frozen=True)
class AccessDecision:
    """The per-record outcome of a scope check.

    In v0.9.0, the :attr:`action` is either ``"allow"`` (the
    record is within scope) or ``"drop"`` (the record is
    outside scope and will be removed from the filtered
    bundle). The ``"redact"`` action is reserved for a
    future sprint that adds field-level redaction.

    The :attr:`reason` is a human-readable English string,
    suitable for product UIs and audit logs. Programmatic
    branching should use :attr:`action`, not :attr:`reason`.
    """

    record_id: str
    action: Literal["allow", "redact", "drop"]
    reason: str

    def __repr__(self) -> str:
        return f"AccessDecision({self.record_id!r}: {self.action} - {self.reason})"


@dataclass(frozen=True)
class AccessSummary:
    """An aggregate summary of a list of :class:`AccessDecision`s.

    Useful for product dashboards: "you tried to share 100
    records; 62 are allowed, 0 will be redacted, 38 will be
    dropped." The :attr:`by_privacy_class` field is a count
    per privacy class for the *records in the input bundle*,
    not for the decisions; the :attr:`by_action` field is a
    count per action.
    """

    total: int
    allowed: int
    redacted: int
    dropped: int
    by_privacy_class: Mapping[str, int] = field(default_factory=dict)
    by_action: Mapping[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Free functions
# ---------------------------------------------------------------------------


def _record_privacy_class(record: Any) -> str:
    """Return the privacy class of a record, defaulting to
    :data:`DEFAULT_PRIVACY_CLASS` for records that lack the
    field.
    """
    if record is None:
        return DEFAULT_PRIVACY_CLASS
    if hasattr(record, "privacy_class"):
        value = getattr(record, "privacy_class", None)
        if isinstance(value, str) and value:
            return value
    if isinstance(record, Mapping):
        value = record.get("privacy_class")
        if isinstance(value, str) and value:
            return value
    return DEFAULT_PRIVACY_CLASS


def _record_id(record: Any) -> str:
    """Return the id of a record (dataclass or dict), or empty string."""
    if record is None:
        return ""
    if hasattr(record, "id"):
        value = getattr(record, "id", None)
        if isinstance(value, str) and value:
            return value
    if isinstance(record, Mapping):
        value = record.get("id")
        if isinstance(value, str) and value:
            return value
    return ""


def _record_type_string(record: Any) -> str:
    """Return a stable record-type string for filtering.

    Dataclass records use the class name (snake_cased); dict
    records use a discriminator field. The result is best-
    effort; the caller is responsible for passing meaningful
    values in :attr:`BundleScope.allowed_record_types`.
    """
    if record is None:
        return ""
    cls = getattr(record, "__class__", None)
    if cls is not None and hasattr(cls, "__name__") and cls.__name__ != "dict":
        name = cls.__name__
        # Snake-case the CamelCase name without importing ``re``.
        out: list[str] = []
        for i, ch in enumerate(name):
            if ch.isupper() and i > 0 and not name[i - 1].isupper():
                out.append("_")
            out.append(ch.lower())
        return "".join(out)
    if isinstance(record, Mapping):
        if "ledger_type" in record:
            return str(record.get("ledger_type", "ledger_entry"))
        if "candidate_type" in record:
            return str(record.get("candidate_type", "candidate"))
        if "source_type" in record:
            return "source_record"
        if "episode_type" in record:
            return "episode_record"
        if "context_pack_kind" in record or "primary_evidence_span_ids" in record:
            return "context_pack"
        if "span_hash_sha256" in record:
            return "evidence_span"
    return ""


def check_access(record: Any, scope: BundleScope) -> AccessDecision:
    """Check whether ``record`` is allowed at ``scope``.

    Returns an :class:`AccessDecision`. The :attr:`action` is
    ``"allow"`` if the record is within the scope, ``"drop"``
    otherwise. In v0.9.0, the ``"redact"`` action is never
    returned by this function (no field-level redaction yet).

    The :attr:`reason` is a human-readable English string
    suitable for product UIs and audit logs. Examples:

    - ``"privacy_class=public <= max=internal"``
    - ``"privacy_class=highly_sensitive > max=internal"``
    - ``"record_type=decision_ledger_entry not in allowed_record_types"``

    Args:
        record: a record (dataclass, dict, or Mapping).
        scope: a :class:`BundleScope`.

    Returns:
        An :class:`AccessDecision`.

    Raises:
        ValueError: if the record's ``privacy_class`` is not
            in :data:`PRIVACY_CLASS_ORDER` (a contract
            violation; the library's PRIVACY_CLASSES set is
            the source of truth for valid values).
    """
    rid = _record_id(record)
    pc = _record_privacy_class(record)
    pc_index = _privacy_class_index(pc)
    max_index = _privacy_class_index(scope.max_privacy_class)

    if pc_index > max_index:
        return AccessDecision(
            record_id=rid,
            action="drop",
            reason=f"privacy_class={pc} > max={scope.max_privacy_class}",
        )

    if scope.allowed_record_types is not None:
        rt = _record_type_string(record)
        if rt and rt not in scope.allowed_record_types:
            return AccessDecision(
                record_id=rid,
                action="drop",
                reason=(
                    f"record_type={rt} not in "
                    f"allowed_record_types={sorted(scope.allowed_record_types)}"
                ),
            )

    return AccessDecision(
        record_id=rid,
        action="allow",
        reason=f"privacy_class={pc} <= max={scope.max_privacy_class}",
    )


def scope_bundle(
    bundle: Iterable[Any], scope: BundleScope
) -> tuple[list[Any], list[AccessDecision]]:
    """Filter ``bundle`` to the records allowed at ``scope``.

    Returns a 2-tuple ``(filtered_bundle, decisions)``. The
    ``filtered_bundle`` is a list of the records whose
    :func:`check_access` returned ``action="allow"``, in the
    same relative order as the input. The ``decisions`` list
    contains the per-record :class:`AccessDecision` for every
    record in the input, in the same order.

    The default action is ``drop`` for records outside the
    scope. The ``redact`` action is reserved for v0.9.x when
    field-level redaction is implemented.

    Args:
        bundle: an iterable of records.
        scope: a :class:`BundleScope`.

    Returns:
        A 2-tuple ``(filtered_bundle, decisions)``.
    """
    records = list(bundle)
    filtered: list[Any] = []
    decisions: list[AccessDecision] = []
    for record in records:
        decision = check_access(record, scope)
        decisions.append(decision)
        if decision.action == "allow":
            filtered.append(record)
    return filtered, decisions


def summarize_access(decisions: Iterable[AccessDecision]) -> AccessSummary:
    """Aggregate a list of :class:`AccessDecision`s into a summary.

    Args:
        decisions: an iterable of :class:`AccessDecision`.

    Returns:
        An :class:`AccessSummary` with counts per action and
        per privacy class. The ``by_privacy_class`` field
        is always populated from the ``reason`` strings
        (parsing ``"privacy_class=X <= max=Y"`` or
        ``"privacy_class=X > max=Y"``); records whose
        reason has no privacy class are not counted in
        ``by_privacy_class``.
    """
    decisions_list = list(decisions)
    total = len(decisions_list)
    allowed = sum(1 for d in decisions_list if d.action == "allow")
    redacted = sum(1 for d in decisions_list if d.action == "redact")
    dropped = sum(1 for d in decisions_list if d.action == "drop")
    by_action: dict[str, int] = {}
    for d in decisions_list:
        by_action[d.action] = by_action.get(d.action, 0) + 1
    # Count by privacy class by parsing the reason. The reason
    # format is documented and stable: "privacy_class=X <= max=Y"
    # or "privacy_class=X > max=Y" or
    # "record_type=... not in allowed_record_types" (no
    # privacy class in the reason for type-filtered records,
    # so they are not counted in by_privacy_class).
    by_privacy_class: dict[str, int] = {}
    for d in decisions_list:
        reason = d.reason
        marker = "privacy_class="
        if marker in reason:
            # Extract the substring after "privacy_class=" up
            # to the next whitespace.
            start = reason.index(marker) + len(marker)
            end = len(reason)
            for i in range(start, len(reason)):
                if reason[i] in (" ", "<", ">", "="):
                    end = i
                    break
            pc = reason[start:end]
            by_privacy_class[pc] = by_privacy_class.get(pc, 0) + 1
    return AccessSummary(
        total=total,
        allowed=allowed,
        redacted=redacted,
        dropped=dropped,
        by_privacy_class=dict(by_privacy_class),
        by_action=dict(by_action),
    )


# ---------------------------------------------------------------------------
# Scope factories
# ---------------------------------------------------------------------------


def public_scope() -> BundleScope:
    """Return a scope that allows only ``public`` records."""
    return BundleScope(max_privacy_class="public", name="public")


def team_scope() -> BundleScope:
    """Return a scope that allows ``public`` and ``internal`` records.

    The most common scope. "Share with my team."
    """
    return BundleScope(max_privacy_class="internal", name="team")


def customer_scope() -> BundleScope:
    """Return a scope that allows up to ``private`` records.

    "Share with a paying customer." Drops ``sensitive`` and
    ``highly_sensitive`` records.
    """
    return BundleScope(max_privacy_class="private", name="customer")


def private_scope() -> BundleScope:
    """Return a scope that allows all records (no filtering).

    The default per-record gate is the most permissive
    (``highly_sensitive``). Use this for the
    "owner-only" or "self" access mode.
    """
    return BundleScope(max_privacy_class="highly_sensitive", name="private")
