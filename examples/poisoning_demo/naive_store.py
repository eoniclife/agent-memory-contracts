"""Store A: the "silent" memory store.

This is the memory architecture most agent stacks ship with today,
reduced to its essentials: extract a dict, append it to a list,
retrieve by keyword. Roughly 80 lines, and every one of them is
plausible -- which is exactly the problem. There is no source
identity check, no authorization check, no audit trail. Whatever
the extractor produced most recently *is* the store's truth.

The demo attacks this store and the governed store
(``governed_store.py``) with identical inputs; ``run.py`` shows the
divergence.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

_WORD_RE = re.compile(r"[a-z0-9.%]+")


def _tokens(text: str) -> set[str]:
    """Lowercase keyword tokens. Deliberately simple."""
    return set(_WORD_RE.findall(text.lower()))


class NaiveMemoryStore:
    """Extract dict -> append list -> keyword retrieve. That's it."""

    def __init__(self) -> None:
        self.facts: list[dict[str, Any]] = []

    def ingest(self, doc: dict[str, Any], extractions: list[dict[str, Any]]) -> int:
        """Append every extracted fact for ``doc`` to the memory list.

        No checks of any kind: the extractor said it, so the store
        remembers it. Returns the number of facts appended.
        """
        appended = 0
        for row in extractions:
            if row["doc_key"] != doc["doc_key"]:
                continue
            self.facts.append({
                "subject": row["subject"],
                "predicate": row["predicate"],
                "object": row["object"],
                "claim_text": row["claim_text"],
                "from_doc": doc["title"],
                "received_at": doc["received_at"],
            })
            appended += 1
        return appended

    def retrieve(self, query: str) -> dict[str, Any] | None:
        """Keyword retrieval with a recency tie-break.

        Scores each fact by token overlap with the query; on a tie
        the most recently appended fact wins. This is the standard
        "latest relevant memory" heuristic -- and it is the
        mechanism the forged memo rides in on: the forgery is newer
        and keyword-denser than the policy it contradicts.
        """
        q = _tokens(query)
        best: dict[str, Any] | None = None
        best_key = (-1, -1)
        for index, fact in enumerate(self.facts):
            overlap = len(q & _tokens(
                f"{fact['subject']} {fact['claim_text']}"))
            if overlap > 0 and (overlap, index) > best_key:
                best_key = (overlap, index)
                best = fact
        return best

    def fingerprint(self) -> str:
        """SHA-256 of the store's current contents (for the report)."""
        canonical = json.dumps(self.facts, sort_keys=True,
                               separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
