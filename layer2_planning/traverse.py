"""Stage 2 — subgraph retrieval (PRD Section 5, FR-3).

A single forward traversal from the goal skill over the named graph, bounded to
that `skill_scope`. Because every edge type points away from the thing that needs
it — a skill decomposes to its primitives, a primitive requires and produces its
states, uses its objects, and precedes the next primitive — one OUTBOUND walk
reaches the whole precondition-closed subgraph.

The scope filter is what keeps one recipe out of another's plan. It is redundant
with Station 5's scoped keys and deliberately kept anyway: two independent
guarantees against the failure that would be hardest to notice.

Read-only (FR-8).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from rule_preclassifier.table import DECOMPOSES_TO, PRECEDES, PRODUCES, REQUIRES, USES
from plangraph_writer.schema import Schema

#: Reachability from the goal. `uniqueVertices: global` is what makes this cheap
#: - each vertex is visited once - and it is also why the edges cannot be read
#: from the same query: a state reached once is never approached again, so every
#: edge into it after the first is simply never emitted. That silently drops most
#: of the `requires` edges and leaves the plan unordered.
REACHABLE = """
FOR v IN 1..@depth OUTBOUND @start GRAPH @graph
  OPTIONS {uniqueVertices: 'global', bfs: true}
  FILTER v.skill_scope == @scope
  RETURN v
"""

#: Every edge of the task, read once the reachable set is known. Indexed on
#: skill_scope by Station 5, and filtered down to the reachable vertices so the
#: result is still exactly the subgraph the goal reaches.
SCOPE_EDGES = """
FOR e IN @@edges
  FILTER e.skill_scope == @scope
  RETURN e
"""


class IncompletePlanGraph(Exception):
    """The goal skill has no primitives — the graph is incomplete (Section 10)."""


@dataclass
class Subgraph:
    """The precondition-closed subgraph for one goal."""

    skill: str
    skill_scope: str
    primitives: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    requires: dict[str, list[str]] = field(default_factory=dict)
    produces: dict[str, list[str]] = field(default_factory=dict)
    uses: dict[str, list[str]] = field(default_factory=dict)
    precedes: list[tuple[str, str]] = field(default_factory=list)
    #: Descriptions carried through for the composer's grounding.
    evidence: dict[str, str] = field(default_factory=dict)

    def orphan_preconditions(self) -> list[tuple[str, str]]:
        """States a primitive needs that nothing in this task produces (Section 10)."""
        produced = {state for states in self.produces.values() for state in states}
        return sorted(
            (primitive, state)
            for primitive, states in self.requires.items()
            for state in states
            if state not in produced
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "skill_scope": self.skill_scope,
            "primitives": self.primitives,
            "states": self.states,
            "objects": self.objects,
            "requires": self.requires,
            "produces": self.produces,
            "uses": self.uses,
            "precedes": [list(pair) for pair in self.precedes],
            "orphan_preconditions": [list(pair) for pair in self.orphan_preconditions()],
        }


def retrieve_subgraph(
    db: Any, schema: Schema, skill_key: str, skill_name: str, skill_scope: str, depth: int = 8
) -> Subgraph:
    """Walk the named graph from the goal skill (FR-3)."""
    if not db.has_collection(schema.edge_collection):
        raise IncompletePlanGraph(
            f"the PlanGraph has no edge collection ({schema.edge_collection!r}), so "
            "there is nothing to traverse. Run Station 5 to write the graph first."
        )

    start = f"{schema.skills_collection}/{skill_key}"
    vertices = list(
        db.aql.execute(
            REACHABLE,
            bind_vars={
                "start": start,
                "graph": schema.graph_name,
                "scope": skill_scope,
                "depth": depth,
            },
        )
    )
    edges = list(
        db.aql.execute(
            SCOPE_EDGES,
            bind_vars={"@edges": schema.edge_collection, "scope": skill_scope},
        )
    )

    subgraph = Subgraph(skill=skill_name, skill_scope=skill_scope)
    names: dict[str, str] = {start: skill_name}
    types: dict[str, str] = {}

    for vertex in vertices:
        names[vertex["_id"]] = vertex.get("name", vertex["_key"])
        types[vertex["_id"]] = vertex.get("type", "")

    requires: defaultdict[str, list[str]] = defaultdict(list)
    produces: defaultdict[str, list[str]] = defaultdict(list)
    uses: defaultdict[str, list[str]] = defaultdict(list)
    seen_edges: set[str] = set()

    for edge in edges:
        if edge["_key"] in seen_edges:
            continue
        seen_edges.add(edge["_key"])

        head, tail = names.get(edge["_from"]), names.get(edge["_to"])
        if head is None or tail is None:
            continue  # an endpoint the goal does not reach, or another task's

        edge_type = edge.get("type")
        if edge_type == DECOMPOSES_TO:
            subgraph.evidence.setdefault(tail, edge.get("evidence", "") or "")
        elif edge_type == REQUIRES:
            requires[head].append(tail)
        elif edge_type == PRODUCES:
            produces[head].append(tail)
        elif edge_type == USES:
            uses[head].append(tail)
        elif edge_type == PRECEDES:
            subgraph.precedes.append((head, tail))

    for identifier, name in names.items():
        entity_type = types.get(identifier)
        if entity_type == "PRIMITIVE":
            subgraph.primitives.append(name)
        elif entity_type == "STATE":
            subgraph.states.append(name)
        elif entity_type == "OBJECT":
            subgraph.objects.append(name)

    subgraph.primitives = sorted(set(subgraph.primitives))
    subgraph.states = sorted(set(subgraph.states))
    subgraph.objects = sorted(set(subgraph.objects))
    subgraph.requires = {k: sorted(set(v)) for k, v in sorted(requires.items())}
    subgraph.produces = {k: sorted(set(v)) for k, v in sorted(produces.items())}
    subgraph.uses = {k: sorted(set(v)) for k, v in sorted(uses.items())}
    subgraph.precedes = sorted(set(subgraph.precedes))

    if not subgraph.primitives:
        raise IncompletePlanGraph(
            f"the skill {skill_name!r} has no primitives in the PlanGraph, so there are "
            "no steps to plan. Re-run the bridge for this rulebook; a plan will not be "
            "fabricated from an incomplete graph."
        )
    return subgraph
