"""Stable identifiers for immutable evidence records."""

from __future__ import annotations

from typing import Any

from ._canonical import canonical_json, sha256_hex as _sha256_hex


def sha256_hex(value: str | bytes) -> str:
    return _sha256_hex(value)


def _canonical_json(value: Any) -> str:
    return canonical_json(value)


def _prefixed_id(prefix: str, payload: Any, length: int = 24) -> str:
    digest = sha256_hex(_canonical_json(payload))
    return f"{prefix}_{digest[:length]}"


def make_source_id(source_type: str, origin_uri_or_raw_ref: str | dict[str, Any] | None, content_hash_sha256: str) -> str:
    """Create a stable SourceRecord ID.

    Deliberately excludes volatile fields such as captured_at and parser_version.
    """
    return _prefixed_id(
        "src",
        {
            "source_type": source_type,
            "origin_or_raw_ref": origin_uri_or_raw_ref,
            "content_hash_sha256": content_hash_sha256,
        },
    )


def make_episode_id(source_id: str, episode_type: str, locator_kind: str, locator_value: str) -> str:
    return _prefixed_id(
        "ep",
        {
            "source_id": source_id,
            "episode_type": episode_type,
            "locator_kind": locator_kind,
            "locator_value": locator_value,
        },
    )


def make_span_id(source_id: str, locator_kind: str, locator_value: str) -> str:
    return _prefixed_id(
        "span",
        {
            "source_id": source_id,
            "locator_kind": locator_kind,
            "locator_value": locator_value,
        },
    )
