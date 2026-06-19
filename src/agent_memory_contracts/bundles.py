"""Bundle-level primitives for the memory integrity model.

A *bundle* is an ordered set of records (sources, spans, candidates,
reducer decisions, ledger entries, taste cards, state snapshots,
context packs, etc.) that should be treated as a single unit for
the purposes of change detection, caching, audit, and idempotent
rebuilds.

The library's id helpers already use SHA-256 of canonical JSON to
give every record a content-derived identifier. This module extends
the same trick to the bundle as a whole: a deterministic
fingerprint that is sensitive to any byte change in any record,
but insensitive to the order of the input iterable.

The intended use cases:

- **Cache key.** A ContextPack rebuild can be cached by the
  fingerprint of the inputs it consumed. Rebuilding with the same
  inputs produces the same fingerprint; rebuild with any change
  busts the cache.
- **Idempotency token.** Two pipeline runs that processed
  the same records are guaranteed to produce the same fingerprint,
  so a downstream write layer can dedupe on the hash.
- **Change detection.** Compare two fingerprints of the same
  bundle to know if anything changed; compare a current
  fingerprint to a stored one to know what to ship.
- **Audit chain.** A log of (timestamp, fingerprint) pairs lets
  you replay a bundle's state at any point in time.

The function is stdlib-only. No new dependencies.

.. versionadded:: 0.3.0
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Iterable, Literal, Mapping

from ._canonical import canonical_json
from .evidence_ids import sha256_hex

#: Separator inserted between records' canonical JSON in the bundle
#: hash. A newline is safe: canonical JSON cannot contain an
#: unescaped newline inside a string (it would have to be written
#: as ``\\n``), so a raw ``\n`` cannot appear in any record's
#: canonical form. Using it as a separator therefore cannot
#: collide with record content.
_SEPARATOR = "\n"

DuplicateMode = Literal["last", "first", "raise"]


class DuplicateRecordError(ValueError):
    """Raised when strict duplicate handling rejects a repeated record id.

    The error separates the semantic identity (``id_value``) from the
    record fingerprints of the first and duplicate payloads. Callers can
    therefore distinguish harmless replays from divergent same-id payloads
    without changing any record id semantics.
    """

    def __init__(
        self,
        *,
        id_field: str,
        id_value: str,
        first_fingerprint: str,
        duplicate_fingerprint: str,
    ) -> None:
        self.id_field = id_field
        self.id_value = id_value
        self.first_fingerprint = first_fingerprint
        self.duplicate_fingerprint = duplicate_fingerprint
        self.same_content = first_fingerprint == duplicate_fingerprint
        super().__init__(
            f"duplicate record id {id_value!r} for id_field {id_field!r} "
            f"(same_content={self.same_content}, "
            f"first_fingerprint={first_fingerprint!r}, "
            f"duplicate_fingerprint={duplicate_fingerprint!r})"
        )


def _validate_duplicate_mode(duplicate_mode: DuplicateMode) -> None:
    if duplicate_mode not in ("last", "first", "raise"):
        raise ValueError(
            "duplicate_mode must be 'last', 'first', or 'raise'; "
            f"got {duplicate_mode!r}"
        )


def _record_dict(record: Any) -> dict[str, Any]:
    if is_dataclass(record) and not isinstance(record, type):
        return asdict(record)
    if isinstance(record, Mapping):
        return dict(record)
    # Last resort: try to use it as a Mapping protocol.
    return dict(record)


def _canonical_record(record: Any, id_field: str) -> tuple[str, str]:
    """Return ``(id_value, canonical_json)`` for a single record.

    The record can be a ``dict``, a ``Mapping``, or a dataclass
    instance. For dataclasses, the ``id_field`` is read via
    ``getattr``; the canonical JSON is built from ``asdict``.
    """
    rec_dict = _record_dict(record)
    if is_dataclass(record) and not isinstance(record, type):
        id_value = getattr(record, id_field, "")
    else:
        id_value = rec_dict.get(id_field, "")
    canonical = canonical_json(rec_dict)
    return str(id_value), canonical


def record_fingerprint(record: Any) -> str:
    """Return a SHA-256 hex digest of a single record's canonical JSON.

    Unlike a record's semantic id, this digest is intentionally sensitive
    to every canonical byte in the record, including metadata and the id
    field itself. It is the primitive callers should use when they need to
    detect two payloads with the same semantic id but different content.
    """
    return sha256_hex(canonical_json(_record_dict(record)))


def _raise_duplicate(
    *,
    id_field: str,
    id_value: str,
    first_canonical: str,
    duplicate_canonical: str,
) -> None:
    raise DuplicateRecordError(
        id_field=id_field,
        id_value=id_value,
        first_fingerprint=sha256_hex(first_canonical),
        duplicate_fingerprint=sha256_hex(duplicate_canonical),
    )


def _canonical_records_by_id(
    records: Iterable[Any],
    *,
    id_field: str,
    duplicate_mode: DuplicateMode,
) -> dict[str, str]:
    _validate_duplicate_mode(duplicate_mode)
    by_id: dict[str, str] = {}
    for record in records:
        id_value, canonical = _canonical_record(record, id_field)
        if id_value in by_id:
            if duplicate_mode == "raise":
                _raise_duplicate(
                    id_field=id_field,
                    id_value=id_value,
                    first_canonical=by_id[id_value],
                    duplicate_canonical=canonical,
                )
            if duplicate_mode == "first":
                continue
        by_id[id_value] = canonical
    return by_id


def bundle_fingerprint(
    records: Iterable[Any],
    *,
    id_field: str = "id",
    duplicate_mode: DuplicateMode = "last",
) -> str:
    """Return a deterministic SHA-256 hex digest of a bundle of records.

    Two bundles produce the same fingerprint if and only if they
    contain the same set of records, where two records are
    considered "the same record" if their ``id_field`` values are
    equal. Content changes within any record, or changes in the
    set of record ids, both change the fingerprint.

    Args:
        records: An iterable of records. Each record may be a
            ``dict``, a ``Mapping``, or a dataclass instance.
            Records need not be unique by id; if duplicates appear
            with the same id and same content, the fingerprint is
            unchanged. If duplicates appear with the same id but
            different content, the **last** occurrence wins
            (deterministic for a given input order).
        id_field: The field on each record that identifies it.
            Defaults to ``"id"`` because that is the canonical
            identifier field on every record type in the library.
        duplicate_mode: How to resolve repeated ``id_field`` values
            within the input bundle. ``"last"`` is the legacy default
            and keeps the final occurrence. ``"first"`` keeps the first
            occurrence. ``"raise"`` raises :class:`DuplicateRecordError`
            on any repeated id and includes both record fingerprints.

    Returns:
        A 64-character lowercase hex SHA-256 digest.

    The fingerprint is:

    - **Deterministic.** The same records in any order produce
      the same hash.
    - **Content-sensitive.** Any byte change in any record changes
      the hash.
    - **Set-semantic.** Records are deduplicated by ``id_field``
      before hashing; the bundle is treated as a set, not a list.
    - **Idempotent.** Re-running the same pipeline on the same
      inputs produces the same hash.
    """
    by_id = _canonical_records_by_id(
        records, id_field=id_field, duplicate_mode=duplicate_mode,
    )
    # Sort by id for order-insensitivity. The sort key is the
    # string id; ids in this library are content-derived SHA-256
    # hex strings, so the sort is effectively a content-aware
    # canonical order.
    sorted_payloads = [by_id[k] for k in sorted(by_id.keys())]
    bundle_canonical = _SEPARATOR.join(sorted_payloads)
    return sha256_hex(bundle_canonical)


__all__ = ["DuplicateRecordError", "bundle_fingerprint", "record_fingerprint"]
