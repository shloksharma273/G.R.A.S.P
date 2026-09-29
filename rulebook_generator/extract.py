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

import hashlib
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


MANUAL_SYSTEM_PROMPT = """\
You turn a section of written technical documentation - an operating manual, a \
procedure, a reference page - into a structured robot skill rulebook.

This is NOT a video transcript, and the difference matters. A narrator skips \
preconditions; a manual is largely *made* of them. So the job inverts:

- primitives   stated as steps, headings or imperative sentences
- objects      stated - the components, subsystems and parameters named
- states       mostly stated as conditions, readiness criteria, checks
- requires     MOSTLY STATED. Read them off the page. A line like "before arming,
               confirm GPS lock is acquired" IS a precondition; capture it. Infer
               one only where the document plainly implies it and never states it.
- produces     mostly stated - what the step leaves true, what the system enters
- ordering     stated by sequence, numbering, and the conditions themselves

Rules:
1. Do NOT invent preconditions. In a transcript, inferring an unstated \
precondition is the job; in a manual, an invented one contradicts a document \
that was specific on purpose. If the manual states a condition, use its wording. \
If it states none for a step, leave `requires` empty rather than guessing.
2. Every primitive must be GROUNDED: the action must actually appear in the \
document. Do not add steps the manual does not describe.
3. A state is a condition of the system that is true or false - `gps_lock_acquired`, \
`vehicle_armed`. It is not an action and not a parameter value.
4. Keep the document's own vocabulary. If it says "prearmed", the state is \
`vehicle_prearmed`, not `motors_partly_on`. Downstream tools match on these names.
5. Numeric thresholds and parameter names belong in `narration`, not in state \
names. `battery_above_minimum` is a state; `COM_ARM_BAT_MIN` and its value are \
narration.
6. Use snake_case for every name, and never put "and" inside a name. A state \
called `armed_and_airborne` is two states; write them separately.
7. Every state in `requires` should be produced by an earlier primitive unless it \
is true at the start. Aim for a graph with no gaps.
8. A manual usually documents SEVERAL procedures. Cover the one continuous \
procedure this section is about. If the text covers unrelated procedures, take the \
FIRST coherent one, ignore the rest, and set "multiple_tasks": true.
9. If the text is not a procedure at all - a parameter table, a changelog, a \
specification list - return exactly {"not_procedural": true, "reason": "..."} and \
nothing else.

Return a single JSON object and nothing else, of exactly this shape:
"""

#: A worked example in the manual register, deliberately from an unrelated domain
#: so it teaches the *grain* - stated preconditions, the document's own words -
#: rather than handing over vocabulary the model might parrot back.
MANUAL_WORKED_EXAMPLE = {
    "skill": "run_centrifuge_cycle",
    "title": "Centrifuge Cycle",
    "overview": (
        "This rulebook describes running a cycle on a benchtop centrifuge as a "
        "sequence of primitive robot actions, with the preconditions the operating "
        "manual states for each step."
    ),
    "objects": ["centrifuge", "rotor", "lid", "sample_tubes", "control_panel"],
    "states": [
        "rotor_seated", "load_balanced", "lid_closed", "lid_latched",
        "speed_set", "cycle_running", "rotor_stopped",
    ],
    "primitives": [
        {
            "name": "seat_rotor",
            "narration": "The robot seats the rotor on the drive spindle and tightens the nut.",
            "requires": [],
            "produces": ["rotor_seated"],
            "uses": ["rotor", "centrifuge"],
        },
        {
            "name": "load_tubes",
            "narration": (
                "The robot loads sample tubes in opposing pairs of equal mass. The manual "
                "requires the rotor to be seated and the load balanced to within 0.5 g."
            ),
            "requires": ["rotor_seated"],
            "produces": ["load_balanced"],
            "uses": ["sample_tubes", "rotor"],
        },
        {
            "name": "close_lid",
            "narration": "The robot closes the lid until the latch engages.",
            "requires": ["load_balanced"],
            "produces": ["lid_closed", "lid_latched"],
            "uses": ["lid"],
        },
        {
            "name": "start_cycle",
            "narration": (
                "The robot presses start. The manual states that the cycle will not begin "
                "unless the lid is latched and a speed has been set."
            ),
            "requires": ["lid_latched", "speed_set"],
            "produces": ["cycle_running"],
            "uses": ["control_panel", "centrifuge"],
        },
    ],
    "ordering": [
        "The rotor must be seated before tubes are loaded.",
        "The load must be balanced and the lid latched before the cycle can start.",
    ],
}


