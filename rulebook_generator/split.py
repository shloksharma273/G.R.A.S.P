"""One source, one rulebook per task.

Layer 2 resolves a command to one skill and plans every primitive in that skill's
scope. So a single rulebook covering a whole robot can only ever produce one
plan - "go to 2,1" and "dock" would both come back as the robot's entire
repertoire. The OpenAMRobot rulebooks in `generated/` were written the way that
works: one rulebook per task a user can ask for, each holding only the bring-up
steps that task needs.

This builds them the same way, in three passes:

    1. reference   the model reconstructs ONE rulebook of every operation
    2. tasks       the model picks the tasks a user would ask for, each named by
                   its goal step(s) in the reference
    3. slices      each task is cut out of the reference: its goal steps plus,
                   by following `requires` back to the step that produces each
                   state, everything they depend on

Only the first two passes are the model's. The slice is deterministic, so a task
rulebook can never gain a step the reference does not have, and every task shares
the reference's step names, states and execution handles exactly. Each slice is
rendered and put through the same validation gate as any other rulebook.

The model's task list is guarded the way the other LLM stages are: a goal that
is not a step in the reference is refused, and so is a skill that shares a name
with one of its own steps - the two collapse into one vertex and the step drops
out of the plan, which nothing downstream would notice. A reply that fails the
guard is reprompted once, then replaced by one task per final step.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from llm_disambiguator.provider import Provider, ServiceError

from .pipeline import GenerationResult, generate, verdict_reason
from .render import render
from .schema import ACCEPT, REJECT, Rulebook, sanitize_name
from .transcript import Transcript
from .validate import check_grounding, validate

STAGE_CHOOSING = "choosing the tasks"
STAGE_SLICING = "cutting one rulebook per task"

METHOD_LLM = "llm"
METHOD_RULES = "rules"

#: More than this is a catalog restated, not a set of tasks.
MAX_TASKS = 40

_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)

TASKS_PROMPT = """\
You are given a REFERENCE rulebook for one robot: every primitive action it can \
perform, with the states each one requires and produces. It will be cut into one \
rulebook per TASK - a thing a user would ask this robot to do in one sentence, \
like "go to 2, 1", "dock at the charger" or "back up 30 cm".

For each task, name its GOAL step(s): the primitive(s) that ARE the task. Do not \
list the steps that only prepare for it - starting drivers, localising, bringing \
up navigation. Those are added automatically by following each goal's \
preconditions back through the reference.

Rules:
1. `goals` are primitive names copied EXACTLY from the reference. Usually one.
2. `skill` is snake_case, verb first, and names the task - `go_to_location`, \
`dock_at_charger`. It must NOT be the name of any primitive in the reference: a \
skill and a step with one name collapse into one node and the step is lost. If \
the obvious name is taken by a step, choose another (`navigate_to_goal` is the \
step, `go_to_location` the task).
3. One task per thing a user would ask for. Do not make a task out of a step that \
only ever prepares for others, unless a user would plausibly ask for it alone \
(e.g. "test the motors").
4. `title` is human-readable and prefixed like the reference title, e.g. \
"OpenAMRobot Go To Location". `overview` is one or two sentences on what the task \
does.
5. Skill names are unique.
6. `assumes` lists states a user asking for this task ALREADY has true, copied \
exactly from the reference - undocking assumes `robot_docked`; loading a map \
assumes the map was saved earlier. Their producing steps are then not pulled in. \
Use [] when the task starts from nothing.
7. `via` names the step(s) the task should run to get there WHEN the reference \
offers several ways to reach the same state - e.g. the real-robot bring-up rather \
than the simulation one. Leave it [] when there is only one way; the rest is found \
by following preconditions.

Return a JSON object and nothing else:
{"tasks": [{"skill": "...", "title": "...", "overview": "...", "goals": ["..."], \
"assumes": [], "via": []}]}"""

REPROMPT = (
    "\n\nYour previous reply was rejected: {reason}. Return ONLY the JSON object, "
    "with goals copied exactly from the reference and no skill named after a step."
)


class TaskChoiceError(Exception):
    """The model's task list does not hold up against the reference."""


