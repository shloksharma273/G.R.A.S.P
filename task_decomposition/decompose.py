"""Splitting one command into the ordered subtasks it asks for.

"go to 2,1 and then charge the robot" is two tasks, and Layer 2 plans exactly
one skill per command - so something has to cut it first. That is the model's
job here, and it is kept as thin as the composer's: it may *split* the command,
*pick* a skill for each piece from the catalog the graph already holds, and
*copy out* the values the user stated. It may not invent a skill, a task the
user did not ask for, or a number the user did not say.

The guard enforces that the way the composer's does. A reply that names a skill
outside the catalog, or a parameter value absent from the command, is rejected,
reprompted once, and then abandoned for the deterministic splitter - so a model
failure downgrades how well the command is understood, never what the plan
claims.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from llm_disambiguator.provider import Provider, ServiceError

METHOD_LLM = "llm"
METHOD_RULES = "rules"

#: More than this is not a command, it is a script; ask for it in pieces.
MAX_SUBTASKS = 8

SYSTEM_PROMPT = """\
You split a robot command into the ordered tasks it asks for, and match each \
task to one skill from a fixed catalog.

Rules, in order of importance:
1. Keep the user's order. "A and then B" is A first, then B.
2. One subtask per skill invocation. "go to 1,0 then go to 2,1" is two \
subtasks of the same skill.
3. Do NOT add tasks the user did not ask for. Every skill already includes its \
own bring-up and prerequisite steps, so never add localization, startup or \
undocking as separate subtasks unless the user asked for them.
4. `skill` is copied verbatim from the catalog, or null if no skill fits. Never \
invent a skill name.
5. `command` is the part of the user's command this subtask covers, in the \
user's own words.
6. `parameters` holds only values the user stated for that subtask, copied as \
stated - coordinates as x and y, a heading as yaw_deg or yaw_rad, a distance as \
distance_m, an angle as angle_deg, a speed as speed_mps, a named place as \
location. Do NOT convert units and do NOT fill in values the user did not say. \
Use {} when there are none.

Return a JSON object and nothing else:
{"subtasks": [{"command": "<words>", "skill": "<catalog name or null>", \
"parameters": {}}]}"""

REPROMPT_SUFFIX = (
    "\n\nYour previous reply was rejected: {reason}. Return ONLY the JSON object. "
    "Copy skill names verbatim from the catalog, and only put values in "
    "`parameters` that appear in the command exactly as the user wrote them."
)

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
_PRIMITIVE = re.compile(r"primitive action (\w+)")

#: Where one task ends and the next begins, for the deterministic splitter.
#: A bare "and" only splits when a word follows it, so "waypoints 1,0 and 2,1"
#: stays one task while "go to 2,1 and charge" becomes two; a full stop only
#: when a space follows it, so "0.3 m" is not cut in half.
_CONNECTOR = re.compile(
    r"\s*(?:;|\.(?=\s|$)|\b(?:and then|then|after that|afterwards|followed by|and)\b(?=\s+[a-z]))\s*",
    re.IGNORECASE,
)


class DecompositionError(Exception):
    """The model's split does not hold up against the catalog or the command."""


@dataclass
class Subtask:
    command: str
    #: The catalog skill the model matched, or None to let goal resolution decide.
    skill: str | None = None
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"command": self.command, "skill": self.skill, "parameters": dict(self.parameters)}


@dataclass
class Decomposition:
    command: str
    subtasks: list[Subtask]
    method: str
    model: str | None = None
    reprompted: bool = False
    fallback_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "method": self.method,
            "model": self.model,
            "subtasks": [s.to_dict() for s in self.subtasks],
        }
        if self.reprompted:
            payload["reprompted"] = True
        if self.fallback_reason:
            payload["fallback_reason"] = self.fallback_reason
        return payload


def catalog_lines(skills: list[dict[str, Any]]) -> list[str]:
    """One line per skill: its name and the steps it runs, which is what tells
    "dock_at_charger" apart from "undock_from_charger" better than the name does."""
    lines = []
    for skill in sorted(skills, key=lambda s: s["name"]):
        steps = list(dict.fromkeys(_PRIMITIVE.findall(skill.get("description") or "")))
        lines.append(f"- {skill['name']}" + (f": {', '.join(steps)}" if steps else ""))
    return lines