CODE_SYSTEM_PROMPT = """\
You turn the source of a robot driver or service into a structured robot skill \
rulebook that can actually be EXECUTED.

This is neither a transcript nor a manual. It is the code and interface \
definitions of a system that exposes operations over an API - a ROS 2 driver \
advertising services, topics and actions, or an HTTP service exposing endpoints. \
So one field matters more than everything else:

- interface   THE POINT. For every primitive, the concrete handle a program must
              call to run it: the kind (service / topic / action / api), the name
              EXACTLY as the source writes it, and the message or type name.
              A plan without this can say what to do and not how to do it.
- primitives  the operations the system exposes - one per callable thing
- objects     the parts acted on (gripper, arm, brakes, program)
- states      conditions of the robot the calls require and produce
- requires    what must be true before this call will succeed
- produces    what is true after it returns
- ordering    implied by those conditions

Rules:
1. COPY INTERFACE NAMES VERBATIM. `/dashboard_client/brake_release` is a name; \
`dashboard_client_brake_release` is not, and will not resolve. Keep the leading \
slash, the namespace and the case exactly as the source writes them. Do the same \
for the type: `std_srvs/srv/Trigger`, not `Trigger`.
2. A shell command - `ros2 launch ...`, `source setup.bash && export ...` - is NOT \
an interface. It is something an operator types. Leave the interface out for such \
a step rather than putting the command in it.
3. NEVER INVENT AN INTERFACE. If the source does not state the name of a call, \
omit the interface for that primitive rather than guessing one. An invented \
service name is a call that fails at runtime, which is worse than an absent one \
that fails honestly at review.
4. Every primitive must be GROUNDED: the operation must actually be declared or \
documented in the text - a `create_service` call, a `.srv` file, a documented \
endpoint. Do not add steps the source does not expose.
5. Preconditions and effects ARE usually inferrable here and you should infer \
them: a driver that exposes `power_on` and `brake_release` implies the robot must \
be powered before the brakes release. Say so.
6. A state is a condition of the robot that is true or false - `robot_powered_on`, \
`brakes_released`, `program_running`. It is not a call and not a message type.
7. Use snake_case for skill, primitive, object and state names. Interface names \
and types are the ONE exception: they stay exactly as written.
8. Never put "and" inside a name.
9. The task is usually implicit - the source exposes capabilities rather than one \
procedure. Assemble the primitives into the one coherent task the user asked for, \
in the order their preconditions imply. If the source covers several unrelated \
subsystems, take the FIRST coherent task and set "multiple_tasks": true.
10. If the text exposes no callable operations at all - a build file, a licence, a \
changelog, a pure data structure - return exactly {"not_procedural": true, \
"reason": "..."} and nothing else.

Return a single JSON object and nothing else, of exactly this shape:
"""

#: The code prompt's schema is the shared one plus the field that makes a rulebook
#: executable. Kept separate rather than added to `INTERMEDIATE_SCHEMA`, because a
#: video or a manual describes what a person does and has no handles to give - and
#: a prompt that asks for one there invites the model to invent it.
CODE_SCHEMA = {
    **INTERMEDIATE_SCHEMA,
    "primitives": [
        {
            **INTERMEDIATE_SCHEMA["primitives"][0],
            "interface": {
                "kind": "service | topic | action | api",
                "name": "EXACTLY as the source writes it, with its leading slash and namespace",
                "type": "the message or service type, e.g. std_srvs/srv/Trigger",
            },
        }
    ],
}

