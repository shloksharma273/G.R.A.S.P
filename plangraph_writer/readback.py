"""Reading the written PlanGraph back (PRD Section 8, acceptance criterion 4).

Section 3 puts planning and traversal firmly in Layer 2, so this is not the
planner — it is the verification that the read contract Layer 2 depends on
actually holds once the graph is written: a traversal from a Skill node returns
its precondition-closed subgraph, ready for topological ordering.

Read-only, and scoped to one task.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from rule_preclassifier.table import DECOMPOSES_TO, PRECEDES, PRODUCES, REQUIRES, USES

from .guard import GuardedDatabase
from .schema import Schema

_BY_SCOPE = "FOR doc IN @@collection FILTER doc.skill_scope == @scope RETURN doc"


@dataclass
class PlanSubgraph:
    """One task's precondition-closed subgraph, as Layer 2 would receive it."""

    skill: str | None = None
    primitives: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    objects: list[str] = field(default_factory=list)
    requires: dict[str, list[str]] = field(default_factory=dict)
    produces: dict[str, list[str]] = field(default_factory=dict)
    precedes: list[tuple[str, str]] = field(default_factory=list)
    uses: dict[str, list[str]] = field(default_factory=dict)

    def topological_order(self) -> list[str] | None:
        """A valid execution sequence over the written `precedes` edges."""
        from direction_normalizer.cycles import OrderingGraph

        graph = OrderingGraph()
        for head, tail in self.precedes:
            graph.add(head, tail)
        order = graph.topological_order()
        if order is None:
            return None
        # Primitives with no ordering constraint still belong in the plan.
        unordered = [p for p in sorted(self.primitives) if p not in set(order)]
        return order + unordered

    def is_precondition_closed(self) -> bool:
        """Whether every state a primitive requires is produced within this task.

        The honest check for "precondition-closed": a plan cannot be executed if
        it needs a state nothing in the task establishes.
        """
        return not self.unmet_preconditions()

    def unmet_preconditions(self) -> list[tuple[str, str]]:
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
            "primitives": self.primitives,
            "states": self.states,
            "objects": self.objects,
            "requires": self.requires,
            "produces": self.produces,
            "precedes": [list(pair) for pair in self.precedes],
            "uses": self.uses,
            "unmet_preconditions": [list(pair) for pair in self.unmet_preconditions()],
        }


def read_subgraph(db: Any, schema: Schema, skill_scope: str) -> PlanSubgraph:
    """Everything Layer 2 would traverse for one task."""
    guard = db if isinstance(db, GuardedDatabase) else GuardedDatabase(db, schema)

    names: dict[str, str] = {}
    subgraph = PlanSubgraph()

    for collection in schema.vertex_collections:
        if not guard.has_collection(collection):
            continue
        for document in guard.aql(_BY_SCOPE, {"@collection": collection, "scope": skill_scope}):
            key = document["_key"]
            name = document.get("name", key)
            names[f"{collection}/{key}"] = name
            entity_type = document.get("type")
            if entity_type == "SKILL":
                subgraph.skill = name
            elif entity_type == "PRIMITIVE":
                subgraph.primitives.append(name)
            elif entity_type == "STATE":
                subgraph.states.append(name)
            elif entity_type == "OBJECT":
                subgraph.objects.append(name)

    for group in (subgraph.primitives, subgraph.states, subgraph.objects):
        group.sort()

    if not guard.has_collection(schema.edge_collection):
        return subgraph

    requires: defaultdict[str, list[str]] = defaultdict(list)
    produces: defaultdict[str, list[str]] = defaultdict(list)
    uses: defaultdict[str, list[str]] = defaultdict(list)

    for document in guard.aql(
        _BY_SCOPE, {"@collection": schema.edge_collection, "scope": skill_scope}
    ):
        head = names.get(document["_from"])
        tail = names.get(document["_to"])
        if head is None or tail is None:
            continue  # an endpoint outside this scope; never chain across tasks
        edge_type = document.get("type")
        if edge_type == REQUIRES:
            requires[head].append(tail)
        elif edge_type == PRODUCES:
            produces[head].append(tail)
        elif edge_type == PRECEDES:
            subgraph.precedes.append((head, tail))
        elif edge_type == USES:
            uses[head].append(tail)
        elif edge_type == DECOMPOSES_TO:
            pass  # the skill's children are exactly `primitives`

    subgraph.requires = {key: sorted(value) for key, value in sorted(requires.items())}
    subgraph.produces = {key: sorted(value) for key, value in sorted(produces.items())}
    subgraph.uses = {key: sorted(value) for key, value in sorted(uses.items())}
    subgraph.precedes.sort()
    return subgraph