def build_user_message(command: str, skills: list[dict[str, Any]]) -> str:
    return "\n".join(
        ["Skill catalog:", *catalog_lines(skills), "", f"Command: {command}"]
    )


def split_by_rules(command: str) -> list[Subtask]:
    """The deterministic fallback: cut on connectors, let goal resolution match.

    Deliberately dumb. It understands no synonyms and extracts no values; it only
    ensures a compound command without a model still becomes several plans
    rather than one wrong one.
    """
    pieces = [p.strip(" ,") for p in _CONNECTOR.split(command or "")]
    return [Subtask(command=p) for p in pieces if p]


def parse_reply(text: str) -> list[dict[str, Any]]:
    if not text or not text.strip():
        raise DecompositionError("the reply was empty")
    try:
        payload = json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError as error:
        raise DecompositionError(f"the reply is not valid JSON ({error})") from None
    items = payload.get("subtasks") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise DecompositionError("expected an object with a 'subtasks' array")
    return items


def _grounded(value: Any, command: str, numbers: set[float]) -> bool:
    """Whether a parameter value is something the user actually said."""
    if isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        return float(value) in numbers
    if isinstance(value, str):
        return value.strip().lower() in command.lower()
    if isinstance(value, list):
        return all(_grounded(v, command, numbers) for v in value)
    return False


def check_reply(items: list[Any], command: str, catalog: set[str]) -> list[Subtask]:
    """The guard: known skills, stated values, a sane count."""
    if not items:
        raise DecompositionError("no subtasks were returned")
    if len(items) > MAX_SUBTASKS:
        raise DecompositionError(f"{len(items)} subtasks, more than the {MAX_SUBTASKS} allowed")

    numbers = {float(n) for n in _NUMBER.findall(command)}
    subtasks: list[Subtask] = []
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise DecompositionError(f"subtask {position} is not an object")
        text = str(item.get("command") or "").strip()
        if not text:
            raise DecompositionError(f"subtask {position} has no command")

        skill = item.get("skill")
        if skill in ("", "null", "none"):
            skill = None
        if skill is not None and skill not in catalog:
            raise DecompositionError(
                f"subtask {position} names the skill {skill!r}, which is not in the catalog"
            )

        parameters = item.get("parameters") or {}
        if not isinstance(parameters, dict):
            raise DecompositionError(f"subtask {position} has parameters that are not an object")
        for key, value in parameters.items():
            if not _grounded(value, command, numbers):
                raise DecompositionError(
                    f"subtask {position} parameter {key}={value!r} does not appear in the command"
                )
        subtasks.append(Subtask(command=text, skill=skill, parameters=dict(parameters)))
    return subtasks


def decompose(
    command: str,
    skills: list[dict[str, Any]],
    provider: Provider | None = None,
    model: str | None = None,
) -> Decomposition:
    """Split a command into subtasks. Falls back to the rule splitter on any failure."""
    rules = split_by_rules(command) or [Subtask(command=command)]
    if provider is None or not skills:
        return Decomposition(
            command=command,
            subtasks=rules,
            method=METHOD_RULES,
            fallback_reason="no LLM configured" if provider is None else "no skills in the graph",
        )

    catalog = {s["name"] for s in skills}
    user = build_user_message(command, skills)
    reason = ""

    for attempt in (0, 1):
        system = SYSTEM_PROMPT if attempt == 0 else SYSTEM_PROMPT + REPROMPT_SUFFIX.format(reason=reason)
        try:
            subtasks = check_reply(parse_reply(provider.complete(system, user)), command, catalog)
        except DecompositionError as error:
            reason = str(error)
            continue
        except ServiceError as error:
            return Decomposition(
                command=command, subtasks=rules, method=METHOD_RULES, model=model,
                reprompted=attempt > 0, fallback_reason=f"the LLM was unavailable: {error}",
            )
        return Decomposition(
            command=command, subtasks=subtasks, method=METHOD_LLM, model=model,
            reprompted=attempt > 0,
        )

    return Decomposition(
        command=command, subtasks=rules, method=METHOD_RULES, model=model, reprompted=True,
        fallback_reason=f"the guard rejected the reply twice: {reason}",
    )
