"""Internal canonical JSON and digest helpers.

This module defines canonicalization v1 for the package. It is intentionally
private for now: public helper names remain in their existing modules while
delegating here, so centralization does not expand the API surface.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CANONICALIZATION_VERSION = "canonical-json-v1"
CANONICAL_JSON_SORT_KEYS = True
CANONICAL_JSON_SEPARATORS = (",", ":")
CANONICAL_JSON_ENSURE_ASCII = False


def canonical_json(value: Any) -> str:
    """Return the v1 canonical JSON string for ids and fingerprints."""
    return json.dumps(
        value,
        sort_keys=CANONICAL_JSON_SORT_KEYS,
        separators=CANONICAL_JSON_SEPARATORS,
        ensure_ascii=CANONICAL_JSON_ENSURE_ASCII,
    )


def sha256_hex(value: str | bytes) -> str:
    """Return a lowercase SHA-256 hex digest for UTF-8 text or bytes."""
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()
