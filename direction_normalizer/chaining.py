"""Job 2 — state chaining (FR-3), the station's whole point.

The rule is one sentence:

    for any state S, every primitive that produces S precedes every primitive
    that requires S.

That reconstructs execution order the rulebook never states. Nothing in the chai
corpus says "boil the water before adding tea leaves"; it says boiling *produces*
water_boiling and adding tea leaves *requires* it, and the ordering falls out.

Implemented as a group-by-state join, so it is linear in edges per state, and
emitted in sorted order so repeat runs are byte-identical.
"""

from __future__ import annotations

from collections import defaultdict

from rule_preclassifier.detect import normalize_name
from rule_preclassifier.table import PRODUCES, REQUIRES

from .model import DerivedEdge, FinalizedEdge
from .policy import ALLOW_SELF_CHAIN, CONFIDENCE, METHOD_DERIVED


def chain(
    finalized: list[FinalizedEdge],
) -> tuple[list[DerivedEdge], list[tuple[str, str]]]:
    """Derive `precedes` edges from the canonical requires/produces edges.

    Args:
        finalized: edges already in canonical direction, so `head` is reliably
            the primitive and `tail` the state.

    Returns:
        `(derived_edges, skipped_self_chains)`, both sorted for determinism.
    """
    producers: defaultdict[str, list[FinalizedEdge]] = defaultdict(list)
    requirers: defaultdict[str, list[FinalizedEdge]] = defaultdict(list)

    for edge in finalized:
        if edge.edge_type == PRODUCES:
            producers[normalize_name(edge.tail)].append(edge)
        elif edge.edge_type == REQUIRES:
            requirers[normalize_name(edge.tail)].append(edge)

    derived: list[DerivedEdge] = []
    self_chains: list[tuple[str, str]] = []

    for state_key in sorted(set(producers) & set(requirers)):
        for produced in sorted(producers[state_key], key=_sort_key):
            for required in sorted(requirers[state_key], key=_sort_key):
                if normalize_name(produced.head) == normalize_name(required.head):
                    # One action that both produces and requires a state cannot
                    # precede itself: a data artifact, not an ordering (Section 9).
                    if not ALLOW_SELF_CHAIN:
                        self_chains.append((produced.head, produced.tail))
                        continue
                derived.append(
                    DerivedEdge(
                        head=produced.head,
                        tail=required.head,
                        via_state=produced.tail,
                        producing_relation_key=produced.relation_key,
                        requiring_relation_key=required.relation_key,
                        confidence=CONFIDENCE[METHOD_DERIVED],
                        direction_method=METHOD_DERIVED,
                    )
                )

    return derived, sorted(set(self_chains))


def _sort_key(edge: FinalizedEdge) -> tuple[str, str]:
    return (normalize_name(edge.head), edge.relation_key)


def dedupe(derived: list[DerivedEdge]) -> list[DerivedEdge]:
    """Collapse repeats of the same arrow, keeping the first justification.

    Two actions can be ordered through more than one shared state — `simmer`
    precedes `strain` only via `tea_brewed` here, but a richer rulebook will
    chain the same pair several ways. The arrow is one edge either way; the
    surviving trace names the state that came first in sorted order, so the
    choice is deterministic rather than incidental.
    """
    seen: dict[tuple[str, str], DerivedEdge] = {}
    for edge in derived:
        key = (normalize_name(edge.head), normalize_name(edge.tail))
        seen.setdefault(key, edge)
    return sorted(seen.values(), key=lambda e: (normalize_name(e.head), normalize_name(e.tail)))