@dataclass
class Task:
    skill: str
    title: str
    overview: str
    goals: list[str]
    #: States already true when a user asks for this task. Not traced back to
    #: the step that produces them - undocking starts docked.
    assumes: list[str] = field(default_factory=list)
    #: Steps the task runs to get there, when the reference offers several ways -
    #: the real-robot bring-up rather than the simulation one.
    via: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


@dataclass
class TaskChoice:
    tasks: list[Task]
    method: str
    reprompted: bool = False
    fallback_reason: str = ""
    from_cache: bool = False


@dataclass
class SplitResult:
    """The reference rulebook and the task rulebooks cut from it."""

    reference: GenerationResult
    tasks: list[GenerationResult] = field(default_factory=list)
    choice: TaskChoice | None = None
    #: Why no task rulebook was cut, when none was.
    reason: str = ""

    @property
    def accepted(self) -> list[GenerationResult]:
        return [t for t in self.tasks if t.verdict == ACCEPT]

    def to_dict(self) -> dict[str, Any]:
        return {
            "reference": self.reference.to_dict(),
            "tasks": [t.to_dict() for t in self.tasks],
            "method": self.choice.method if self.choice else None,
            "reprompted": bool(self.choice and self.choice.reprompted),
            "fallback_reason": self.choice.fallback_reason if self.choice else "",
            "reason": self.reason,
        }


# --- pass 2: which tasks ------------------------------------------------------


def reference_lines(reference: Rulebook) -> list[str]:
    lines = [f"Reference: {reference.title} ({reference.skill})", ""]
    for primitive in reference.primitives:
        narration = " ".join(primitive.narration.split())
        if len(narration) > 180:
            narration = narration[:177] + "..."
        lines.append(
            f"- {primitive.name}: {narration} | requires: {', '.join(primitive.requires) or '-'}"
            f" | produces: {', '.join(primitive.produces) or '-'}"
        )
    return lines


def parse_tasks(text: str) -> list[dict[str, Any]]:
    if not text or not text.strip():
        raise TaskChoiceError("the reply was empty")
    try:
        payload = json.loads(_FENCE.sub("", text.strip()))
    except json.JSONDecodeError as error:
        raise TaskChoiceError(f"the reply is not valid JSON ({error})") from None
    items = payload.get("tasks") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise TaskChoiceError("expected an object with a 'tasks' array")
    return items


def check_tasks(items: list[Any], reference: Rulebook) -> list[Task]:
    """The guard: goals that exist, skills that are unique and not a step's name."""
    if not items:
        raise TaskChoiceError("no tasks were returned")
    if len(items) > MAX_TASKS:
        raise TaskChoiceError(f"{len(items)} tasks, more than the {MAX_TASKS} allowed")

    steps = {sanitize_name(p.name) for p in reference.primitives}
    tasks: list[Task] = []
    seen: set[str] = set()
    for position, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            raise TaskChoiceError(f"task {position} is not an object")
        skill = sanitize_name(str(item.get("skill") or ""))
        if not skill:
            raise TaskChoiceError(f"task {position} has no skill name")
        if skill in seen:
            raise TaskChoiceError(f"two tasks are named {skill!r}")
        if skill in steps:
            raise TaskChoiceError(
                f"task {skill!r} has the name of a step in the reference; a skill and a "
                "step with one name collapse into one node"
            )
        goals = [sanitize_name(str(g)) for g in item.get("goals") or [] if str(g).strip()]
        if not goals:
            raise TaskChoiceError(f"task {skill!r} names no goal step")
        unknown = [g for g in goals if g not in steps]
        if unknown:
            raise TaskChoiceError(
                f"task {skill!r} names goal(s) that are not steps in the reference: "
                + ", ".join(unknown)
            )
        via = [sanitize_name(str(v)) for v in item.get("via") or [] if str(v).strip()]
        unknown = [v for v in via if v not in steps]
        if unknown:
            raise TaskChoiceError(
                f"task {skill!r} goes via step(s) that are not in the reference: "
                + ", ".join(unknown)
            )
        states = {sanitize_name(x) for p in reference.primitives for x in (*p.requires, *p.produces)}
        assumes = [sanitize_name(str(a)) for a in item.get("assumes") or [] if str(a).strip()]
        unknown = [a for a in assumes if a not in states]
        if unknown:
            raise TaskChoiceError(
                f"task {skill!r} assumes state(s) the reference never mentions: "
                + ", ".join(unknown)
            )
        seen.add(skill)
        tasks.append(
            Task(
                skill=skill,
                title=str(item.get("title") or "").strip() or skill.replace("_", " ").title(),
                overview=str(item.get("overview") or "").strip(),
                goals=list(dict.fromkeys(goals)),
                assumes=list(dict.fromkeys(assumes)),
                via=list(dict.fromkeys(via)),
            )
        )
    return tasks


