#!/usr/bin/env python3
"""Audit the public API surface against docs/STABILITY.md.

Walks ``agent_memory_contracts.__all__`` and verifies
that every public name is documented in
``docs/STABILITY.md``. Exits 0 if they match, 1 if
they don't.

Run locally::

    python scripts/audit_public_api.py

The CI workflow runs this on every push to ``main``.
A new public name added to ``__all__`` without a
corresponding entry in ``STABILITY.md`` fails the
audit; the fix is to add the name to the doc in the
same PR.

.. versionadded:: 1.0.0
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STABILITY_DOC = REPO_ROOT / "docs" / "STABILITY.md"


def _public_names() -> list[str]:
    """Return the sorted list of public names from
    ``agent_memory_contracts.__all__``.
    """
    sys.path.insert(0, str(REPO_ROOT / "src"))
    import agent_memory_contracts
    return sorted(getattr(agent_memory_contracts, "__all__", []))


def _documented_names() -> set[str]:
    """Parse ``STABILITY.md`` and return the set of
    names mentioned in any code-span (backticks).
    """
    if not STABILITY_DOC.exists():
        raise SystemExit(f"STABILITY doc not found: {STABILITY_DOC}")
    text = STABILITY_DOC.read_text(encoding="utf-8")
    # Match backtick-wrapped identifiers that look like
    # Python names. We use a permissive pattern that
    # catches ``Name``, ``module.name``, ``Class.method``,
    # etc. The audit script does *not* try to fully parse
    # Markdown; it just collects every backtick-wrapped
    # identifier.
    candidates: set[str] = set()
    for match in re.finditer(r"`([A-Za-z_][A-Za-z0-9_.]*)`", text):
        candidates.add(match.group(1))
    # Strip the ``agent_memory_contracts.`` prefix.
    stripped: set[str] = set()
    for c in candidates:
        if c.startswith("agent_memory_contracts."):
            stripped.add(c.split(".", 1)[1])
        else:
            stripped.add(c)
    return stripped


def main(argv: list[str] | None = None) -> int:
    pub = set(_public_names())
    doc = _documented_names()
    missing_in_doc = pub - doc
    extra_in_doc = doc - pub
    if not missing_in_doc and not extra_in_doc:
        print(f"OK: {len(pub)} public names, all documented in {STABILITY_DOC.name}.")
        return 0
    if missing_in_doc:
        print(f"FAIL: {len(missing_in_doc)} public names are NOT documented in {STABILITY_DOC.name}:")
        for name in sorted(missing_in_doc):
            print(f"  - {name}")
    if extra_in_doc:
        print(f"WARN: {len(extra_in_doc)} names are in {STABILITY_DOC.name} but not in __all__:")
        for name in sorted(extra_in_doc):
            print(f"  - {name}")
    # Missing-in-doc is a hard failure; extra-in-doc is
    # a warning (the doc may document internal names
    # that aren't part of the public surface).
    return 1 if missing_in_doc else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
