# Sprint 22 / v0.8.0 spec: citation graph + provenance traversal

**Status:** awaiting your review. I'll start implementation after
you sign off. Open questions are listed at the bottom; please
answer them inline (or say "use your judgment").

**Branching decision:** staying on `main`, same as v0.7.0.

---

## Problem

The library has 6 record types that are *linked* to each other by
content-derived ids (every record's id is SHA-256 of its canonical
JSON), and the relationships are already encoded in the record
fields:

- An `EvidenceSpanRecord` has a `source_id` field pointing to its
  `SourceRecord`.
- A `CandidateClaim` / `FactLedgerEntry` / `DecisionLedgerEntry` /
  `PreferenceLedgerEntry` / `TasteCard` / `ContextPack` has an
  `evidence_span_ids` field (or `evidence_id` singular in older
  record variants) pointing to one or more `EvidenceSpanRecord`s.
- An `EpisodeRecord` has both a `source_id` (pointing to its
  `SourceRecord`) and an `evidence_span_ids` field (pointing to
  spans inside the episode).

The chains are encoded, but the library has no first-class
abstraction for "given a record, find everything it cites" or
"given a record, find everything that cites it" or "given a
bundle, find the claims that are not backed by any source."

What that means in practice:

1. **Provenance is invisible.** A `FactLedgerEntry` in a bundle
   does not expose, as a library primitive, "show me the source
   documents I derived from." You have to grep for `evidence_span_ids`
   in the bundle, then for `source_id` in each span, by hand.
2. **Audit gaps are invisible.** "Which claims in this bundle have
   no source backing?" requires writing the join yourself. A
   product building a "company brain" cannot answer "are we
   asserting things we can't back up?" without standing up a graph
   database.
3. **Citation impact is invisible.** "If I delete this source
   record, which claims stop being backed?" is the same join, in
   reverse. A first-class graph primitive lets a product answer it
   with one call.
4. **Unused inputs are invisible.** "Which sources are not cited
   by any claim?" is the dangling-source query. Useful for
   archival and for "what did we ingest but never use?" cost
   reports.

This sprint ships a first-class, stdlib-only, frozen-dataclass
citation graph primitive on top of the existing record types. No
schema changes. No new ids. No new dependency on a graph database.
The graph is **derived** from a bundle, not stored alongside it.

---

## What's in this sprint

### New module: `src/agent_memory_contracts/citations.py`

A single new module that builds a citation graph from a bundle and
provides traversal and analysis primitives. Library convention:
frozen dataclasses, content-derived ids, `from_dict`/`to_dict`
where the shape is serialized, `mypy --strict` clean, stdlib only.

#### `CitationNode` (frozen dataclass)

A typed wrapper around a record that participates in the citation
graph. The graph has exactly three node kinds:

| Node kind   | What it wraps                                          |
| ----------- | ------------------------------------------------------ |
| `"source"`  | `SourceRecord` or `EpisodeRecord` (both are "roots")   |
| `"evidence"`| `EvidenceSpanRecord`                                   |
| `"claim"`   | Anything that cites evidence (candidates, ledger entries, taste cards, context packs) |

```python
@dataclass(frozen=True)
class CitationNode:
    record_id: str
    node_kind: Literal["source", "evidence", "claim"]
    record_type: str  # e.g., "source_record", "fact_ledger_entry", "context_pack"
    record: Any       # the underlying typed record (or dict in from-dict)
```

The `record` field is typed `Any` on purpose: depending on the
node kind, it's a `SourceRecord`, an `EpisodeRecord`, an
`EvidenceSpanRecord`, a `CandidateClaim`, a `FactLedgerEntry`, a
`DecisionLedgerEntry`, a `PreferenceLedgerEntry`, a `TasteCard`,
or a `ContextPack`. The union is wide; runtime type discrimination
on `node_kind` is the intended access pattern.

#### `CitationEdge` (frozen dataclass)

A directed edge in the graph. Two edge relations:

- `("claim", "cites", "evidence")` — claim → evidence span
- `("evidence", "derives_from", "source")` — evidence → source