#: A worked example in the code register. Deliberately a mobile base rather than
#: an arm: it has to teach the *shape* - a verbatim handle per step, states that
#: are robot conditions - without handing over names the model might paste back
#: into a rulebook for a different robot.
CODE_WORKED_EXAMPLE = {
    "skill": "drive_to_charging_dock",
    "title": "Drive To Charging Dock",
    "overview": (
        "This rulebook describes driving a mobile base to its charging dock as a "
        "sequence of primitive robot actions, each with the ROS 2 interface a "
        "program calls to execute it."
    ),
    "objects": ["motor_controller", "costmap", "base", "dock"],
    "states": [
        "motors_enabled", "costmap_clear", "goal_accepted", "base_at_dock",
    ],
    "primitives": [
        {
            "name": "enable_motors",
            "narration": "The robot enables the motor controller.",
            "requires": [],
            "produces": ["motors_enabled"],
            "uses": ["motor_controller"],
            "interface": {
                "kind": "service",
                "name": "/motor_controller/enable",
                "type": "std_srvs/srv/Trigger",
            },
        },
        {
            "name": "clear_costmap",
            "narration": "The robot clears the stale costmap before planning.",
            "requires": ["motors_enabled"],
            "produces": ["costmap_clear"],
            "uses": ["costmap"],
            "interface": {
                "kind": "service",
                "name": "/global_costmap/clear_entirely_global_costmap",
                "type": "nav2_msgs/srv/ClearEntireCostmap",
            },
        },
        {
            "name": "send_navigation_goal",
            "narration": "The robot sends the dock pose as a navigation goal.",
            "requires": ["motors_enabled", "costmap_clear"],
            "produces": ["goal_accepted", "base_at_dock"],
            "uses": ["base", "dock"],
            "interface": {
                "kind": "action",
                "name": "/navigate_to_pose",
                "type": "nav2_msgs/action/NavigateToPose",
            },
        },
    ],
    "ordering": [
        "The motors must be enabled before the costmap is cleared.",
        "The motors must be enabled and the costmap clear before a goal is sent.",
    ],
}

#: Appended in split mode, where the first pass is a *reference* rulebook for the
#: whole system rather than one task. It overrides each register's "take the first
#: coherent task" rule, because the tasks are cut out of this afterwards - a step
#: the reference leaves out is a step no task rulebook can ever contain.
CATALOG_SUFFIX = """

THIS RUN IS DIFFERENT: build a REFERENCE rulebook for the WHOLE system, not one \
task. It will afterwards be sliced into one rulebook per task a user can ask for, \
so every operation the source describes must be in it.

- Cover EVERY operation, procedure and command the source describes, including \
bring-up, recovery, maintenance and test steps. Ignore the rule about taking only \
the first task, and do not set "multiple_tasks".
- Keep each step's preconditions COMPLETE. A task is cut out by following \
`requires` back to the steps that produce it, so a missing precondition drops a \
bring-up step from every task that needs it.
- Give states that several steps share ONE name, so that the step producing it \
and the steps requiring it connect.
- When the source gives SEVERAL WAYS to reach the same states - a real-robot launch \
and a simulation launch, a light profile and a full one - make them ONE primitive \
and put each variant's command in its narration. A plan runs one bring-up, never \
several; separate primitives for each variant get mixed together.
- A check that only confirms an earlier step worked - `verify_...`, `check_...`, \
`measure_...` - belongs in the narration of the step it checks, not as a primitive \
of its own, unless a user would ask for that check by itself.
- Aim for the grain of a task a user asks for: one primitive per thing the robot \
does, not per command typed.
- `skill` names the whole system, e.g. `operate_<robot>`.
"""

REPROMPT_SUFFIX = (
    "\n\nYour previous reply could not be used. Return ONLY the JSON object of the "
    "shape given above - no prose, no code fences - with every name in snake_case "
    "and every primitive's `requires` and `produces` filled in."
)


def build_system_prompt(manual: bool = False, code: bool = False, catalog: bool = False) -> str:
    """The extraction prompt, with the reference-rulebook rules when `catalog`."""
    prompt = _register_prompt(manual=manual, code=code)
    return prompt + CATALOG_SUFFIX if catalog else prompt


