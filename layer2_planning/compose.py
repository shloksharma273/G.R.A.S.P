"""Stage 4 — plan composition (PRD Section 5, FR-5, FR-6).

"The order is already fixed. The LLM only phrases each step and binds its
objects." That is the whole design: the model is handed a finished sequence and
asked for prose, never for judgement about what runs when.

The grounding guard enforces it. The composed steps must map one-to-one and in
the same order to the primitives from Stage 3; if they do not, the reply is
rejected, reprompted once, and then abandoned in favour of deterministic
templated phrasing. Order and membership come from the graph, never the LLM —
so an LLM failure downgrades the wording and never the correctness.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from llm_disambiguator.provider import Provider, ServiceError

from .traverse import Subgraph

METHOD_LLM = "llm"
METHOD_TEMPLATE = "template"

SYSTEM_PROMPT = """\
You write one short imperative sentence for each step of a robot plan.

You are given an ORDERED list of primitive actions. The order is already decided \
and is not yours to change.

Rules, in order of importance:
1. Return exactly one item per action given, in exactly the same order.
2. Echo each action's `action` value verbatim so the steps can be matched up.
3. `description` is one short imperative sentence naming what the robot does, \
mentioning the objects it acts on where they are given.
4. Do NOT add steps, drop steps, reorder steps, or invent actions, objects or \
states that were not given to you.

Return a JSON object and nothing else:
{"steps": [{"action": "<echoed verbatim>", "description": "<one sentence>"}]}"""

REPROMPT_SUFFIX = (
    "\n\nYour previous reply did not match the given actions one-to-one and in "
    "order. Return ONLY the JSON object, with exactly one item per action, each "
    "action echoed verbatim, in the order given."
)

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class GroundingError(Exception):
    """The composed steps do not match the graph-derived sequence (FR-6)."""


@dataclass
class Composition:
    descriptions: dict[str, str]
    method: str
    model: str | None = None
    reprompted: bool = False
    fallback_reason: str = ""


def humanize(primitive: str) -> str:
    """Deterministic phrasing: `add_tea_leaves` -> `Add tea leaves.`"""
    words = primitive.replace("_", " ").split()
    if not words:
        return ""
    return (" ".join(words)[:1].upper() + " ".join(words)[1:]).strip() + "."


def _article(name: str) -> str:
    """An object name with exactly one article.

    Entity names routinely carry their own - the live graph holds `THE CUP`, and
    the rulebooks say "the mattress" - so prepending one unconditionally produces
    "using the the mattress".
    """
    readable = name.replace("_", " ").strip()
    return readable if readable.lower().startswith(("the ", "a ", "an ")) else f"the {readable}"


def template_descriptions(steps: list[str], subgraph: Subgraph) -> dict[str, str]:
    """Templated phrasing over the fixed order — the always-available fallback.

    Deliberately plain. Its job is to prove that order lives in the graph: with
    the LLM disabled entirely, the plan is still correct, just plainly worded
    (acceptance criterion 5).
    """
    descriptions = {}
    for primitive in steps:
        sentence = humanize(primitive)
        objects = subgraph.uses.get(primitive) or []
        if objects:
            readable = [_article(o) for o in objects]
            joined = readable[0] if len(readable) == 1 else (
                ", ".join(readable[:-1]) + " and " + readable[-1]
            )
            sentence = f"{sentence[:-1]} using {joined}."
        descriptions[primitive] = sentence
    return descriptions


def build_user_message(steps: list[str], subgraph: Subgraph) -> str:
    lines = [f"Skill: {subgraph.skill}", "", "Actions, in order:"]
    for position, primitive in enumerate(steps, start=1):
        parts = [f"{position}. action: {primitive}"]
        if subgraph.uses.get(primitive):
            parts.append("   objects: " + ", ".join(subgraph.uses[primitive]))
        if subgraph.requires.get(primitive):
            parts.append("   requires: " + ", ".join(subgraph.requires[primitive]))
        if subgraph.produces.get(primitive):
            parts.append("   produces: " + ", ".join(subgraph.produces[primitive]))
        lines.extend(parts)
    return "\n".join(lines)


def parse_steps(text: str) -> list[dict[str, Any]]:
    if not text or not text.strip():
        raise GroundingError("the model returned an empty message")
    try:
        payload = json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError as error:
        raise GroundingError(f"reply is not valid JSON ({error})") from None

    if isinstance(payload, list):
        items = payload
    elif isinstance(payload, dict):
        items = payload.get("steps") or payload.get("plan") or payload.get("items")
    else:
        items = None

    if not isinstance(items, list):
        raise GroundingError("expected an object with a 'steps' array")
    return [item for item in items if isinstance(item, dict)]


def check_grounding(items: list[dict[str, Any]], steps: list[str]) -> dict[str, str]:
    """The guard: one-to-one, in order, no additions, no omissions (FR-6)."""
    if len(items) != len(steps):
        raise GroundingError(
            f"the model returned {len(items)} step(s) for {len(steps)} action(s)"
        )

    descriptions: dict[str, str] = {}
    for position, (item, expected) in enumerate(zip(items, steps), start=1):
        actual = str(item.get("action", "")).strip()
        if actual != expected:
            raise GroundingError(
                f"step {position} is {actual!r} but the graph ordered {expected!r}; "
                "the model reordered or renamed the plan"
            )
        description = str(item.get("description", "")).strip()
        descriptions[expected] = description or humanize(expected)
    return descriptions


def compose(
    steps: list[str],
    subgraph: Subgraph,
    provider: Provider | None = None,
    model: str | None = None,
) -> Composition:
    """Phrase each step. Falls back to templates on any failure (FR-5, FR-6)."""
    templates = template_descriptions(steps, subgraph)

    if provider is None or not steps:
        return Composition(
            descriptions=templates,
            method=METHOD_TEMPLATE,
            fallback_reason="" if provider is not None else "no LLM configured",
        )

    user = build_user_message(steps, subgraph)
    reprompted = False

    for attempt in (0, 1):
        system = SYSTEM_PROMPT if attempt == 0 else SYSTEM_PROMPT + REPROMPT_SUFFIX
        try:
            reply = provider.complete(system, user)
            descriptions = check_grounding(parse_steps(reply), steps)
        except GroundingError as error:
            if attempt == 0:
                reprompted = True
                continue
            return Composition(
                descriptions=templates,
                method=METHOD_TEMPLATE,
                model=model,
                reprompted=True,
                fallback_reason=f"grounding guard rejected the reply twice: {error}",
            )
        except ServiceError as error:
            return Composition(
                descriptions=templates,
                method=METHOD_TEMPLATE,
                model=model,
                reprompted=reprompted,
                fallback_reason=f"the LLM was unavailable: {error}",
            )
        else:
            return Composition(
                descriptions=descriptions,
                method=METHOD_LLM,
                model=model,
                reprompted=reprompted,
            )

    return Composition(  # pragma: no cover - the loop always returns
        descriptions=templates, method=METHOD_TEMPLATE, model=model
    )