```python
@dataclass(frozen=True)
class CitationEdge:
    from_id: str
    to_id: str
    relation: Literal["cites", "derives_from"]
```

Edges are deduplicated: if a claim cites the same evidence twice
(impossible by schema, but defensively), the graph keeps one edge.
The graph is a DAG because the node kinds are layered: claims only
point to evidence, evidence only points to sources, sources are
terminal. No cycles are possible.

#### `CitationPath` (frozen dataclass)

A single walk through the graph from a start node to a terminal
node. Used as the unit of return for traversal.

```python
@dataclass(frozen=True)
class CitationPath:
    start_id: str
    end_id: str
    nodes: tuple[CitationNode, ...]   # ordered, start -> ... -> end
    edges: tuple[CitationEdge, ...]   # ordered, length = len(nodes) - 1

    @property
    def length(self) -> int: ...      # number of edges

    def is_supported(self) -> bool: ...  # True iff end_id points to a "source" node
```

A claim with no citations has no paths (it is itself an
unsupported claim; the relevant query is `find_unsupported_claims`,
not traversal). A claim with one chain of citations to one source
yields one `CitationPath`. A claim citing two pieces of evidence
that each point to one source yields two paths.

#### `CitationGraph` (frozen dataclass)

The graph itself, built once from a bundle. Frozen, so the
`from_bundle` constructor does the work and the consumer traverses.

```python
@dataclass(frozen=True)
class CitationGraph:
    nodes: Mapping[str, CitationNode]
    outgoing: Mapping[str, tuple[CitationEdge, ...]]  # from_id -> edges
    incoming: Mapping[str, tuple[CitationEdge, ...]]  # to_id -> edges
    dangling_refs: tuple[DanglingRef, ...]            # ids referenced but not present

    @classmethod
    def from_bundle(cls, bundle: Bundle) -> CitationGraph: ...

    def has_node(self, node_id: str) -> bool: ...

    def get_node(self, node_id: str) -> CitationNode | None: ...

    def traverse(
        self,
        start: str | CitationNode,
        *,
        direction: Literal["forward", "backward", "both"] = "forward",
        max_depth: int | None = None,
    ) -> list[CitationPath]: ...

    def descendants(
        self,
        node: str | CitationNode,
        *,
        max_depth: int | None = None,
    ) -> Iterator[CitationNode]: ...

    def predecessors(
        self,
        node: str | CitationNode,
        *,
        max_depth: int | None = None,
    ) -> Iterator[CitationNode]: ...

    def shortest_path(self, src: str, dst: str) -> CitationPath | None: ...

    def size(self) -> int: ...

    def node_count_by_kind(self) -> dict[str, int]: ...
```

Traversal is BFS, deterministic, and bounded by `max_depth`
(default `None` = unbounded; the DAG means this terminates). The
`dangling_refs` field captures references that point to records
not in the bundle (e.g., a claim citing `span_abc` when the bundle
has no `span_abc`). Traversal skips dangling refs but does not
raise — the graph is always buildable from a partially-valid
bundle. The product can choose to surface `dangling_refs` as its
own audit signal.

`DanglingRef` is a small frozen dataclass:

```python
@dataclass(frozen=True)
class DanglingRef:
    from_id: str
    missing_id: str
    relation: Literal["cites", "derives_from"]
```

#### `find_unsupported_claims(bundle, *, claim_predicate=default_claim_predicate) -> list[Any]`

The headline "audit gap" query. Returns every claim in the bundle
for which no path to a `SourceRecord` or `EpisodeRecord` exists in
the graph. Default predicate matches any record with
`evidence_span_ids` (or `evidence_id`) referencing an
`EvidenceSpanRecord` that, in turn, points to a source. The
caller can override the predicate to scope the query (e.g.,
"find unsupported `FactLedgerEntry`s only, not candidate claims").

```python
def find_unsupported_claims(
    bundle: Bundle,
    *,
    claim_predicate: Callable[[Any], bool] = default_claim_predicate,
) -> list[Any]:
    ...
```

The result is a list of the underlying records (not `CitationNode`s),
sorted by `id` for determinism. An empty list means "every claim
in the bundle is backed by at least one source."

