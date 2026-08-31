"""The incremental cycle guard (FR-6) and a topological sort for verification.

An ordering graph with a cycle is not a plan: no execution sequence satisfies it.
So edges are added one at a time and any arrow whose tail already reaches its head
is refused before it goes in, which means the graph is never allowed to become
cyclic rather than being audited afterwards.

Only `precedes` needs guarding. In canonical form `requires` and `produces` both
run PRIMITIVE -> STATE, so those edges form a bipartite graph with every arrow
pointing the same way across the split — it cannot contain a cycle. The whole-graph
audit is the validation wrapper's job (Section 3); this is the local guarantee that
Station 4 never *introduces* one.
"""

from __future__ import annotations

from collections import defaultdict, deque

from rule_preclassifier.detect import normalize_name


class OrderingGraph:
    """A `precedes` DAG that refuses any edge which would close a cycle."""

    def __init__(self) -> None:
        self._out: defaultdict[str, set[str]] = defaultdict(set)
        self._nodes: dict[str, str] = {}  # normalized key -> display name

    def would_cycle(self, head: str, tail: str) -> bool:
        """Whether adding head -> tail would create a cycle."""
        source, target = normalize_name(head), normalize_name(tail)
        if source == target:
            return True  # a self-loop is the shortest cycle there is
        return self._reaches(target, source)

    def add(self, head: str, tail: str) -> bool:
        """Add the edge unless it would close a cycle. Returns whether it went in."""
        if self.would_cycle(head, tail):
            return False
        source, target = normalize_name(head), normalize_name(tail)
        self._nodes.setdefault(source, head)
        self._nodes.setdefault(target, tail)
        self._out[source].add(target)
        return True

    def _reaches(self, start: str, goal: str) -> bool:
        """Breadth-first reachability — the incremental half of the guard."""
        if start == goal:
            return True
        seen = {start}
        queue = deque([start])
        while queue:
            for successor in self._out.get(queue.popleft(), ()):
                if successor == goal:
                    return True
                if successor not in seen:
                    seen.add(successor)
                    queue.append(successor)
        return False

    def topological_order(self) -> list[str] | None:
        """A valid execution order, or None if the graph is cyclic.

        Kahn's algorithm with ties broken alphabetically, so the same graph always
        yields the same sequence. This is a *verification* helper — Layer 2 owns
        real planning — but it is what makes the acceptance criterion checkable:
        a sort of the derived graph must read as the recipe.
        """
        indegree = {node: 0 for node in self._nodes}
        for successors in self._out.values():
            for successor in successors:
                indegree[successor] = indegree.get(successor, 0) + 1

        ready = sorted(node for node, degree in indegree.items() if degree == 0)
        order: list[str] = []
        while ready:
            node = ready.pop(0)
            order.append(self._nodes[node])
            for successor in sorted(self._out.get(node, ())):
                indegree[successor] -= 1
                if indegree[successor] == 0:
                    ready.append(successor)
            ready.sort()

        return order if len(order) == len(indegree) else None

    @property
    def nodes(self) -> list[str]:
        return [self._nodes[key] for key in sorted(self._nodes)]

    def __len__(self) -> int:
        return sum(len(successors) for successors in self._out.values())
