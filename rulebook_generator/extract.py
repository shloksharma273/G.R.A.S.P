"""Stage 2 — schema-constrained extraction (PRD Section 6, FR-3).

Section 5 is the whole difficulty: narration is chatty, skips the obvious, says
"this" and "that", and **almost never states a precondition**. Fetching captions
is trivial; reconstructing a precondition graph from them is not. So the prompt
does three things beyond asking for JSON:

* it states the temporal distinction explicitly, because `requires` is the field
  the transcript will not contain and the model has to infer;
* it carries one full worked example — the chai rulebook — so the model sees the
  shape and the grain of the target rather than a description of it;
* it insists that primitives be grounded in the narration while preconditions may
  be inferred, which is the only asymmetry that makes inference safe.

Temperature 0 and a cache keyed on the transcript hash give FR-6. Nothing here
decides whether the result is usable — that is the validation gate's job, and it
is mandatory precisely because this stage is inference.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from llm_disambiguator.provider import Provider, ServiceError

from .schema import INTERMEDIATE_SCHEMA, Rulebook
from .transcript import Transcript

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class ExtractionFailed(Exception):
    """The model did not return a usable intermediate."""


class NotProcedural(Exception):
    """The transcript describes no coherent procedural task (Section 10)."""


SYSTEM_PROMPT = """\
You turn a transcript of a how-to video into a structured robot skill rulebook.

The hard part is NOT transcribing. It is RECONSTRUCTION. Narration is chatty, \
skips obvious steps, says "this" and "that", and almost never states a \
precondition out loud. Your job is to recover the underlying structure:

- primitives   mostly stated - find the discrete action boundaries
- objects      mostly stated - resolve pronouns ("this" -> the pan)
- states       partly stated, largely inferred
- requires     ALMOST NEVER STATED - you must infer these. You cannot add pasta
               before the water boils, even if nobody says so.
- produces     partly stated, partly inferred
- ordering     implied by sequence, refined by the dependencies you inferred

Rules:
1. Every primitive must be GROUNDED: the action must actually be described in \
the transcript. Do not invent steps. Preconditions and effects, by contrast, you \
are expected to infer.
2. A state is a condition of the world that is true or false - `water_boiling`, \
`pan_on_stove`. It is not an action.
3. Every state you put in `requires` should be produced by some earlier \
primitive, unless it is true at the start. Aim for a graph with no gaps.
4. Use snake_case for every name.
5. Never put "and" inside a name. A state called `pasta_oiled_and_mixed` is two \
states; write them separately.
6. If the video covers SEVERAL unrelated tasks - a compilation, a "basics" video, \
a technique roundup - do not fuse them into one skill. Take the FIRST coherent \
task, ignore the rest, and set "multiple_tasks": true.
7. If the transcript is not a procedural how-to task - a vlog, a review, a music \
video, a discussion - return exactly {"not_procedural": true, "reason": "..."} \
and nothing else. Do not force a rulebook out of it.