#### `find_unused_sources(bundle, *, source_predicate=default_source_predicate) -> list[Any]`

The inverse query. Returns every `SourceRecord` (or
`EpisodeRecord`) that no claim cites — i.e., sources that are
ingested but never used by any promoted memory. Useful for archival
and cost reports.

```python
def find_unused_sources(
    bundle: Bundle,
    *,
    source_predicate: Callable[[Any], bool] = default_source_predicate,
) -> list[Any]:
    ...
```

The result is a list of the underlying records, sorted by `id`.
An empty list means "every source in the bundle is cited by at
least one claim."

#### Default predicates

```python
def default_claim_predicate(record: Any) -> bool:
    """A record counts as a 'claim' for citation purposes iff it
    carries at least one of the standard evidence-reference fields."""
    if not hasattr(record, "__dict__"):
        return False
    return hasattr(record, "evidence_span_ids") or hasattr(record, "evidence_id")

def default_source_predicate(record: Any) -> bool:
    """A record counts as a 'source' for citation purposes iff it
    is one of the two source-plane record types."""
    from .evidence_contracts import SourceRecord, EpisodeRecord
    return isinstance(record, (SourceRecord, EpisodeRecord))
```

The source predicate is intentionally narrow. If a future
record type should be a citation root, the caller passes a custom
predicate — no library-code change required.

---

## What's NOT in this sprint

Stated explicitly so we don't drift the API surface:

- **No graph mutation API.** The graph is derived from a bundle;
  if the bundle changes, rebuild the graph. There is no `add_node`
  or `remove_edge`. The frozen-dataclass convention applies.
- **No graph serialization format.** The graph is a transient
  analysis artifact, not a stored plane. Persist a bundle; build a
  graph on demand. (`to_dict`/`from_dict` on the graph is not
  in scope; the underlying bundle is the persisted form.)
- **No cycle handling.** The DAG property is structural (sources
  point to nothing, evidence points only to sources, claims point
  only to evidence). If a future record type breaks this, the
  `from_bundle` constructor raises — not a silent cycle.
- **No "trust scoring" / confidence propagation.** A path from
  a claim to a source is binary (exists or not). Confidence is
  already on the records; the graph does not aggregate it.
- **No "graph analytics" beyond what the four primitives give.**
  No PageRank, no centrality, no clustering. The library is
  primitives, not a graph database. (Those would be a future
  sprint or an external library.)
- **No CLI subcommand in v0.8.0.** The four primitives
  (`CitationGraph.from_bundle`, `traverse`, `find_unsupported_claims`,
  `find_unused_sources`) are the deliverable. A `citations`
  subcommand is plausible for v0.8.1 if the product needs it; the
  spec explicitly defers it.
- **No schema changes.** The graph is built from existing record
  fields (`source_id`, `evidence_span_ids`, `evidence_id`).
  Existing bundles and existing CI do not change.

---

## Public API placement

The four primitive classes and the two free functions are exported
from `agent_memory_contracts.__init__` as top-level names, same as
the v0.7.0 additions:

```python
# src/agent_memory_contracts/__init__.py — added in v0.8.0
from .citations import (
    CitationGraph,
    CitationNode,
    CitationEdge,
    CitationPath,
    DanglingRef,
    find_unsupported_claims,
    find_unused_sources,
    default_claim_predicate,
    default_source_predicate,
)
```

This is a public API commitment for the v0.8.0 line. Renaming
`CitationGraph` (or any other name) after v0.8.0 is a breaking
change. The convention in this library has been: choose the name
in the spec, ship it, and treat the name as stable from that
release onwards.

---

## Semantics

### Building the graph

```python
graph = CitationGraph.from_bundle(bundle)
```

`from_bundle` walks the bundle's typed record collections:

1. Every `SourceRecord` and `EpisodeRecord` becomes a `"source"`
   node.
2. Every `EvidenceSpanRecord` becomes an `"evidence"` node; for
   each one, an edge is added to its `source_id` target (skipping
   dangling refs, recording them in `dangling_refs`).
