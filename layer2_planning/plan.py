"""The plan.json contract (PRD Section 6) — the boundary to Layer 3.

"steps[] order is authoritative and graph-derived; description is the only
LLM-authored field." Everything in this module exists to make that sentence
checkable rather than aspirational: `description` is the single field a model
touches, and `order`, `action`, `requires`, `produces`, `uses` and `interface`
all come straight off the graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rulebook_generator.parse import execution_of, sentences

CONTRACT_VERSION = "1.0"


@dataclass(frozen=True)
class Step:
    order: int
    action: str
    description: str
    requires: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    #: How to run the step - `{kind, name, type}`, e.g. a topic to publish to.
    #: None when the rulebook did not say, which is every rulebook built from a
    #: video or a manual.
    interface: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "order": self.order,
            "action": self.action,
            "description": self.description,
            "requires": list(self.requires),
            "produces": list(self.produces),
            "uses": list(self.uses),
            "interface": dict(self.interface) if self.interface else None,
        }


@dataclass
class Plan:
    goal: str
    command: str
    steps: list[Step] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def actions(self) -> list[str]:
        return [step.action for step in self.steps]

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "command": self.command,
            "steps": [step.to_dict() for step in self.steps],
            "meta": self.meta,
        }


@dataclass
class Clarification:
    """What Layer 2 returns instead of a plan when the goal is not clear (FR-2)."""

    command: str
    reason: str
    candidates: list[dict[str, Any]] = field(default_factory=list)
    ambiguous: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "clarification_needed": True,
            "command": self.command,
            "reason": self.reason,
            "ambiguous": self.ambiguous,
            "candidates": self.candidates,
        }


def interface_of(evidence: str) -> dict[str, str] | None:
    """The execution handle stated in a step's decomposition evidence, if any.

    Read with the rulebook parser's own sentence matcher, so the sentence the
    renderer wrote and the one read back here cannot drift.
    """
    for sentence in sentences(evidence or ""):
        handle = execution_of(sentence)
        if handle is not None:
            return handle.to_dict()
    return None


def build_plan(
    command: str,
    goal: str,
    steps: list[str],
    descriptions: dict[str, str],
    subgraph: Any,
    meta: dict[str, Any],
) -> Plan:
    """Assemble the contract. Order and membership come from `steps`, nothing else."""
    return Plan(
        goal=goal,
        command=command,
        steps=[
            Step(
                order=position,
                action=action,
                description=descriptions.get(action, ""),
                requires=list(subgraph.requires.get(action, ())),
                produces=list(subgraph.produces.get(action, ())),
                uses=list(subgraph.uses.get(action, ())),
                interface=interface_of(subgraph.evidence.get(action, "")),
            )
            for position, action in enumerate(steps, start=1)
        ],
        meta=meta,
    )