def tasks_by_rule(reference: Rulebook) -> list[Task]:
    """The fallback: one task per final step - one whose effects no other step needs.

    Deliberately plain. It knows nothing about which steps a user would ask for,
    so it can only offer the ends of the precondition chains.
    """
    needed = {state for p in reference.primitives for state in p.requires}
    steps = {p.name for p in reference.primitives}
    tasks = []
    for primitive in reference.primitives:
        if needed & set(primitive.produces):
            continue
        skill = f"{primitive.name}_task"
        while skill in steps:
            skill += "_task"
        tasks.append(
            Task(
                skill=skill,
                title=f"{reference.title} {primitive.name.replace('_', ' ').title()}".strip(),
                overview=primitive.narration,
                goals=[primitive.name],
            )
        )
    return tasks


def choose_tasks(
    reference: Rulebook,
    provider: Provider | None,
    model: str = "",
    cache: Any = None,
) -> TaskChoice:
    """Pass 2. Falls back to `tasks_by_rule` when the model is absent or fails twice."""
    if provider is None:
        return TaskChoice(tasks_by_rule(reference), METHOD_RULES, fallback_reason="no LLM configured")

    user = "\n".join(reference_lines(reference))
    digest = hashlib.sha256(user.encode("utf-8")).hexdigest()[:16]
    key = f"{model}|tasks|{digest}"
    if cache is not None:
        cached = cache.get(key)
        if cached is not None:
            try:
                return TaskChoice(check_tasks(cached, reference), METHOD_LLM, from_cache=True)
            except TaskChoiceError:
                pass  # an unusable cached reply behaves as a miss

    reason = ""
    for attempt in (0, 1):
        system = TASKS_PROMPT if attempt == 0 else TASKS_PROMPT + REPROMPT.format(reason=reason)
        try:
            items = parse_tasks(provider.complete(system, user))
            tasks = check_tasks(items, reference)
        except TaskChoiceError as error:
            reason = str(error)
            continue
        except ServiceError as error:
            return TaskChoice(
                tasks_by_rule(reference), METHOD_RULES, reprompted=attempt > 0,
                fallback_reason=f"the model was unavailable: {error}",
            )
        if cache is not None:
            cache.put(key, items)
        return TaskChoice(tasks, METHOD_LLM, reprompted=attempt > 0)

    return TaskChoice(
        tasks_by_rule(reference), METHOD_RULES, reprompted=True,
        fallback_reason=f"the guard rejected the task list twice: {reason}",
    )


# --- pass 3: the slice --------------------------------------------------------


def slice_rulebook(reference: Rulebook, task: Task) -> Rulebook:
    """A task's rulebook: its goals, its `via` steps, and what their preconditions need.

    Preconditions are resolved as a set cover, not one state at a time. A
    reference usually offers several ways to reach the same states - a real-robot
    bring-up and a simulation one, a light profile and a full one - and choosing
    each state's producer on its own mixes them: localisation from one launch,
    navigation from another. So a state already produced by a step in the slice is
    satisfied, and otherwise the step added is the one that produces the most of
    what is still missing, ties going to the earlier step in the reference (which
    lists bring-up before use). A task's `via` steps are in the slice from the
    start, which is how the task list names the variant it means.

    A state nothing produces is a starting condition and stays a bare
    precondition, as it is in the reference. A state the task `assumes` is neither
    chased nor kept as a precondition: the user asking to undock is standing at a
    docked robot, and a precondition no step in the rulebook can produce would only
    be flagged as unsatisfiable. The assumption is written into the overview
    instead, so it is not lost.
    """
    assumed = set(task.assumes)
    order = {p.name: index for index, p in enumerate(reference.primitives)}
    by_name = {p.name: p for p in reference.primitives}
    keep = [n for n in dict.fromkeys([*task.goals, *task.via]) if n in by_name]

    while True:
        produced = {state for name in keep for state in by_name[name].produces}
        missing = {
            state
            for name in keep
            for state in by_name[name].requires
            if state not in produced and state not in assumed
        }
        candidates = [
            p for p in reference.primitives
            if p.name not in keep and missing & set(p.produces)
        ]
        if not candidates:
            break  # what is still missing is a starting condition
        best = max(candidates, key=lambda p: (len(missing & set(p.produces)), -order[p.name]))
        keep.append(best.name)

    keep_set = set(keep)
    primitives = [
        dataclasses.replace(p, requires=[s for s in p.requires if s not in assumed])
        for p in reference.primitives
        if p.name in keep_set
    ]
    overview = task.overview
    if assumed:
        overview = (overview.rstrip(". ") + ". " if overview else "") + (
            "Assumes: " + ", ".join(s.replace("_", " ") for s in task.assumes) + "."
        )
    used_states = {s for p in primitives for s in (*p.requires, *p.produces)}
    used_objects = {o for p in primitives for o in p.uses}
    return Rulebook(
        skill=task.skill,
        title=task.title,
        overview=overview,
        objects=[o for o in reference.objects if o in used_objects]
        + sorted(used_objects - set(reference.objects)),
        states=[s for s in reference.states if s in used_states]
        + sorted(used_states - set(reference.states)),
        primitives=primitives,
        ordering=[],  # derived from the slice's own graph when rendered
        source_url=reference.source_url,
    )


