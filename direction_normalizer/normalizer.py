"""Station 4's pipeline (PRD Section 5): normalize, then chain, then reconcile.

Order matters. Type-implied edges are normalized first so that `requires` and
`produces` are reliably PRIMITIVE -> STATE before anything tries to chain them;
chaining then reads a canonical graph rather than guessing which end is which.

Pure graph logic: no LLM, no network, no randomness, no clock (FR-7). Station 3
remains the system's only model call.
"""

from __future__ import annotations

from typing import Any, Iterable

from rule_preclassifier.detect import canonical_type, normalize_name
from rule_preclassifier.model import ParkedBundle
from rule_preclassifier.table import AMBIGUOUS_DIRECTION, CONFLICTING_EDGE

from .adapt import adapt_all
from .chaining import chain, dedupe
from .cycles import OrderingGraph
from .model import (
    CREATES_CYCLE,
    STATION,
    DerivedEdge,
    FinalizedEdge,
    InputEdge,
    NormalizationResult,
)
from .policy import (
    CANONICAL_DIRECTION,
    CONFIDENCE,
    CONFIRMED_CONFIDENCE,
    METHOD_DERIVED,
    METHOD_DESCRIPTION,
    METHOD_ORIENTATION,
    METHOD_TYPE_IMPLIED,
    ORDERING_EDGE,
    PREFER_DERIVED_ON_CONFLICT,
    ordering_cues,
)


def normalize(edges: Iterable[Any]) -> NormalizationResult:
    """Fix every edge's direction and derive the ordering (FR-1 ... FR-8)."""
    inputs = adapt_all(edges)
    result = NormalizationResult(total_input=len(inputs))

    # --- Job 1: direction implied by the endpoint types ---------------------
    ordering_inputs: list[InputEdge] = []
    for edge in inputs:
        if edge.edge_type == ORDERING_EDGE:
            ordering_inputs.append(edge)
        else:
            result.finalized.append(_normalize_type_implied(edge))

    # --- Job 2: state chaining over the now-canonical requires/produces -----
    derived, result.self_chains = chain(result.finalized)
    derived = dedupe(derived)
    chained = {(normalize_name(e.head), normalize_name(e.tail)): e for e in derived}

    # --- Reconcile explicit ordering against the derived graph --------------
    resolved = [_resolve_ordering(edge, chained) for edge in ordering_inputs]

    # Derived edges go in first: on a conflict the structural answer wins, so it
    # must already own the arrow by the time the explicit edge is considered.
    graph = OrderingGraph()
    for edge in derived:
        if graph.add(edge.head, edge.tail):
            result.derived.append(edge)
        else:
            # Chaining produced an arrow that would close a cycle. Never emit it
            # (FR-6), but keep it visible: a cyclic chain means two actions each
            # produce a state the other requires, which is a real modelling bug.
            result.refused_derived.append(edge)

    for edge, decision in resolved:
        _place_ordering_edge(edge, decision, chained, graph, result)

    result.assert_conservation()
    return result


# --------------------------------------------------------------------------
# Job 1
# --------------------------------------------------------------------------


def _normalize_type_implied(edge: InputEdge) -> FinalizedEdge:
    """Force the canonical arrow, flipping anything written backwards (FR-2)."""
    head_type, _tail_type = CANONICAL_DIRECTION.get(edge.edge_type, (None, None))
    source_name, target_name = edge.written

    if head_type is None:
        # An edge type with no canonical rule: keep it as written rather than
        # inventing one, and say that is what happened.
        return _finalize(edge, source_name, target_name, METHOD_ORIENTATION, flipped=False)

    if canonical_type(edge.bundle.source.type) == head_type:
        return _finalize(edge, source_name, target_name, METHOD_TYPE_IMPLIED, flipped=False)
    if canonical_type(edge.bundle.target.type) == head_type:
        return _finalize(edge, target_name, source_name, METHOD_TYPE_IMPLIED, flipped=True)

    # Neither endpoint carries the head type. Upstream's hint is the better
    # guide than the raw orientation, so prefer it when it exists.
    if edge.head and edge.tail:
        flipped = normalize_name(edge.head) != normalize_name(source_name)
        return _finalize(edge, edge.head, edge.tail, METHOD_TYPE_IMPLIED, flipped=flipped)
    return _finalize(edge, source_name, target_name, METHOD_ORIENTATION, flipped=False)


def _finalize(
    edge: InputEdge, head: str, tail: str, method: str, flipped: bool
) -> FinalizedEdge:
    return FinalizedEdge(
        bundle=edge.bundle,
        edge_type=edge.edge_type,
        head=head,
        tail=tail,
        direction_method=method,
        upstream_method=edge.upstream_method,
        upstream_station=edge.upstream_station,
        flipped=flipped,
        confidence=edge.confidence,
    )


# --------------------------------------------------------------------------
# Job 2 — the trust hierarchy (Section 5)
# --------------------------------------------------------------------------


