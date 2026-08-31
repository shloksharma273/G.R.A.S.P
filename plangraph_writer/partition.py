"""Split one KG's edges into per-skill subgraphs (PRD Section 5).

Station 5 writes one task at a time, because identity is scoped per task. The PRD
says to "run the bridge once per rulebook" — but a single AutoGraph project
routinely holds several rulebooks in one knowledge graph, and Stations 1-4 read
all of them together. This is the missing step between: partition Station 4's
output by skill so each task can be written under its own scope from one run.

Membership is reachability. Each skill reaches its primitives through
`decomposes_to`, and through them the states, objects and orderings those
primitives touch. An entity two skills both reach belongs to **both** partitions
and is written once per scope — which is the whole point of scoping, and is why a
shared `stove_on` cannot chain a burger's actions into a chai's plan.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable

from rule_preclassifier.detect import normalize_name
from rule_preclassifier.table import DECOMPOSES_TO

from .identity import scope_key


@dataclass
class Partition:
    """One skill's slice of a multi-skill knowledge graph."""

    skill: str
    scope: str
    finalized: list[Any] = field(default_factory=list)
    derived: list[Any] = field(default_factory=list)

    @property
    def edge_count(self) -> int:
        return len(self.finalized) + len(self.derived)


def _row(edge: Any) -> dict[str, Any]:
    return edge if isinstance(edge, dict) else edge.to_dict()


def _ends(edge: Any) -> tuple[str, str]:
    row = _row(edge)
    return normalize_name(row.get("from") or ""), normalize_name(row.get("to") or "")


def skills_in(finalized: Iterable[Any]) -> list[str]:
    """Every skill that actually decomposes into something.

    A skill entity with no `decomposes_to` edge is not plannable — it was
    extracted as a name and nothing else — so it is not a partition.
    """
    names: dict[str, str] = {}
    for edge in finalized:
        row = _row(edge)
        if row.get("edge_type") == DECOMPOSES_TO:
            head = row.get("from") or ""
            names.setdefault(normalize_name(head), head)
    return [names[key] for key in sorted(names)]


def partition_by_skill(
    finalized: Iterable[Any], derived: Iterable[Any]
) -> list[Partition]:
    """Group edges into one partition per skill, by reachability."""
    finalized = list(finalized)
    derived = list(derived)

    adjacency: defaultdict[str, set[str]] = defaultdict(set)
    for edge in finalized + derived:
        head, tail = _ends(edge)
        if head and tail:
            adjacency[head].add(tail)

    partitions: list[Partition] = []
    for skill in skills_in(finalized):
        reachable = _reach(normalize_name(skill), adjacency)
        partition = Partition(skill=skill, scope=scope_key(skill))
        for edge in finalized:
            head, tail = _ends(edge)
            if head in reachable and tail in reachable:
                partition.finalized.append(edge)
        for edge in derived:
            head, tail = _ends(edge)
            if head in reachable and tail in reachable:
                partition.derived.append(edge)
        partitions.append(partition)
    return partitions


def _reach(start: str, adjacency: dict[str, set[str]]) -> set[str]:
    seen = {start}
    queue = deque([start])
    while queue:
        for successor in adjacency.get(queue.popleft(), ()):
            if successor not in seen:
                seen.add(successor)
                queue.append(successor)
    return seen


def orphan_skills(finalized: Iterable[Any], all_skill_names: Iterable[str]) -> list[str]:
    """Skills present as entities but with nothing to decompose into.

    Reported rather than written: a skill with no primitives would resolve as a
    goal and then yield an empty plan, which is worse than not being there.
    """
    decomposing = {normalize_name(s) for s in skills_in(finalized)}
    return sorted(
        name for name in all_skill_names if normalize_name(name) not in decomposing
    )
