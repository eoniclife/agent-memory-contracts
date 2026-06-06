"""Citation graph + provenance traversal primitives.

A *citation graph* is a derived, in-memory view of the
``source -> evidence -> claim`` chain encoded in a bundle of
records. Every record's id is content-derived (SHA-256 of its
canonical JSON), and the cross-record relationships are already
encoded in record fields:

- An :class:`~agent_memory_contracts.evidence_contracts.EvidenceSpanRecord`
  has a ``source_id`` field pointing to its
  :class:`~agent_memory_contracts.evidence_contracts.SourceRecord`.
- A claim (a candidate, a ledger entry, a taste card, a context
  pack) has an ``evidence_span_ids`` field (or, in older record
  variants, a singular ``evidence_id`` field) pointing to one or
  more :class:`~agent_memory_contracts.evidence_contracts.EvidenceSpanRecord`s.
- An :class:`~agent_memory_contracts.evidence_contracts.EpisodeRecord`
  has both a ``source_id`` and an ``evidence_span_ids`` field; in
  the citation graph, an episode is a *source-kind* node (a
  container for one event in a source), and the evidence spans
  it carries are reachable through it as well as through the
  source itself.

The chains are encoded in the bundle, but the library has no
first-class abstraction for "given a record, find everything it
cites" or "given a record, find everything that cites it" or
"given a bundle, find the claims that are not backed by any
source." This module ships that abstraction.

The graph is:

- **Derived, not stored.** ``CitationGraph.from_bundle(bundle)``
  builds the graph from existing record fields; nothing is added
  to the bundle, nothing is persisted alongside it. If the
  bundle changes, rebuild the graph.
- **Frozen.** The graph is a frozen dataclass. There is no
  ``add_node`` or ``remove_edge``; the convention in this
  library is "don't mutate, rebuild."
- **A DAG.** Node kinds are layered: source-kind nodes are
  terminal, evidence-kind nodes point only to source-kind nodes,
  claim-kind nodes point only to evidence-kind nodes. No cycles
  are structurally possible. If a record type in the future
  breaks this, ``from_bundle`` raises
  :class:`ValueError` rather than silently producing a cyclic
  graph.

The deliverable in this module is four frozen dataclasses
(:class:`CitationNode`, :class:`CitationEdge`, :class:`CitationPath`,
:class:`DanglingRef`, :class:`CitationGraph`) and three free
functions (:func:`find_unsupported_claims`,
:func:`find_unused_sources`, :func:`find_dangling_refs`) plus two
default predicates (:func:`default_claim_predicate`,
:func:`default_source_predicate`).

Typical use cases:

- **Provenance render.** A product UI shows a ``FactLedgerEntry``;
  the user clicks "show me the source." The library call is
  ``CitationGraph.from_bundle(bundle).traverse(entry.id)``,
  returning the chain of evidence and source records.
- **Audit gap query.** A compliance dashboard asks "are we
  asserting things we can't back up?" The library call is
  ``find_unsupported_claims(bundle)``; an empty list means
  every claim in the bundle is backed.
- **Citation impact.** A product is about to delete a
  ``SourceRecord``; the user wants to know which claims stop
  being backed. The library call is
  ``graph.traverse(source.id, direction="backward")``.
- **Storage cost report.** A platform team asks "which sources
  are ingested but never cited?" The library call is
  ``find_unused_sources(bundle)``.

Like the rest of the bundle primitives, this module is
standard-library only. No new dependencies.

.. versionadded:: 0.8.0
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Literal, Mapping

from .evidence_contracts import EpisodeRecord, EvidenceSpan, SourceRecord
from .ledger_contracts import MemoryReducerDecision


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CitationNode:
    """A typed wrapper around a record that participates in the graph.

    The graph has three node kinds:

    - ``"source"``  : a :class:`SourceRecord` or :class:`EpisodeRecord`
      (both are "roots" of a citation chain).
    - ``"evidence"``: an :class:`EvidenceSpanRecord` (a slice of a
      source, cited by a claim).
    - ``"claim"``   : any record that cites evidence by id
      (candidates, ledger entries, taste cards, context packs).

    The ``record`` field is intentionally typed as ``Any``: the
    union of record types is wide, and runtime type discrimination
    on ``node_kind`` (and, if needed, ``record_type``) is the
    intended access pattern.
    """

    record_id: str
    node_kind: Literal["source", "evidence", "claim"]
    record_type: str
    record: Any

    def __repr__(self) -> str:
        return (
            f"CitationNode(record_id={self.record_id!r}, "
            f"node_kind={self.node_kind!r}, record_type={self.record_type!r})"
        )


@dataclass(frozen=True)
class CitationEdge:
    """A directed edge in the citation graph.

    Two relations are used:

    - ``"cites"``         : a claim node cites an evidence node.
    - ``"derives_from"``  : an evidence node derives from a source
                            node.

    Edges are deduplicated: if a claim cites the same evidence
    span twice (impossible by schema, but defensively handled),
    the graph keeps one edge.
    """

    from_id: str
    to_id: str
    relation: Literal["cites", "derives_from"]

    def __repr__(self) -> str:
        return f"CitationEdge({self.from_id!r} --{self.relation}-> {self.to_id!r})"


@dataclass(frozen=True)
class DanglingRef:
    """A reference from one record to another that is missing from the bundle.

    The graph records dangling refs (rather than raising) so that
    the caller can decide whether to surface them as their own
    audit signal. Common causes:

    - A partial bundle (e.g., produced by :func:`bundle_diff` in
      v0.4.0) where one side of a citation is on the other side
      of the diff.
    - A reducer in mid-write that has emitted the claim before
      the supporting evidence span was committed.
    - A bundle exported from a different corpus whose evidence
      spans are not co-exported.

    ``DanglingRef`` has no ``id`` field: it is a transient
    analysis artifact, not a record type.
    """

    from_id: str
    missing_id: str
    relation: Literal["cites", "derives_from"]

    def __repr__(self) -> str:
        return (
            f"DanglingRef({self.from_id!r} --{self.relation}-> "
            f"{self.missing_id!r} [missing])"
        )


@dataclass(frozen=True)
class CitationPath:
    """A single walk through the graph from a start node to a terminal node.

    A path is the unit of return for traversal. It carries the
    ordered nodes and edges, with the :meth:`is_supported` helper
    that returns ``True`` iff the terminal node is a source-kind
    node (i.e., the chain reaches a real-world artifact).
    """

    start_id: str
    end_id: str
    nodes: tuple[CitationNode, ...]
    edges: tuple[CitationEdge, ...]

    @property
    def length(self) -> int:
        """Number of edges in the path."""
        return len(self.edges)

    def is_supported(self) -> bool:
        """``True`` iff the terminal node is a source-kind node.

        A claim with no citations has no paths (and so is not
        "supported" by the :func:`find_unsupported_claims`
        measure). A claim citing one piece of evidence that
        derives from one source yields one supported path. A
        claim citing two pieces of evidence that each derive
        from a different source yields two supported paths.
        """
        if not self.nodes:
            return False
        return self.nodes[-1].node_kind == "source"

    def __repr__(self) -> str:
        ids = " -> ".join(n.record_id for n in self.nodes)
        return f"CitationPath({ids})"


@dataclass(frozen=True)
class CitationGraph:
    """A derived citation graph built from a bundle of records.

    The graph is built once via :meth:`from_bundle` and is
    frozen. Traversal and analysis primitives are methods on the
    graph; the headline audit queries are free functions
    (:func:`find_unsupported_claims`, :func:`find_unused_sources`)
    that internally build a graph.
    """

    nodes: Mapping[str, CitationNode] = field(default_factory=dict)
    outgoing: Mapping[str, tuple[CitationEdge, ...]] = field(default_factory=dict)
    incoming: Mapping[str, tuple[CitationEdge, ...]] = field(default_factory=dict)
    dangling_refs: tuple[DanglingRef, ...] = ()

    # ------------------------------------------------------------------ build

    @classmethod
    def from_bundle(cls, bundle: Iterable[Any]) -> "CitationGraph":
        """Build a :class:`CitationGraph` from a bundle of records.

        A *bundle* in this library is an iterable of records.
        Each record may be a ``dict``, a ``Mapping``, or a
        dataclass instance. The builder classifies each record
        by its shape:

        - has ``source_type`` (a :class:`SourceRecord`) or
          ``episode_type`` (an :class:`EpisodeRecord`) ->
          ``"source"`` node.
        - has ``span_hash_sha256`` (an
          :class:`EvidenceSpanRecord`) -> ``"evidence"`` node.
        - has ``evidence_span_ids`` (list) or ``evidence_id``
          (string) -> ``"claim"`` node.

        Records that match none of these shapes are silently
        skipped. They are not part of the citation graph (a
        :class:`MemoryReducerDecision`, for example, is a
        derivation audit record, not a claim).

        The builder does not raise on dangling references. It
        records each one in :attr:`dangling_refs` and returns a
        valid graph. Cycles in the input raise
        :class:`ValueError` (the graph's DAG property is
        structural; a cycle indicates a bug elsewhere).

        Args:
            bundle: an iterable of records (dataclasses, dicts,
                or Mappings).

        Returns:
            A frozen :class:`CitationGraph`.
        """
        nodes: dict[str, CitationNode] = {}
        # Per-id edge lists, deduped.
        outgoing: dict[str, list[CitationEdge]] = {}
        incoming: dict[str, list[CitationEdge]] = {}
        dangling: list[DanglingRef] = []

        def add_edge(edge: CitationEdge, allow_dangling: bool) -> None:
            # Self-loop detection: structural cycle.
            if edge.from_id == edge.to_id:
                raise ValueError(
                    f"cycle in citation graph: self-loop on {edge.from_id!r}"
                )
            if edge.to_id not in nodes:
                if allow_dangling:
                    dangling.append(
                        DanglingRef(
                            from_id=edge.from_id,
                            missing_id=edge.to_id,
                            relation=edge.relation,
                        )
                    )
                return
            # Dedup: skip if this exact edge already exists.
            existing = outgoing.setdefault(edge.from_id, [])
            if any(
                e.to_id == edge.to_id and e.relation == edge.relation
                for e in existing
            ):
                return
            existing.append(edge)
            incoming.setdefault(edge.to_id, []).append(edge)

        # Pass 1: classify and add nodes.
        for record in bundle:
            record_id = _record_id(record)
            if not record_id:
                continue
            kind, rec_type = _classify(record)
            if kind is None:
                continue
            nodes[record_id] = CitationNode(
                record_id=record_id,
                node_kind=kind,
                record_type=rec_type,
                record=record,
            )

        # Pass 2: add edges. Evidence -> source first, then claim -> evidence.
        # We do this in two passes so that the "to" of any edge is
        # always already classified (and either present as a node or
        # recorded as dangling).
        for record_id, node in nodes.items():
            if node.node_kind == "evidence":
                src_id = _source_id_of_evidence(node.record)
                if src_id is not None:
                    add_edge(
                        CitationEdge(record_id, src_id, "derives_from"),
                        allow_dangling=True,
                    )
        for record_id, node in nodes.items():
            if node.node_kind == "claim":
                for ev_id in _evidence_refs_of_claim(node.record):
                    add_edge(
                        CitationEdge(record_id, ev_id, "cites"),
                        allow_dangling=True,
                    )

        # Pass 3: detect cycles. BFS from each node, with a
        # predecessor set. If we revisit a node on the current
        # walk, that's a cycle.
        # The DAG property is structural: edges go
        # claim -> evidence -> source. A cycle would only
        # appear if a record type in the future broke this
        # layering. Defensive check.
        adjacency: Mapping[str, tuple[CitationEdge, ...]] = {
            k: tuple(v) for k, v in outgoing.items()
        }
        for start_id in nodes:
            if _has_cycle(start_id, adjacency):
                cycle = _find_cycle(start_id, adjacency)
                raise ValueError(
                    f"cycle in citation graph starting at {start_id!r}: {cycle}"
                )

        return cls(
            nodes=nodes,
            outgoing={k: tuple(v) for k, v in outgoing.items()},
            incoming={k: tuple(v) for k, v in incoming.items()},
            dangling_refs=tuple(dangling),
        )

    # ----------------------------------------------------------- introspection

    def has_node(self, node_id: str) -> bool:
        """Return whether the graph contains a node with this id."""
        return node_id in self.nodes

    def get_node(self, node_id: str) -> CitationNode | None:
        """Return the node with this id, or ``None`` if absent."""
        return self.nodes.get(node_id)

    def size(self) -> int:
        """Return the number of nodes in the graph."""
        return len(self.nodes)

    def node_count_by_kind(self) -> dict[str, int]:
        """Return a dict mapping each node kind to its count."""
        counts: dict[str, int] = {"source": 0, "evidence": 0, "claim": 0}
        for node in self.nodes.values():
            counts[node.node_kind] = counts.get(node.node_kind, 0) + 1
        return counts

    # -------------------------------------------------------------- traversal

    def traverse(
        self,
        start: str | CitationNode,
        *,
        direction: Literal["forward", "backward", "both"] = "forward",
        max_depth: int | None = None,
    ) -> list[CitationPath]:
        """BFS traversal from a start node.

        Forward direction walks the outgoing edges (claim -> evidence,
        evidence -> source). Backward walks incoming edges
        (source <- evidence <- claim). Both returns the union of
        forward and backward paths.

        The result is one :class:`CitationPath` per terminal
        reachable from ``start`` within ``max_depth`` edges.
        Because the graph is a DAG, the result is finite and
        deterministic. Edge order on tie is by ``to_id`` for
        stable iteration.

        Args:
            start: a node id (string) or a :class:`CitationNode`.
            direction: ``"forward"`` (default), ``"backward"``, or
                ``"both"``.
            max_depth: maximum number of edges to follow. ``None``
                (the default) means unbounded (terminates because
                of the DAG). ``0`` returns the start node as a
                length-0 path.

        Returns:
            A list of :class:`CitationPath` objects, one per
            terminal reachable from ``start``. Empty if ``start``
            has no outgoing (or incoming) edges.

        Raises:
            KeyError: if ``start`` is a string id that is not in
                the graph.
        """
        start_id = self._resolve_start_id(start)
        max_d = max_depth if max_depth is None else int(max_depth)
        if max_d is not None and max_d < 0:
            raise ValueError("max_depth must be >= 0")
        if max_d == 0:
            return [
                CitationPath(
                    start_id=start_id,
                    end_id=start_id,
                    nodes=(self.nodes[start_id],),
                    edges=(),
                )
            ]

        paths: list[CitationPath] = []
        # BFS that tracks the full path to each discovered node.
        # Each queue entry is (current_id, current_path).
        # We use deque for BFS order.
        initial_path: list[CitationNode] = [self.nodes[start_id]]
        initial_edges: list[CitationEdge] = []
        queue: deque[tuple[str, list[CitationNode], list[CitationEdge]]] = deque(
            [(start_id, initial_path, initial_edges)]
        )
        # Visited set: for "forward"/"backward" the DAG means we
        # would never revisit a node, but for "both" the reversed
        # edges can produce a cycle (e.g., a span reached by going
        # forward from a claim can be reached again by going
        # backward from the source it derives from). Track visited
        # nodes globally per traversal to keep BFS finite.
        visited: set[str] = {start_id}
        directions = (
            ("outgoing", "incoming")
            if direction == "both"
            else (("outgoing",) if direction == "forward" else ("incoming",))
        )

        # Map direction -> "outgoing" or "incoming" attr name.
        adjacency_attr = {"outgoing": "outgoing", "incoming": "incoming"}
        # The "edges" we append are oriented from current_id to
        # next_id; for backward traversal, the edge is reversed.
        while queue:
            current_id, path_nodes, path_edges = queue.popleft()
            next_edges: list[CitationEdge] = []
            for attr in directions:
                edges = getattr(self, adjacency_attr[attr])
                for e in edges.get(current_id, ()):
                    if attr == "outgoing":
                        next_edges.append(e)
                    else:
                        # Reverse the edge for backward traversal.
                        next_edges.append(
                            CitationEdge(
                                from_id=e.to_id,
                                to_id=e.from_id,
                                relation=e.relation,
                            )
                        )
            # Stable order on tie: by to_id, then relation.
            next_edges.sort(key=lambda e: (e.to_id, e.relation))
            for edge in next_edges:
                if max_d is not None and len(path_edges) + 1 > max_d:
                    continue
                next_id = edge.to_id
                if next_id not in self.nodes:
                    # Should not happen: dangling refs are not in
                    # the edge list (they are recorded separately).
                    continue
                next_node = self.nodes[next_id]
                new_nodes = tuple(path_nodes + [next_node])
                new_edges = tuple(path_edges + [edge])
                # When ``max_d`` is set and we've reached it,
                # emit a path to this node and stop expanding
                # from it. Otherwise, continue BFS only if the
                # next node has further edges to follow; emit a
                # path to the next node when it doesn't.
                if max_d is not None and len(new_edges) >= max_d:
                    paths.append(
                        CitationPath(
                            start_id=start_id,
                            end_id=next_id,
                            nodes=new_nodes,
                            edges=new_edges,
                        )
                    )
                    continue
                has_more = False
                for attr in directions:
                    if getattr(self, adjacency_attr[attr]).get(next_id):
                        has_more = True
                        break
                if has_more and next_id not in visited:
                    visited.add(next_id)
                    queue.append((next_id, list(new_nodes), list(new_edges)))
                elif not has_more or next_id in visited:
                    paths.append(
                        CitationPath(
                            start_id=start_id,
                            end_id=next_id,
                            nodes=new_nodes,
                            edges=new_edges,
                        )
                    )
                # else: already visited AND not terminal; skip
                # to avoid cycles in ``direction="both"``
                # traversals.

        return paths

    def descendants(
        self,
        node: str | CitationNode,
        *,
        max_depth: int | None = None,
    ) -> Iterator[CitationNode]:
        """Iterate over nodes reachable by following outgoing edges.

        Convenience wrapper around :meth:`traverse` that returns
        the *terminal* nodes of each path, deduplicated. Useful
        for "what does this claim ultimately cite?" without
        caring about the path structure.
        """
        for path in self.traverse(node, direction="forward", max_depth=max_depth):
            yield path.nodes[-1]

    def predecessors(
        self,
        node: str | CitationNode,
        *,
        max_depth: int | None = None,
    ) -> Iterator[CitationNode]:
        """Iterate over nodes reachable by following incoming edges.

        Convenience wrapper around :meth:`traverse` that returns
        the terminal nodes of each path. Useful for "which
        claims cite this source?" without caring about the path
        structure.
        """
        for path in self.traverse(node, direction="backward", max_depth=max_depth):
            yield path.nodes[-1]

    def shortest_path(self, src: str, dst: str) -> CitationPath | None:
        """Return the shortest path from ``src`` to ``dst``, or ``None``.

        BFS guarantees the first discovered path is the shortest.
        The graph is a DAG, so BFS is well-defined and
        terminates.

        Args:
            src: the id of the source node.
            dst: the id of the destination node.

        Returns:
            The shortest :class:`CitationPath` from ``src`` to
            ``dst``, or ``None`` if no path exists.
        """
        if src not in self.nodes:
            raise KeyError(f"src not in graph: {src!r}")
        if dst not in self.nodes:
            raise KeyError(f"dst not in graph: {dst!r}")
        if src == dst:
            return CitationPath(
                start_id=src,
                end_id=dst,
                nodes=(self.nodes[src],),
                edges=(),
            )
        # BFS from src following outgoing edges.
        visited: set[str] = {src}
        queue: deque[tuple[str, list[CitationNode], list[CitationEdge]]] = deque(
            [(src, [self.nodes[src]], [])]
        )
        while queue:
            current_id, path_nodes, path_edges = queue.popleft()
            for edge in sorted(
                self.outgoing.get(current_id, ()), key=lambda e: (e.to_id, e.relation)
            ):
                if edge.to_id in visited:
                    continue
                visited.add(edge.to_id)
                next_node = self.nodes[edge.to_id]
                new_nodes = path_nodes + [next_node]
                new_edges = path_edges + [edge]
                if edge.to_id == dst:
                    return CitationPath(
                        start_id=src,
                        end_id=dst,
                        nodes=tuple(new_nodes),
                        edges=tuple(new_edges),
                    )
                queue.append((edge.to_id, new_nodes, new_edges))
        return None

    # ----------------------------------------------------------------- helpers

    def _resolve_start_id(self, start: str | CitationNode) -> str:
        if isinstance(start, CitationNode):
            start_id = start.record_id
        else:
            start_id = str(start)
        if start_id not in self.nodes:
            raise KeyError(f"start not in graph: {start_id!r}")
        return start_id


# ---------------------------------------------------------------------------
# Default predicates
# ---------------------------------------------------------------------------


def default_claim_predicate(record: Any) -> bool:
    """The default predicate for "is this record a claim?".

    A record counts as a claim for citation purposes iff it
    carries at least one of the standard evidence-reference
    fields (``evidence_span_ids`` as a list, or ``evidence_id``
    as a string, for older record variants).
    """
    if record is None:
        return False
    if hasattr(record, "evidence_span_ids") or hasattr(record, "evidence_id"):
        return True
    if isinstance(record, Mapping):
        return "evidence_span_ids" in record or "evidence_id" in record
    return False


def default_source_predicate(record: Any) -> bool:
    """The default predicate for "is this record a citation source?".

    A record counts as a source for citation purposes iff it is
    a :class:`SourceRecord` or an :class:`EpisodeRecord`. For
    dict/Mapping records, the test is the presence of the
    discriminator fields ``source_type`` or ``episode_type``.
    """
    if isinstance(record, (SourceRecord, EpisodeRecord)):
        return True
    if isinstance(record, Mapping):
        return "source_type" in record or "episode_type" in record
    return False


# ---------------------------------------------------------------------------
# Audit queries
# ---------------------------------------------------------------------------


def find_unsupported_claims(
    bundle: Iterable[Any],
    *,
    claim_predicate: Callable[[Any], bool] = default_claim_predicate,
) -> list[Any]:
    """Return every claim in the bundle with no path to a source.

    For each record in the bundle that satisfies
    ``claim_predicate``, the function checks whether at least one
    forward path to a source-kind node exists in the graph.
    Claims with no such path are returned.

    "No such path" includes the case where the claim's evidence
    spans are present in the bundle but those spans' ``source_id``
    references are dangling (the source is missing). A claim is
    unsupported relative to the bundle, even if it would be
    supported in a larger corpus.

    Args:
        bundle: an iterable of records.
        claim_predicate: a callable that takes a record and
            returns ``True`` if it should be treated as a claim
            for this query. Defaults to
            :func:`default_claim_predicate`.

    Returns:
        A list of unsupported claim records, sorted by id for
        determinism. An empty list means every claim in the
        bundle is backed by at least one source.
    """
    records = list(bundle)
    graph = CitationGraph.from_bundle(records)
    unsupported: list[Any] = []
    for record in records:
        if not claim_predicate(record):
            continue
        record_id = _record_id(record)
        if record_id is None or record_id not in graph.nodes:
            continue
        # A claim is unsupported iff it has no path to a source.
        paths = graph.traverse(record_id, direction="forward")
        if not any(p.is_supported() for p in paths):
            unsupported.append(record)
    unsupported.sort(key=_record_id_or_empty)
    return unsupported


def find_unused_sources(
    bundle: Iterable[Any],
    *,
    source_predicate: Callable[[Any], bool] = default_source_predicate,
) -> list[Any]:
    """Return every source in the bundle not cited by any claim.

    For each record in the bundle that satisfies
    ``source_predicate``, the function checks whether at least
    one backward path to a claim-kind node exists in the graph.
    Sources with no such path are returned.

    By default, both :class:`SourceRecord` and
    :class:`EpisodeRecord` are included. To restrict to
    ``SourceRecord`` only, pass a custom ``source_predicate``
    that excludes episodes:

    .. code-block:: python

        from agent_memory_contracts import (
            find_unused_sources,
            default_source_predicate,
        )
        from agent_memory_contracts.evidence_contracts import SourceRecord

        unused = find_unused_sources(
            bundle,
            source_predicate=lambda r: isinstance(r, SourceRecord),
        )

    Args:
        bundle: an iterable of records.
        source_predicate: a callable that takes a record and
            returns ``True`` if it should be treated as a source
            for this query. Defaults to
            :func:`default_source_predicate`.

    Returns:
        A list of unused source records, sorted by id for
        determinism. An empty list means every source in the
        bundle is cited by at least one claim.
    """
    records = list(bundle)
    graph = CitationGraph.from_bundle(records)
    unused: list[Any] = []
    for record in records:
        if not source_predicate(record):
            continue
        record_id = _record_id(record)
        if record_id is None or record_id not in graph.nodes:
            continue
        # A source is unused iff it has no path from a claim.
        paths = graph.traverse(record_id, direction="backward")
        if not paths:
            unused.append(record)
    unused.sort(key=_record_id_or_empty)
    return unused


def find_dangling_refs(bundle: Iterable[Any]) -> list[DanglingRef]:
    """Return every dangling reference in the bundle.

    A dangling reference is a citation from one record to
    another that is not present in the bundle. The graph
    builder records these rather than raising, so this function
    surfaces them as a list of :class:`DanglingRef` records
    sorted by ``(from_id, missing_id, relation)`` for
    determinism.

    Args:
        bundle: an iterable of records.

    Returns:
        A list of :class:`DanglingRef`. Empty list means the
        bundle has no dangling references (every citation
        resolves to a record in the bundle).
    """
    graph = CitationGraph.from_bundle(bundle)
    return sorted(
        list(graph.dangling_refs),
        key=lambda d: (d.from_id, d.missing_id, d.relation),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _record_id(record: Any) -> str | None:
    """Extract the id from a record (dataclass, dict, or Mapping)."""
    if record is None:
        return None
    if hasattr(record, "id"):
        value = getattr(record, "id", None)
        if isinstance(value, str) and value:
            return value
    if isinstance(record, Mapping):
        value = record.get("id")
        if isinstance(value, str) and value:
            return value
    return None


def _record_id_or_empty(record: Any) -> str:
    """Sort key: id of a record, or empty string for non-identifiable."""
    rid = _record_id(record)
    return rid if rid is not None else ""


def _classify(record: Any) -> tuple[Literal["source", "evidence", "claim"] | None, str]:
    """Classify a record into a node kind and return its record type.

    The classification is shape-based: a record is a
    ``SourceRecord`` iff it has a ``source_type`` field, an
    ``EpisodeRecord`` iff it has an ``episode_type`` field, an
    ``EvidenceSpanRecord`` iff it has a ``span_hash_sha256``
    field, a claim iff it has ``evidence_span_ids`` (list) or
    ``evidence_id`` (string).

    Returns ``(None, "")`` if the record does not match any of
    these shapes (e.g., a :class:`MemoryReducerDecision` is not
    part of the citation graph).
    """
    if record is None:
        return None, ""
    if isinstance(record, SourceRecord):
        return "source", "source_record"
    if isinstance(record, EpisodeRecord):
        return "source", "episode_record"
    if isinstance(record, EvidenceSpan):
        return "evidence", "evidence_span"
    # MemoryReducerDecision is an audit record about a reduction,
    # not a claim. It carries ``evidence_span_ids`` for
    # traceability, but the *decision* itself is not part of the
    # citation graph. Skip it.
    if isinstance(record, MemoryReducerDecision):
        return None, ""
    if hasattr(record, "source_type"):
        return "source", "source_record"
    if hasattr(record, "episode_type"):
        return "source", "episode_record"
    if hasattr(record, "span_hash_sha256"):
        return "evidence", "evidence_span"
    if isinstance(record, Mapping):
        if "source_type" in record:
            return "source", "source_record"
        if "episode_type" in record:
            return "source", "episode_record"
        if "span_hash_sha256" in record:
            return "evidence", "evidence_span"
    if default_claim_predicate(record):
        return "claim", _claim_record_type(record)
    return None, ""


def _claim_record_type(record: Any) -> str:
    """Return a stable record-type string for a claim-kind record."""
    # Prefer the dataclass name; fall back to a discriminator on
    # the dict shape.
    cls = getattr(record, "__class__", None)
    if cls is not None and hasattr(cls, "__name__") and cls.__name__ != "dict":
        return _snake_case(cls.__name__)
    if isinstance(record, Mapping):
        if "ledger_type" in record:
            return str(record.get("ledger_type", "ledger_entry"))
        if "candidate_type" in record:
            return str(record.get("candidate_type", "candidate"))
        if "taste_card" in record or "domain" in record and "signal_kind" in record:
            return "taste_signal_candidate"
        if "context_pack_kind" in record or "primary_evidence_span_ids" in record:
            return "context_pack"
    return "claim"


def _snake_case(name: str) -> str:
    """Convert CamelCase to snake_case without importing ``re``."""
    out: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0 and not name[i - 1].isupper():
            out.append("_")
        out.append(ch.lower())
    return "".join(out)


def _source_id_of_evidence(record: Any) -> str | None:
    """Return the ``source_id`` of an evidence record, or ``None``."""
    if record is None:
        return None
    if hasattr(record, "source_id"):
        value = getattr(record, "source_id", None)
        if isinstance(value, str) and value:
            return value
    if isinstance(record, Mapping):
        value = record.get("source_id")
        if isinstance(value, str) and value:
            return value
    return None


def _evidence_refs_of_claim(record: Any) -> list[str]:
    """Return the list of evidence span ids cited by a claim record."""
    if record is None:
        return []
    out: list[str] = []
    # Plural form (the standard one).
    if hasattr(record, "evidence_span_ids"):
        value = getattr(record, "evidence_span_ids", None)
        if isinstance(value, (list, tuple)):
            out.extend(str(v) for v in value if isinstance(v, str) and v)
    # Singular form (older record variants).
    if hasattr(record, "evidence_id"):
        value = getattr(record, "evidence_id", None)
        if isinstance(value, str) and value:
            out.append(value)
    if isinstance(record, Mapping):
        v = record.get("evidence_span_ids")
        if isinstance(v, (list, tuple)):
            out.extend(str(x) for x in v if isinstance(x, str) and x)
        v = record.get("evidence_id")
        if isinstance(v, str) and v:
            out.append(v)
    # Deduplicate while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def _has_cycle(start_id: str, adjacency: Mapping[str, tuple[CitationEdge, ...]]) -> bool:
    """Defensive: check if a cycle is reachable from ``start_id``."""
    # Iterative DFS with a "currently on the path" set.
    on_path: set[str] = set()
    visited: set[str] = set()
    stack: list[tuple[str, Iterator[CitationEdge]]] = [
        (start_id, iter(adjacency.get(start_id, ())))
    ]
    on_path.add(start_id)
    while stack:
        node, it = stack[-1]
        advanced = False
        for edge in it:
            if edge.to_id in on_path:
                return True
            if edge.to_id not in visited:
                visited.add(edge.to_id)
                on_path.add(edge.to_id)
                stack.append((edge.to_id, iter(adjacency.get(edge.to_id, ()))))
                advanced = True
                break
        if not advanced:
            on_path.discard(node)
            stack.pop()
    return False


def _find_cycle(
    start_id: str, adjacency: Mapping[str, tuple[CitationEdge, ...]]
) -> list[str]:
    """Return the cycle reachable from ``start_id``, as a list of node ids.

    Used for the error message of :meth:`CitationGraph.from_bundle`
    when a cycle is detected. Returns ``[]`` if no cycle is
    reachable (the caller should not invoke this in that case).
    """
    on_path: list[str] = []
    on_path_set: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> list[str] | None:
        on_path.append(node)
        on_path_set.add(node)
        for edge in adjacency.get(node, ()):
            nxt = edge.to_id
            if nxt in on_path_set:
                # Cycle: from on_path[?] to nxt, then back to nxt
                idx = on_path.index(nxt)
                return on_path[idx:] + [nxt]
            if nxt not in visited:
                visited.add(nxt)
                result = visit(nxt)
                if result is not None:
                    return result
        on_path.pop()
        on_path_set.discard(node)
        return None

    return visit(start_id) or []