def _resolve_ordering(
    edge: InputEdge, chained: dict[tuple[str, str], DerivedEdge]
) -> tuple[InputEdge, dict[str, Any]]:
    """Decide an explicit `precedes` edge's direction by the trust hierarchy (FR-4).

    1. state chaining, 2. description wording, 3. the written orientation as a
    weak tiebreaker, 4. park.
    """
    source, target = edge.written
    forward_key = (normalize_name(source), normalize_name(target))
    backward_key = (forward_key[1], forward_key[0])

    # 1 — structural
    if forward_key in chained:
        return edge, {"method": METHOD_DERIVED, "head": source, "tail": target, "agrees": True}
    if backward_key in chained:
        # Chaining says the opposite of what was written: a genuine conflict.
        return edge, {
            "method": METHOD_DERIVED,
            "head": target,
            "tail": source,
            "agrees": False,
            "conflict": True,
        }

    # 2 — textual
    forward_cues, backward_cues = ordering_cues(edge.bundle.description)
    if forward_cues and not backward_cues:
        return edge, {
            "method": METHOD_DESCRIPTION,
            "head": source,
            "tail": target,
            "cues": forward_cues,
        }
    if backward_cues and not forward_cues:
        return edge, {
            "method": METHOD_DESCRIPTION,
            "head": target,
            "tail": source,
            "cues": backward_cues,
        }

    # 3 — the written orientation, explicitly marked as barely trusted
    if source.strip() and target.strip() and forward_key[0] != forward_key[1]:
        return edge, {"method": METHOD_ORIENTATION, "head": source, "tail": target}

    # 4 — nothing to go on
    return edge, {"method": None}


def _place_ordering_edge(
    edge: InputEdge,
    decision: dict[str, Any],
    chained: dict[tuple[str, str], DerivedEdge],
    graph: OrderingGraph,
    result: NormalizationResult,
) -> None:
    """Apply a resolved ordering edge: finalize, park, or fold into the derived one."""
    method = decision["method"]

    if method is None:
        result.parked.append(
            ParkedBundle(
                bundle=edge.bundle,
                reason_code=AMBIGUOUS_DIRECTION,
                detail=(
                    "no state chain, no ordering cue and no usable orientation "
                    "resolves this precedes edge"
                ),
                station=STATION,
            )
        )
        return

    if decision.get("conflict") and PREFER_DERIVED_ON_CONFLICT:
        # Section 5: the derived edge wins, and the explicit loser is parked for
        # review rather than dropped, because a disagreement is a finding.
        result.parked.append(
            ParkedBundle(
                bundle=edge.bundle,
                reason_code=CONFLICTING_EDGE,
                detail=(
                    f"written as {edge.written[0]} -> {edge.written[1]}, but state "
                    f"chaining derives {decision['head']} -> {decision['tail']}; "
                    "the structural ordering is kept"
                ),
                station=STATION,
            )
        )
        return

    head, tail = decision["head"], decision["tail"]

    if method == METHOD_DERIVED and decision.get("agrees"):
        # An explicit edge that chaining independently confirms: raise the
        # derived edge's confidence and keep the explicit one as the record of
        # provenance, rather than emitting the same arrow twice.
        key = (normalize_name(head), normalize_name(tail))
        existing = chained.get(key)
        if existing is not None:
            _confirm(result, existing)
        result.finalized.append(
            FinalizedEdge(
                bundle=edge.bundle,
                edge_type=edge.edge_type,
                head=head,
                tail=tail,
                direction_method=METHOD_DERIVED,
                upstream_method=edge.upstream_method,
                upstream_station=edge.upstream_station,
                flipped=normalize_name(head) != normalize_name(edge.written[0]),
                confidence=CONFIRMED_CONFIDENCE,
                confirmed_by_chaining=True,
            )
        )
        return

    # An explicit edge with no chain support: kept, at the confidence its
    # evidence earns (FR-5), unless it would close a cycle.
    if not graph.add(head, tail):
        result.parked.append(
            ParkedBundle(
                bundle=edge.bundle,
                reason_code=CREATES_CYCLE,
                detail=(
                    f"{head} -> {tail} would close a cycle in the precedes graph; "
                    "the edge is not added"
                ),
                station=STATION,
            )
        )
        return

    result.finalized.append(
        FinalizedEdge(
            bundle=edge.bundle,
            edge_type=edge.edge_type,
            head=head,
            tail=tail,
            direction_method=method,
            upstream_method=edge.upstream_method,
            upstream_station=edge.upstream_station,
            flipped=normalize_name(head) != normalize_name(edge.written[0]),
            confidence=CONFIDENCE.get(method),
        )
    )


def _confirm(result: NormalizationResult, derived: DerivedEdge) -> None:
    """Mark a derived edge as independently confirmed and raise its confidence."""
    for index, edge in enumerate(result.derived):
        if edge is derived or edge.arrow == derived.arrow:
            result.derived[index] = DerivedEdge(
                head=edge.head,
                tail=edge.tail,
                via_state=edge.via_state,
                producing_relation_key=edge.producing_relation_key,
                requiring_relation_key=edge.requiring_relation_key,
                confidence=CONFIRMED_CONFIDENCE,
                direction_method=edge.direction_method,
                confirms_explicit=True,
            )
            return


def ordering_graph(result: NormalizationResult) -> OrderingGraph:
    """The full precedes graph — derived plus explicit — for sorting or auditing."""
    graph = OrderingGraph()
    for head, tail in result.precedes_edges():
        graph.add(head, tail)
    return graph