def grade(rulebook: Rulebook, transcript: Transcript | None, check_ground: bool) -> GenerationResult:
    """Render one task rulebook and put it through the gate."""
    markdown = render(rulebook)
    report = validate(rulebook, markdown=markdown)
    if len(rulebook.primitives) == 1:
        # A one-step task has nothing to order. The gate's no_ordering check is
        # there to catch several steps with no dependencies between them.
        report.issues = [i for i in report.issues if i.code != "no_ordering"]
    if check_ground and transcript is not None:
        report.issues.extend(check_grounding(rulebook, transcript.text))
    report.settle()
    return GenerationResult(
        transcript=transcript,
        rulebook=rulebook,
        markdown=markdown,
        report=report,
        verdict=report.verdict,
        reason=verdict_reason(report),
    )


# --- all three --------------------------------------------------------------


def generate_split(
    transcript: Transcript,
    config: Any,
    provider: Provider | None = None,
    cache: Any = None,
    on_stage: Callable[[str], None] | None = None,
) -> SplitResult:
    """Transcript in, a reference rulebook and one graded rulebook per task out."""
    announce = on_stage if on_stage is not None else (lambda _stage: None)
    if provider is None:
        # The reference is the whole system in one reply; at a single task's
        # budget it is cut off mid-JSON.
        llm = dataclasses.replace(
            config.llm, max_tokens=max(config.llm.max_tokens, config.reference_max_tokens)
        )
        provider = Provider(llm)

    reference = generate(
        transcript, config, provider=provider, cache=cache, on_stage=on_stage, catalog=True
    )
    split = SplitResult(reference=reference)
    if reference.rulebook is None:
        split.reason = f"no reference rulebook, so no task could be cut: {reference.reason}"
        return split
    if reference.verdict == REJECT:
        # Still cut. Every task is gated again on its own, so a cycle or a broken
        # handle in one corner of the reference rejects only the tasks whose slice
        # reaches it - which is the difference between one bad step and none.
        split.reason = (
            "the reference rulebook was rejected (" + reference.reason + "); each task "
            "below is graded on its own, so only a task that includes the fault fails"
        )

    announce(STAGE_CHOOSING)
    try:
        split.choice = choose_tasks(reference.rulebook, provider, config.llm.model, cache)
    finally:
        if cache is not None:
            cache.save()

    announce(STAGE_SLICING)
    for task in split.choice.tasks:
        split.tasks.append(
            grade(
                slice_rulebook(reference.rulebook, task),
                reference.transcript,
                config.check_grounding,
            )
        )
    if not split.tasks:
        split.reason = "the reference holds no step that could be a task"
    return split


__all__ = [
    "STAGE_CHOOSING",
    "STAGE_SLICING",
    "SplitResult",
    "Task",
    "TaskChoice",
    "TaskChoiceError",
    "check_tasks",
    "choose_tasks",
    "generate_split",
    "grade",
    "slice_rulebook",
    "tasks_by_rule",
]
