"""Reference runtime for the six memory planes (sqlite3, stdlib-only).

The contracts library defines *what* a governed memory record is;
this subpackage is the reference implementation of *how* a runtime
holds and moves them:

- :mod:`.store` -- sqlite3-backed six-plane persistence: immutable
  payloads, insert-only edge tables, append-only triggers,
  supersession materialized at read.
- :mod:`.gate` -- the single validated write path: idempotent
  ingestion plus the one transactional ``promote()`` that can
  create trusted memory.
- :mod:`.anchors` -- the hash-chained audit anchors written on
  every committed batch, with chain verification and coverage
  checks.
- :mod:`.grounding` -- deterministic, receipted ContextPack builds
  and the cite-or-refuse answer contract.

The product ADRs (Brainiac runtime) are the blueprint; deviations
needed to stay library-pure are documented inline and in
``docs/ROADMAP-to-product.md``.
"""

from .gate import (
    IngestReceipt,
    MemoryGate,
    PromoteReceipt,
)
from .grounding import (
    AnswerResult,
    Citation,
    PackBuildResult,
    RuntimeBuildReceipt,
    answer,
    build_context_pack,
    related_candidates,
    verify_grounding,
)
from .anchors import (
    AnchorDivergence,
    AnchorReceipt,
    ChainVerification,
    CoverageReport,
    full_scope,
    verify_chain,
    verify_coverage,
)
from .store import (
    ConflictError,
    IdMismatchError,
    LedgerSearchHit,
    MemoryStore,
    NotFoundError,
    StorageBackend,
    StoreError,
    ValidationRejectedError,
    canonical_json,
)

__all__ = [
    "IngestReceipt",
    "MemoryGate",
    "PromoteReceipt",
    "AnswerResult",
    "Citation",
    "PackBuildResult",
    "RuntimeBuildReceipt",
    "answer",
    "build_context_pack",
    "related_candidates",
    "verify_grounding",
    "AnchorDivergence",
    "AnchorReceipt",
    "ChainVerification",
    "CoverageReport",
    "full_scope",
    "verify_chain",
    "verify_coverage",
    "ConflictError",
    "IdMismatchError",
    "LedgerSearchHit",
    "MemoryStore",
    "NotFoundError",
    "StorageBackend",
    "StoreError",
    "ValidationRejectedError",
    "canonical_json",
]
