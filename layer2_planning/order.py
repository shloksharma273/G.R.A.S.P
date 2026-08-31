"""Stage 3 — ordering (PRD Section 5, FR-4).

The precedence constraints come from two places, exactly as Section 5 specifies:
the `precedes` edges Station 4 wrote, and the produces→requires state chain read
directly off the subgraph. On a graph the bridge built these agree, because
Station 4 derived the former from the latter — recomputing the chain here is not
redundant, it means Layer 2 still orders correctly against a PlanGraph whose
`precedes` edges are missing or incomplete.

Ties break by name, so the same graph always yields the same sequence. A cycle is
a hard error: validation should have caught it, and no execution order exists.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from direction_normalizer.cycles import OrderingGraph

from .traverse import Subgraph

SOURCE_EDGE = "precedes_edge"
SOURCE_CHAIN = "state_chain"


class CyclicPlan(Exception):
    """The subgraph's ordering constraints contain a cycle (Section 10)."""

    def __init__(self, message: str, edges: list[tuple[str, str]]) -> None:
        super().__init__(message)
        self.edges = edges


@dataclass
class Ordering:
    """A valid execution sequence and where each constraint came from."""

    steps: list[str] = field(default_factory=list)
    constraints: dict[tuple[str, str], set[str]] = field(default_factory=dict)

    @property
    def from_edges(self) -> int:
        return sum(1 for sources in self.constraints.values() if SOURCE_EDGE in sources)

    @property
    def from_chain(self) -> int:
        return sum(1 for sources in self.constraints.values() if SOURCE_CHAIN in sources)

    @property
    def agreed(self) -> int:
        return sum(1 for sources in self.constraints.values() if len(sources) > 1)

    def to_dict(self) -> dict[str, Any]:
        return {
            "steps": self.steps,
            "constraints": len(self.constraints),
            "from_precedes_edges": self.from_edges,
            "from_state_chain": self.from_chain,
            "confirmed_by_both": self.agreed,
        }


def constraints_for(subgraph: Subgraph) -> dict[tuple[str, str], set[str]]:
    """Every ordering constraint, with its provenance."""
    constraints: dict[tuple[str, str], set[str]] = {}

    for head, tail in subgraph.precedes:
        constraints.setdefault((head, tail), set()).add(SOURCE_EDGE)

    # produces S -> requires S, recomputed from the subgraph itself.
    producers: dict[str, list[str]] = {}
    for primitive, states in subgraph.produces.items():
        for state in states:
            producers.setdefault(state, []).append(primitive)

    for primitive, states in subgraph.requires.items():
        for state in states:
            for producer in producers.get(state, ()):
                if producer != primitive:
                    constraints.setdefault((producer, primitive), set()).add(SOURCE_CHAIN)

    return constraints


def order_plan(subgraph: Subgraph) -> Ordering:
    """Topologically sort the subgraph's primitives (FR-4)."""
    constraints = constraints_for(subgraph)

    graph = OrderingGraph()
    rejected: list[tuple[str, str]] = []
    for head, tail in sorted(constraints):
        if not graph.add(head, tail):
            rejected.append((head, tail))

    if rejected:
        raise CyclicPlan(
            "the ordering constraints for this skill contain a cycle, so no valid "
            "execution sequence exists. The offending edge(s): "
            + ", ".join(f"{head} -> {tail}" for head, tail in rejected)
            + ". This is a validation gap upstream, not a planning failure.",
            rejected,
        )

    order = graph.topological_order()
    if order is None:  # pragma: no cover - the incremental guard prevents this
        raise CyclicPlan("the ordering graph is cyclic", [])

    # Primitives no constraint mentions still belong in the plan; they can run at
    # any point, so they go last in a stable, name-ordered block.
    ordered = set(order)
    unconstrained = [p for p in subgraph.primitives if p not in ordered]
    return Ordering(steps=order + sorted(unconstrained), constraints=constraints)