Return a single JSON object and nothing else, of exactly this shape:
"""

WORKED_EXAMPLE = {
    "skill": "make_masala_chai",
    "title": "Masala Chai",
    "overview": (
        "This rulebook describes the skill of making masala chai as a sequence of "
        "primitive robot actions."
    ),
    "objects": ["stove", "pan", "water", "tea_leaves", "milk", "sugar", "strainer", "cup"],
    "states": [
        "pan_on_stove", "stove_on", "water_in_pan", "water_boiling",
        "tea_brewing", "milk_added", "sugar_added", "tea_brewed", "tea_in_cup",
    ],
    "primitives": [
        {
            "name": "place_pan",
            "narration": "The robot places the pan on the stove.",
            "requires": [],
            "produces": ["pan_on_stove"],
            "uses": ["pan", "stove"],
        },
        {
            "name": "add_water",
            "narration": "The robot pours water into the pan.",
            "requires": ["pan_on_stove"],
            "produces": ["water_in_pan"],
            "uses": ["water", "pan"],
        },
        {
            "name": "boil_water",
            "narration": "The robot heats the water until it boils.",
            "requires": ["water_in_pan", "stove_on"],
            "produces": ["water_boiling"],
            "uses": ["water", "stove"],
        },
    ],
    "ordering": [
        "The pan must be placed on the stove before water is added.",
        "Water must be added and the stove must be on before the water can boil.",
    ],
}

REPROMPT_SUFFIX = (
    "\n\nYour previous reply could not be used. Return ONLY the JSON object of the "
    "shape given above - no prose, no code fences - with every name in snake_case "
    "and every primitive's `requires` and `produces` filled in."
)


def build_system_prompt() -> str:
    return (
        SYSTEM_PROMPT
        + json.dumps(INTERMEDIATE_SCHEMA, indent=2)
        + "\n\nHere is one complete worked example, from a rulebook for masala chai. "
        "Note that `add_water` requires `pan_on_stove` even though no narrator would "
        "ever say so - that is the inference you are being asked to make:\n\n"
        + json.dumps(WORKED_EXAMPLE, indent=2)
    )


def build_user_message(transcript: Transcript) -> str:
    return (
        "Reconstruct the rulebook for the task described in this transcript.\n\n"
        f"TRANSCRIPT ({transcript.words} words):\n{transcript.text}"
    )


@dataclass
class Extraction:
    rulebook: Rulebook | None
    raw: dict[str, Any]
    model: str
    from_cache: bool = False
    reprompted: bool = False
    not_procedural: bool = False
    reason: str = ""
    multiple_tasks: bool = False


def parse_reply(text: str) -> dict[str, Any]:
    if not text or not text.strip():
        raise ExtractionFailed("the model returned an empty message")
    try:
        payload = json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError as error:
        raise ExtractionFailed(f"reply is not valid JSON ({error})") from None
    if not isinstance(payload, dict):
        raise ExtractionFailed(f"expected a JSON object, got {type(payload).__name__}")
    return payload


def validate_shape(payload: dict[str, Any]) -> Rulebook:
    """Turn a reply into an intermediate, or say why it cannot be one."""
    if payload.get("not_procedural"):
        raise NotProcedural(str(payload.get("reason") or "the transcript is not a procedural task"))

    rulebook = Rulebook.from_dict(payload)
    if not rulebook.skill:
        raise ExtractionFailed("the reply names no skill")
    if not rulebook.primitives:
        raise ExtractionFailed("the reply contains no primitive actions")
    return rulebook


def extract(
    transcript: Transcript,
    provider: Provider,
    model: str,
    cache: Any = None,
) -> Extraction:
    """One schema-constrained pass, cached by transcript hash (FR-3, FR-6)."""
    key = f"{model}|{transcript.digest}"
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            try:
                return Extraction(
                    rulebook=validate_shape(cached), raw=cached, model=model, from_cache=True,
                    multiple_tasks=bool(cached.get("multiple_tasks")),
                )
            except NotProcedural as error:
                return Extraction(
                    rulebook=None, raw=cached, model=model, from_cache=True,
                    not_procedural=True, reason=str(error),
                )
            except ExtractionFailed:
                pass  # an unusable cached reply behaves as a miss

    system = build_system_prompt()
    user = build_user_message(transcript)
    reprompted = False

    for attempt in (0, 1):
        prompt = system if attempt == 0 else system + REPROMPT_SUFFIX
        try:
            payload = parse_reply(provider.complete(prompt, user))
        except ExtractionFailed:
            if attempt == 0:
                reprompted = True
                continue
            raise
        except ServiceError:
            raise

        try:
            rulebook = validate_shape(payload)
        except NotProcedural as error:
            if cache is not None:
                cache.put(key, payload)
            return Extraction(
                rulebook=None, raw=payload, model=model,
                reprompted=reprompted, not_procedural=True, reason=str(error),
            )
        except ExtractionFailed:
            if attempt == 0:
                reprompted = True
                continue
            raise

        if cache is not None:
            cache.put(key, payload)
        return Extraction(
            rulebook=rulebook, raw=payload, model=model, reprompted=reprompted,
            multiple_tasks=bool(payload.get("multiple_tasks")),
        )

    raise ExtractionFailed("extraction failed after one reprompt")  # pragma: no cover