def _register_prompt(manual: bool = False, code: bool = False) -> str:
    """The extraction prompt for the kind of source in hand.

    Three registers, pulling in different directions, so each gets its own prompt
    and its own worked example. Video: preconditions are absent and must be
    inferred. Manual: preconditions are on the page and inventing one contradicts
    a document that was deliberately specific. Code: the preconditions are
    inferrable but the *interface names* are not - a handle that is guessed is a
    call that fails, so those must be copied verbatim or omitted.
    """
    if code:
        return (
            CODE_SYSTEM_PROMPT
            + json.dumps(CODE_SCHEMA, indent=2)
            + "\n\nHere is one complete worked example, for a mobile base. Note that "
            "every primitive carries the exact handle a program calls, and that the "
            "handles are copied rather than invented:\n\n"
            + json.dumps(CODE_WORKED_EXAMPLE, indent=2)
        )
    if manual:
        return (
            MANUAL_SYSTEM_PROMPT
            + json.dumps(INTERMEDIATE_SCHEMA, indent=2)
            + "\n\nHere is one complete worked example, from the operating manual for a "
            "benchtop centrifuge. Note that `start_cycle` requires `lid_latched` because "
            "the manual says so - it is read off the page, not guessed:\n\n"
            + json.dumps(MANUAL_WORKED_EXAMPLE, indent=2)
        )
    return (
        SYSTEM_PROMPT
        + json.dumps(INTERMEDIATE_SCHEMA, indent=2)
        + "\n\nHere is one complete worked example, from a rulebook for masala chai. "
        "Note that `add_water` requires `pan_on_stove` even though no narrator would "
        "ever say so - that is the inference you are being asked to make:\n\n"
        + json.dumps(WORKED_EXAMPLE, indent=2)
    )


def build_user_message(
    transcript: Transcript, manual: bool = False, code: bool = False, catalog: bool = False
) -> str:
    if catalog:
        return (
            "Reconstruct ONE reference rulebook covering every operation this source "
            "describes"
            + (", each with the interface a program calls to run it" if code else "")
            + ".\n\n"
            f"SOURCE ({transcript.words} words):\n{transcript.text}"
        )
    if code:
        return (
            "Reconstruct an executable rulebook for the task this source exposes. "
            "Every primitive must carry the interface a program calls to run it, "
            "copied exactly as written below.\n\n"
            f"SOURCE ({transcript.words} words):\n{transcript.text}"
        )
    if manual:
        return (
            "Reconstruct the rulebook for the procedure described in this "
            "documentation.\n\n"
            f"DOCUMENTATION ({transcript.words} words):\n{transcript.text}"
        )
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
    body = _FENCE.sub("", text.strip())
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        if error.pos >= len(body) or error.msg.startswith("Unterminated string"):
            # The parser ran out of input: the reply was cut off, not malformed.
            raise ExtractionFailed(
                f"the reply stops mid-JSON after {len(body)} characters - it was most "
                "likely cut off at the model's output limit (raise LLM_MAX_TOKENS, or "
                "RULEBOOK_REFERENCE_MAX_TOKENS for a split run)"
            ) from None
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
    manual: bool | None = None,
    code: bool | None = None,
    catalog: bool = False,
) -> Extraction:
    """One schema-constrained pass, cached by transcript hash (FR-3, FR-6).

    `manual` selects the documentation prompt instead of the transcript one; left
    as None it follows the transcript's own source. It is part of the cache key
    because the two prompts give different rulebooks for identical input text, and
    a cache that ignored the mode would serve one for the other.
    """
    code = (transcript.source == "code") if code is None else code
    manual = (transcript.source == "manual") if manual is None else manual
    register = "code" if code else "manual" if manual else "video"
    if catalog:
        register += "+catalog"
    system = build_system_prompt(manual=manual, code=code, catalog=catalog)
    # The prompt is part of the key: a changed prompt must not be served the
    # reply an earlier prompt got for the same text.
    prompt_digest = hashlib.sha256(system.encode("utf-8")).hexdigest()[:12]
    key = f"{model}|{register}|{prompt_digest}|{transcript.digest}"
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

    user = build_user_message(transcript, manual=manual, code=code, catalog=catalog)
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