3. Every record that has `evidence_span_ids` (or `evidence_id`)
   becomes a `"claim"` node; for each reference, an edge is
   added to the referenced evidence span (skipping dangling
   refs).
4. The result is frozen.

`from_bundle` does not raise on dangling refs. It records them
and returns a valid graph. The product can choose to treat
`dangling_refs` as its own audit signal (e.g., "this bundle has
3 citations to evidence spans not present in the bundle" — a
common problem during partial exports or during a reducer's
in-progress write).

### Traversal

`graph.traverse(start)` is BFS, defaulting to forward direction
("what does this claim cite?"). The result is a list of
`CitationPath` objects, one per terminal in the reachable
subgraph. Because the graph is a DAG and we don't filter on
terminal type, a forward traversal from a claim can return
multiple paths when a claim cites multiple evidence spans that
each derive from a different source.

`graph.traverse(start, direction="backward")` reverses the
graph: from a source, "what claims cite something I produced?"
This is the citation-impact query: "if I delete this source,
which claims are no longer supported?"

`graph.traverse(start, direction="both")` returns the union of
forward and backward paths. Useful for "give me everything that
is connected to this record" in the product UX.

`max_depth` is the number of edges to follow. The default is
`None` (unbounded; terminates because of the DAG). Setting
`max_depth=1` on a claim returns its direct evidence. Setting
`max_depth=2` on a claim returns evidence + the sources behind
that evidence.

`shortest_path(src, dst)` is BFS for a single path. Returns
`None` if `dst` is not reachable from `src`. Stable across
calls (deterministic edge ordering on tie).

### `find_unsupported_claims`

```python
unsupported = find_unsupported_claims(bundle)
# -> [FactLedgerEntry(id="fact_xyz", ...), ...]
```

Implementation: build the graph, then for every `"claim"` node,
check whether at least one forward path to a `"source"` node
exists. Claims with no paths are returned. Claims with at least
one path that terminates at a source are NOT returned. Claims
whose paths terminate only at dangling refs (because the bundle
is missing the source) are returned — the claim is unsupported
relative to the bundle, even if it would be supported in a
larger corpus.

### `find_unused_sources`

```python
unused = find_unused_sources(bundle)
# -> [SourceRecord(id="src_abc", ...), ...]
```

Implementation: build the graph, then for every `"source"` node,
check whether at least one backward path to a `"claim"` node
exists. Sources with no path to a claim are returned. Sources
that are reachable from claims are NOT returned. Episode records
are included by default (a `Predicate` override is the way to
restrict to `SourceRecord` only).

---

## Failure modes and edge cases

Tested explicitly in the test suite:

1. **Empty bundle.** `from_bundle(Bundle())` returns a graph with
   zero nodes, zero edges, and zero dangling refs. `traverse`
   raises `KeyError` on a non-existent start id (this is the
   standard "no such node" behavior; the alternative would be
   returning an empty list, which hides bugs).
2. **Dangling `source_id` on an evidence span.** The edge is
   skipped. A `DanglingRef` is recorded. `find_unsupported_claims`
   treats the claim as unsupported (its only chain leads to a
   dangling ref).
3. **Dangling `evidence_span_id` on a claim.** The edge is
   skipped. A `DanglingRef` is recorded. The claim is unsupported.
4. **`evidence_id` singular (older record variants).** The
   default predicate accepts it. The graph builder checks both
   `evidence_span_ids` (plural, list) and `evidence_id`
   (singular, str). Both are supported.
5. **Cycles.** The graph is structurally a DAG. If a record
   has a self-referential `source_id` (e.g., a source citing
   itself) or a cycle in the future, `from_bundle` raises
   `ValueError` with a message identifying the cycle. The test
   suite includes a synthetic cycle fixture for this case.
6. **`max_depth=0`.** Returns the start node as a length-0 path
   (no edges). Useful for "is this record in the graph at all?"
7. **`max_depth` larger than the graph.** Behaves like
   `max_depth=None`.
8. **String start id not in the graph.** `traverse` raises
   `KeyError`. `descendants` and `predecessors` raise
   `KeyError`. The caller is expected to use `has_node` or
   `get_node` first.
