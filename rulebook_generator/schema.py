"""The structured intermediate (PRD Section 6, stage 2).

The rulebook markdown is the deliverable; this is the scaffold it is rendered
from and parsed back into. Keeping the intermediate explicit is what makes the
round-trip check possible — `parse(render(x))` must recover `x`, which is a much
stronger guarantee than "the markdown looked right".

Section 5 is the reason for the shape: preconditions are the load-bearing part
and are almost never stated in narration, so `requires` is a first-class field
the extractor is obliged to fill rather than something inferred later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from rule_preclassifier.detect import normalize_name

#: Verdicts the validation gate can return (Section 6, stage 4).
ACCEPT = "accept"
FLAG = "flag_for_review"
REJECT = "reject"


#: How a primitive is actually invoked on the robot. A ROS 2 driver exposes a
#: service, a topic, or an action; an HTTP stack exposes an endpoint.
CALL_KINDS = ("service", "topic", "action", "api")

#: The verb each kind is executed with, used by the renderer and the parser. They
#: read this one table so the sentence they write and the sentence they read back
#: cannot drift apart.
CALL_PHRASES = {
    "service": "calling the service",
    "topic": "publishing to the topic",
    "action": "sending a goal to the action",
    "api": "calling the endpoint",
}


@dataclass
class Interface:
    """The concrete handle a planner needs in order to *run* a step.

    Deliberately not an object and not a state. An object is a thing acted on and
    a state is a condition of the world; this is neither. It is the address of the
    action, and without it a plan can say what to do and not how to do it.

    The names are kept verbatim - `/dashboard_client/brake_release`, not
    `dashboard_client_brake_release`. Everything else in a rulebook is normalized
    to snake_case, but a ROS name that has been normalized is no longer callable,
    which defeats the entire point of recording it.
    """

    kind: str
    name: str
    type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "name": self.name, "type": self.type}

    @classmethod
    def from_dict(cls, payload: Any) -> "Interface | None":
        if not isinstance(payload, dict):
            return None
        kind = str(payload.get("kind", "")).strip().lower()
        name = str(payload.get("name", "")).strip().strip("`")
        if not name:
            return None
        if kind not in CALL_KINDS:
            kind = "service"
        return cls(kind=kind, name=name, type=str(payload.get("type", "")).strip().strip("`"))

    @property
    def phrase(self) -> str:
        return CALL_PHRASES.get(self.kind, CALL_PHRASES["service"])


@dataclass
class Primitive:
    """One atomic robot action, with its preconditions and effects made explicit."""

    name: str
    narration: str
    requires: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)
    uses: list[str] = field(default_factory=list)
    #: How to actually invoke it, when the source said. None for a rulebook
    #: reconstructed from a video or a manual, which describe what a person does
    #: rather than what a program calls.
    interface: Interface | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "name": self.name,
            "narration": self.narration,
            "requires": list(self.requires),
            "produces": list(self.produces),
            "uses": list(self.uses),
        }
        if self.interface is not None:
            payload["interface"] = self.interface.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Primitive":
        return cls(
            name=sanitize_name(str(payload.get("name", ""))),
            narration=str(payload.get("narration", "")).strip(),
            requires=_names(payload.get("requires")),
            produces=_names(payload.get("produces")),
            uses=_names(payload.get("uses")),
            interface=Interface.from_dict(payload.get("interface")),
        )


@dataclass
class Rulebook:
    """One skill, in the canonical rulebook's own terms."""

    skill: str
    title: str = ""
    overview: str = ""
    objects: list[str] = field(default_factory=list)
    states: list[str] = field(default_factory=list)
    primitives: list[Primitive] = field(default_factory=list)
    ordering: list[str] = field(default_factory=list)
    source_url: str = ""

    @property
    def primitive_names(self) -> list[str]:
        return [p.name for p in self.primitives]

    def by_name(self, name: str) -> Primitive | None:
        key = normalize_name(name)
        for primitive in self.primitives:
            if normalize_name(primitive.name) == key:
                return primitive
        return None

    @property
    def produced_states(self) -> set[str]:
        return {s for p in self.primitives for s in p.produces}

    @property
    def required_states(self) -> set[str]:
        return {s for p in self.primitives for s in p.requires}

    def to_dict(self) -> dict[str, Any]:
        return {
            "skill": self.skill,
            "title": self.title,
            "overview": self.overview,
            "objects": list(self.objects),
            "states": list(self.states),
            "primitives": [p.to_dict() for p in self.primitives],
            "ordering": list(self.ordering),
            "source_url": self.source_url,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Rulebook":
        skill = sanitize_name(str(payload.get("skill", "")))
        primitives = [
            Primitive.from_dict(item)
            for item in (payload.get("primitives") or [])
            if isinstance(item, dict)
        ]
        return cls(
            skill=skill,
            title=str(payload.get("title", "")).strip() or _titleize(skill),
            overview=str(payload.get("overview", "")).strip(),
            objects=_names(payload.get("objects")),
            states=_names(payload.get("states")),
            primitives=[p for p in primitives if p.name],
            ordering=[str(o).strip() for o in (payload.get("ordering") or []) if str(o).strip()],
            source_url=str(payload.get("source_url", "")).strip(),
        )


def sanitize_name(raw: str) -> str:
    """A normalized name that survives the canonical rulebook's prose lists.

    The template writes objects and states as "a, b, and c", so a name that
    itself contains "and" - `pasta_oiled_and_mixed` - reads back as two names and
    the round-trip check rejects the whole rulebook. Folding the conjunction is
    lossless enough and keeps the format's own convention intact. A conjunction
    in a state name is usually two states anyway; the prompt asks the model not
    to write them, and this is the backstop.
    """
    name = normalize_name(raw)
    while "_and_" in name:
        name = name.replace("_and_", "_")
    return name


def _names(value: Any) -> list[str]:
    """A list of sanitized names, deduplicated, order preserved."""
    if not isinstance(value, list):
        return []
    seen: dict[str, None] = {}
    for item in value:
        name = sanitize_name(str(item))
        if name:
            seen.setdefault(name, None)
    return list(seen)


def _titleize(skill: str) -> str:
    return " ".join(word.capitalize() for word in skill.replace("_", " ").split())


#: The JSON shape the extractor must return (FR-3). Sent to the model verbatim,
#: so the contract the prompt states and the contract the parser enforces cannot
#: drift apart.
INTERMEDIATE_SCHEMA = {
    "skill": "snake_case name of the whole task, e.g. make_masala_chai",
    "title": "Human-readable title, e.g. Masala Chai",
    "overview": "Two or three sentences describing the task.",
    "objects": ["snake_case physical things acted on, e.g. pan, water, tea_leaves"],
    "states": ["snake_case world conditions, e.g. water_boiling, pan_on_stove"],
    "primitives": [
        {
            "name": "snake_case action, e.g. add_water",
            "narration": "One sentence: what the robot does.",
            "requires": ["states that must ALREADY be true before this action can run"],
            "produces": ["states that become true AFTER this action runs"],
            "uses": ["objects this action acts on"],
        }
    ],
    "ordering": ["Sentences stating the order, e.g. The water must boil before tea is added."],
}
