"""agent-memory-contracts: schema packs for AI agent memory integrity.

A Python library of JSON Schemas and dataclass contracts for the
six memory planes of an AI agent:

    1. Evidence      -- immutable source/episode/spans (the evidence plane)
    2. Candidate     -- untrusted extracted interpretations (the candidate plane)
    3. Ledger        -- reducer-approved trusted memory (the trusted memory plane)
    4. Taste         -- reducer-approved taste/preference cards
    5. State         -- reducer-approved project and core state snapshots
    6. ContextPack   -- task-ready bundles of memory with build + validation receipts

The library is standard-library only (no runtime dependencies) and is
designed to be embedded in any agent memory architecture that wants
explicit separation between untrusted extraction, reducer authority, and
rebuildable views.

The contracts were extracted from a 30+ sprint falsification-first build
of an agent memory kernel; the schemas and id formats are stable but
treated as 1.0.0 in this initial release.
"""

from .evidence_contracts import (
    EvidenceSpan,
    EpisodeRecord,
    SourceRecord,
)
from .evidence_ids import (
    make_episode_id,
    make_source_id,
    make_span_id,
    sha256_hex,
)
from .candidate_contracts import (
    CandidateClaim,
    CandidateDecision,
    CandidatePreference,
    CandidateTask,
    CandidateTasteSignal,
    validate_candidate_bundle,
)
from .candidate_ids import make_candidate_id
from .ledger_contracts import (
    DecisionLedgerEntry,
    FactLedgerEntry,
    MemoryReducerDecision,
    PreferenceLedgerEntry,
    ledger_entry_from_dict,
    reducer_decision_from_dict,
    validate_ledger_bundle,
)
from .ledger_ids import make_ledger_entry_id, make_reducer_decision_id
from .taste_contracts import (
    TasteCard,
    TasteReducerDecision,
    current_taste_cards,
    is_taste_card_active_at,
    taste_card_from_dict,
    taste_cards_as_of,
    taste_reducer_decision_from_dict,
    taste_supersession_chain,
    validate_taste_bundle,
)
from .taste_ids import make_taste_card_id, make_taste_reducer_decision_id
from .state_contracts import (
    CoreStateSnapshot,
    ProjectStateSnapshot,
    StateReducerDecision,
    core_state_from_dict,
    project_state_from_dict,
    state_reducer_decision_from_dict,
    validate_state_bundle,
)
from .state_ids import (
    make_core_state_id,
    make_project_state_id,
    make_state_reducer_decision_id,
)
from .state_queries import (
    core_state_supersession_chain,
    core_states_as_of,
    current_core_state,
    current_core_states,
    current_project_states,
    is_core_state_active_at,
    is_project_state_active_at,
    project_state_for_project,
    project_state_supersession_chain,
    project_states_as_of,
)
from .contextpack_contracts import (
    ContextPack,
    ContextPackBuildReceipt,
    ContextPackValidationReport,
    context_pack_build_receipt_from_dict,
    context_pack_from_dict,
    context_pack_validation_report_from_dict,
)
from .contextpack_ids import (
    make_context_pack_build_receipt_id,
    make_context_pack_id,
    make_context_pack_validation_report_id,
)
from .contextpack_validation import validate_contextpack_bundle
from .bundles import bundle_fingerprint

__version__ = "0.4.0"

__all__ = [
    # Evidence plane
    "SourceRecord",
    "EpisodeRecord",
    "EvidenceSpan",
    "make_source_id",
    "make_episode_id",
    "make_span_id",
    "sha256_hex",
    # Candidate plane
    "CandidateClaim",
    "CandidateDecision",
    "CandidatePreference",
    "CandidateTask",
    "CandidateTasteSignal",
    "make_candidate_id",
    "validate_candidate_bundle",
    # Ledger plane
    "FactLedgerEntry",
    "PreferenceLedgerEntry",
    "DecisionLedgerEntry",
    "MemoryReducerDecision",
    "make_ledger_entry_id",
    "make_reducer_decision_id",
    "ledger_entry_from_dict",
    "reducer_decision_from_dict",
    "validate_ledger_bundle",
    # Taste plane
    "TasteCard",
    "TasteReducerDecision",
    "make_taste_card_id",
    "make_taste_reducer_decision_id",
    "taste_card_from_dict",
    "taste_reducer_decision_from_dict",
    "current_taste_cards",
    "taste_cards_as_of",
    "is_taste_card_active_at",
    "taste_supersession_chain",
    "validate_taste_bundle",
    # State plane
    "ProjectStateSnapshot",
    "CoreStateSnapshot",
    "StateReducerDecision",
    "make_project_state_id",
    "make_core_state_id",
    "make_state_reducer_decision_id",
    "project_state_from_dict",
    "core_state_from_dict",
    "state_reducer_decision_from_dict",
    "current_project_states",
    "current_core_state",
    "current_core_states",
    "is_project_state_active_at",
    "is_core_state_active_at",
    "project_state_for_project",
    "project_states_as_of",
    "core_states_as_of",
    "project_state_supersession_chain",
    "core_state_supersession_chain",
    "validate_state_bundle",
    # ContextPack plane
    "ContextPack",
    "ContextPackBuildReceipt",
    "ContextPackValidationReport",
    "make_context_pack_id",
    "make_context_pack_build_receipt_id",
    "make_context_pack_validation_report_id",
    "context_pack_from_dict",
    "context_pack_build_receipt_from_dict",
    "context_pack_validation_report_from_dict",
    "validate_contextpack_bundle",
    "bundle_fingerprint",
]