9. **CitationNode start.** `traverse` accepts either a string
   id or a `CitationNode`. Same BFS.
10. **Predicate that raises.** The default predicates don't
    raise on arbitrary inputs. A user-supplied predicate that
    raises propagates the exception — we don't catch it. The
    failure mode is "your predicate has a bug."
11. **Frozen graph on a mutable bundle.** The graph holds
    references to the records (not copies). Mutating a record
    after building the graph mutates the node's `record` field.
    The convention in this library is "don't mutate," so this
    is consistent.

---

## Test plan

### Synthetic graph fixtures

The test file `tests/test_citations.py` ships four hand-built
fixture bundles, each a 5-10 node graph:

1. **Linear chain.** One source, one evidence, one claim.
   `traverse(claim)` returns one path of length 2. `traverse(source,
   direction="backward")` returns one path of length 2. The
   chain is fully supported.
2. **Diamond.** One source, two evidence spans deriving from it,
   one claim citing both spans. `traverse(claim)` returns two
   paths of length 2 (one per span). `find_unsupported_claims`
   returns an empty list.
3. **Disconnected.** One source, one evidence, one unsupported
   claim (citing a span with no source), one unused source
   (cited by nothing). `find_unsupported_claims` returns the
   one claim. `find_unused_sources` returns the one source.
4. **Mixed-plane.** Two sources, two evidence spans, two claims
   (one a `FactLedgerEntry`, one a `TasteCard`). Includes
   dangling refs to test the `dangling_refs` field. Includes
   one claim with the singular `evidence_id` field for
   backwards-compat coverage.

### Test cases

For each fixture, the test suite covers:

- `CitationGraph.from_bundle` produces the expected node count,
  edge count, and dangling ref count.
- `has_node` and `get_node` agree on existence.
- `traverse(claim, direction="forward")` returns the expected
  paths, in a stable order.
- `traverse(source, direction="backward")` returns the expected
  paths.
- `traverse(record, direction="both")` is the union of forward
  and backward paths.
- `traverse(claim, max_depth=1)` stops at evidence.
- `traverse(claim, max_depth=2)` reaches the source.
- `shortest_path` returns the expected shortest path, or `None`
  for disconnected nodes.
- `find_unsupported_claims(bundle)` returns the expected list
  (or empty).
- `find_unused_sources(bundle)` returns the expected list
  (or empty).
- `size` and `node_count_by_kind` match.
- `__init__.py` exports all v0.8.0 names.
- `mypy --strict` clean on the new module.

Plus the failure-mode tests (empty bundle, dangling refs,
non-existent start id, max_depth=0, cycle raises).

Target: **30+ new tests** (matches the v0.7.0 test discipline
of "a primitive is shipped with comprehensive coverage").

### `examples/citations.py`

A worked example matching the v0.7.0 cadence. Reads a small
synthetic bundle (built in the example, not loaded from disk),
builds the graph, prints the supported/unsupported split, prints
the unused sources, and shows one traversal per direction. The
example runs in CI's "examples smoke test" step.

---

## Out of scope (the boundary)

To make the sprint scope explicit:

- v0.8.0 does **not** add a `citations` CLI subcommand. The
  primitives are library-only. (Easy to add in v0.8.1 if needed.)
- v0.8.0 does **not** add a graph serialization format. The
  graph is transient.
- v0.8.0 does **not** add a "trust score" or confidence
  propagation. The graph is structural.
- v0.8.0 does **not** add a "graph analytics" suite. The four
  primitives are the deliverable.
- v0.8.0 does **not** change any schema. All 23 schemas stay
  unchanged; all 21 modules stay backwards compatible.
- v0.8.0 does **not** add a citation visualization or rendering.
  Visualization is a product concern, not a library concern.
- v0.8.0 does **not** add cycle handling. The DAG property is
  structural; a cycle raises, doesn't loop.

---

## Bottom line

The citation graph is a thin derivation layer on top of the
existing record types. It does not need new ids, new schemas, or
a graph database. It needs ~200 LOC of graph construction, ~100
LOC of BFS traversal, ~50 LOC of the two free functions, and
~30 tests. The deliverable is small, the value is high (a
company-brain product gets four first-class audit primitives for
"are we asserting things we can back up?"), and the failure modes
are bounded by the DAG.

If you sign off as-is, I start work in ~30 minutes from your
"go." If you have overrides on the 9 small questions below, list
them inline. If you have a substantive redesign, let's talk.

---

## Open questions for you

1. **Module name:** `citations.py` (my default) vs `graph.py` vs
   `provenance.py`. Default: `citations.py` (matches the spec
   title and the public API names; `provenance.py` would suggest
   traversal-only semantics, which the graph goes beyond with
   `find_unsupported_claims`).
2. **Node-kind discriminator name:** `node_kind` (my default)
   vs `kind` vs `type`. Default: `node_kind` (avoids the schema
   plane's `record_type` collision and is explicit about what it
   discriminates).
3. **Default traversal direction in `traverse()`:** `"forward"`
   (my default) vs `"backward"` vs requiring the caller to pick
   (no default). Default: `"forward"` (the common case is "show
   me what this claim cites"). Caller can override per call.
4. **Should `traverse` return paths or just nodes?** Default:
   paths (the `CitationPath` carries both the nodes and the
   edges, with the `is_supported` helper). A node-only return
   loses the edge information, which the product needs for
   "render the citation chain in the UI."
5. **Predicate default for `find_unsupported_claims`:** the
   `default_claim_predicate` (any record with
   `evidence_span_ids` or `evidence_id`). Override here if you
   want a stricter default (e.g., only ledger entries, not
   candidates).
6. **Predicate default for `find_unused_sources`:** the
   `default_source_predicate` (SourceRecord OR EpisodeRecord).
   Override if you want to restrict to one of the two.
7. **`DanglingRef` is part of the graph (always present) or
   surfaced as a separate `find_dangling_refs(bundle)` free
   function?** Default: part of the graph (`graph.dangling_refs`
   is a tuple), AND a `find_dangling_refs(bundle)` free function
   for ergonomic call sites. The free function returns a list
   of `DanglingRef`; the graph attribute is the same shape.
8. **Should `__init__.py` export the `DanglingRef` dataclass as
   a top-level name?** Default: yes (it's a stable public type).
9. **Should the v0.8.0 spec include a CLI subcommand for
   citations, or defer to v0.8.1?** Default: defer. The four
   primitives are the deliverable. The product can `from
   agent_memory_contracts import find_unsupported_claims` for
   now; a CLI subcommand is one line of code if the UX needs it.

If you have no overrides, I'll go with all defaults. If you have
1-2 minor overrides, just list them inline. If you have a
substantive redesign, let's talk before I start.

---

## What I'd want feedback on

The 9 open questions above are the small ones. The bigger
questions, where I'd most value your pushback:

- **Is the "claim = any record with `evidence_span_ids`" definition
  right?** A `ContextPack` is technically a *bundle* of claims,
  not a single claim. Including it in the default predicate means
  `find_unsupported_claims` would flag a context pack with no
  evidence, but a context pack without evidence is degenerate
  (the schema requires at least one). Alternatively, a stricter
  default would exclude context packs. My read is "include it" —
  the predicate is the right escape hatch if a product wants to
  exclude them. If you disagree, override question 5.
- **Should `find_unused_sources` include `EpisodeRecord`s by
  default?** Episodes are containers; a source that's only
  referenced by an unused episode is borderline. My read is
  "include them" — the product can filter with the predicate —
  because the cost of an unused episode is a real cost. If you
  want the default narrower (SourceRecord only), override
  question 6.
- **Is the no-cycle-handling call right?** A real bundle from a
  well-behaved kernel can never have a cycle. A partial bundle
  (e.g., one created by `bundle_diff` in v0.4.0) could, in
  principle, if the future adds a record type with a circular
  reference. My read is "raise on cycle; the user fixes the
  bundle." If you want the graph to silently elide cycles,
  override this in the bigger-questions section, and I'll add
  the elision logic.

---

## Implementation order

After sign-off, the work happens in 6 commits on `main`:

1. `docs(specs): sprint 22 / v0.8.0 — citation graph + provenance
   traversal` (this doc, on `docs/specs/sprint_22_citation_graph.md`).
2. `feat: add CitationNode, CitationEdge, CitationPath, DanglingRef,
   and CitationGraph in agent_memory_contracts.citations` (the
   module + dataclasses + `from_bundle`).
3. `feat: add traverse, descendants, predecessors, shortest_path
   on CitationGraph` (the BFS).
4. `feat: add find_unsupported_claims, find_unused_sources,
   default predicates` (the two free functions).
5. `test+example: 30+ tests for citation graph; examples/citations.py`.
6. `release: agent-memory-contracts v0.8.0` (version bump in
   `pyproject.toml` and `__init__.py`, CHANGELOG entry, tag,
   push, GitHub Release).

The work is solo (one main primitive, one feature) — no team
plan needed. Estimated: 1-2 days, matching the v0.7.0 cadence.

After implementation:
- `pytest -q` reports **355+ tests** (325 + ~30 new), 0 failures.
- `mypy --strict src/agent_memory_contracts` clean.
- All 5 examples (4 existing + `citations.py`) run as smoke tests
  in CI.

---

## Bottom line (one more time)

The spec is concrete, the public API is committed, the failure
modes are enumerated, the boundaries are stated, and the test
plan is a mirror of the v0.7.0 cadence. I have a clear sprint.

Sign off as-is, list inline overrides, or let's talk about the
three bigger questions. From your "go" to a tagged v0.8.0 is
1-2 days solo.

---

## Decisions applied to this sprint

Applied 2026-06-06 per the user's "go with best judgment"
mandate. Recorded here so the spec stays the source of truth for
"why was this built this way" review.

### 9 small decisions (all defaults)

1. **Module name:** `citations.py`.
2. **Node-kind discriminator:** `node_kind`.
3. **Default traversal direction:** `"forward"`.
4. **`traverse` return shape:** `list[CitationPath]`.
5. **Default claim predicate:** any record with
   `evidence_span_ids` or `evidence_id` (includes candidates,
   ledger entries, taste cards, and context packs).
6. **Default source predicate:** `SourceRecord OR EpisodeRecord`.
7. **Dangling-ref shape:** tuple on graph
   (`graph.dangling_refs`) + free function
   `find_dangling_refs(bundle)` for ergonomic call sites.
8. **Export `DanglingRef` from `__init__.py`:** yes.
9. **CLI subcommand for citations:** deferred to v0.8.1.

### 3 bigger-question decisions (all defaults)

- **"Claim = any record with `evidence_span_ids`"** — kept the
  inclusive default. `ContextPack`s are first-class claims; a
  product that wants to exclude them passes a custom
  `claim_predicate`. The schema requires at least one
  `evidence_span_id` on a `ContextPack`, so a context pack
  without evidence is degenerate (and the predicate would
  already catch it as unsupported).
- **`find_unused_sources` includes `EpisodeRecord`s by
  default** — kept the inclusive default. An unused episode is
  a real cost (storage, retrieval) and the product can filter
  to `SourceRecord`-only with a custom `source_predicate`.
- **No cycle handling** — kept the "raise on cycle" default.
  `from_bundle` raises `ValueError` with the cycle identified;
  the user fixes the bundle. Rationale: a silent elision would
  hide the bug that introduced the cycle; the library's role
  is to surface the problem, not paper over it.

### Minor implementation choices (not in the open questions)

- **Id format for `DanglingRef`:** content-derived SHA-256 is
  overkill for a transient analysis artifact, so `DanglingRef`
  has no `id` field. It's a value object, not a record type.
- **`from_bundle` accepts both `Bundle` and `dict`:** the
  existing `Bundle` type in `bundles.py` is the canonical input;
  raw `dict` is also accepted for callers that haven't gone
  through the typed constructors. Both paths produce the same
  graph.
- **Graph traversal returns `list[CitationPath]`, not a
  generator:** the result is typically small (a few paths per
  claim) and the determinism of `list` (stable iteration order)
  matters for the product UX. A generator helper is a future
  addition if a use case shows up.

