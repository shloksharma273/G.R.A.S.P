"""A compound command, planned one subtask at a time and merged into one run.

Each subtask goes through Layer 2 unchanged, so every sub-plan keeps Layer 2's
guarantees: the steps come from the graph, the order from the state chain. What
this adds is the seam between them.

Every skill carries its own bring-up - "charge the robot" starts localization
just as "go to 2,1" does - so naively concatenating the plans would bring the
robot up twice. A later subtask's step is dropped when it is a *supporting* step
(something later in its own plan depends on it) that an earlier subtask already
ran. A subtask's *goal* steps - the ones nothing else in its plan needs - are
never dropped, because they are what the user asked for: "go to 1,0 then go to
2,1" navigates twice.

The graph models no negative effects - nothing says undocking makes "docked"
false - so a dropped step is assumed to still hold. That is the right call for
bring-up and the wrong one for a state a later task undoes; the dropped steps
are reported so a reader can see exactly what was assumed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from layer2_planning.config import PlannerConfig
from layer2_planning.pipeline import plan_command, plan_goal
from layer2_planning.plan import Clarification, Plan
from layer2_planning.retrieve import Candidate, LexicalRetriever, VectorRetriever, load_skills
from llm_disambiguator.provider import Provider

from .decompose import Decomposition, Subtask, decompose

#: How a goal chosen by the decomposer is recorded in the sub-plan's meta.
MATCH_METHOD_DECOMPOSER = "decomposer"


@dataclass
class SubtaskResult:
    index: int
    subtask: Subtask
    result: Plan | Clarification

    @property
    def planned(self) -> bool:
        return isinstance(self.result, Plan)

    def to_dict(self) -> dict[str, Any]:
        payload = self.result.to_dict()
        payload["kind"] = "plan" if self.planned else "clarification"
        return {"index": self.index, **self.subtask.to_dict(), "result": payload}


@dataclass
class CompoundPlan:
    command: str
    decomposition: Decomposition
    subtasks: list[SubtaskResult] = field(default_factory=list)
    #: The merged, executable run: one dict per step, in execution order.
    steps: list[dict[str, Any]] = field(default_factory=list)
    #: Supporting steps left out because an earlier subtask already ran them.
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @property
    def executable(self) -> bool:
        """Only when every subtask produced a plan; a half-understood command is not run."""
        return all(result.planned for result in self.subtasks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "executable": self.executable,
            "decomposition": self.decomposition.to_dict(),
            "subtasks": [s.to_dict() for s in self.subtasks],
            "steps": list(self.steps),
            "skipped": list(self.skipped),
        }


def goal_steps(plan: Plan) -> set[str]:
    """The steps nothing else in the plan depends on - what the subtask is *for*."""
    needed = {state for step in plan.steps for state in step.requires}
    return {step.action for step in plan.steps if not needed & set(step.produces)}


def merge(results: list[SubtaskResult]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Concatenate the sub-plans in the user's order, dropping repeated bring-up."""
    steps: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    ran: dict[str, int] = {}  # action -> the merged order that first ran it

    for result in results:
        if not result.planned:
            continue
        plan = result.result
        goals = goal_steps(plan)
        for step in plan.steps:
            is_goal = step.action in goals
            if not is_goal and step.action in ran:
                skipped.append(
                    {
                        "subtask": result.index,
                        "action": step.action,
                        "satisfied_by": ran[step.action],
                        "reason": f"already run as step {ran[step.action]}",
                    }
                )
                continue
            order = len(steps) + 1
            entry = step.to_dict()
            entry.update(
                order=order,
                subtask=result.index,
                goal=is_goal,
                # Values belong to the step that acts on them - "go to 2,1" puts
                # x and y on the navigation, not on starting localization.
                parameters=dict(result.subtask.parameters) if is_goal else {},
            )
            steps.append(entry)
            ran.setdefault(step.action, order)
    return steps, skipped


def plan_compound(
    command: str,
    db: Any,
    config: PlannerConfig,
    provider: Provider | None = None,
    retriever: Any = None,
    skills: list[dict[str, Any]] | None = None,
    now: str | None = None,
) -> Plan | Clarification | CompoundPlan:
    """Decompose a command, plan each part with Layer 2, merge the parts.

    A command that is one task comes back as Layer 2's own `Plan` or
    `Clarification` - the contract downstream already reads - with the
    decomposition recorded in the plan's meta.
    """
    if provider is None and config.use_llm and config.llm is not None:
        provider = Provider(config.llm)
    llm = provider if config.use_llm else None

    skills = load_skills(db, config.schema) if skills is None else skills
    if retriever is None:
        vector = VectorRetriever(db, config.schema)
        retriever = vector if vector.available else LexicalRetriever(skills)

    decomposition = decompose(
        command, skills, provider=llm, model=config.llm.model if config.llm else None
    )
    by_name = {skill["name"]: skill for skill in skills}

    results = []
    for index, subtask in enumerate(decomposition.subtasks, start=1):
        results.append(
            SubtaskResult(
                index=index,
                subtask=subtask,
                result=_plan_subtask(subtask, by_name, retriever, db, config, llm, now),
            )
        )

    if len(results) == 1:
        (only,) = results
        if only.planned:
            only.result.meta["decomposition"] = decomposition.to_dict()
            only.result.meta["parameters"] = dict(only.subtask.parameters)
        return only.result

    compound = CompoundPlan(command=command, decomposition=decomposition, subtasks=results)
    compound.steps, compound.skipped = merge(results)
    return compound


def _plan_subtask(subtask, by_name, retriever, db, config, provider, now):
    skill = by_name.get(subtask.skill) if subtask.skill else None
    if skill is None:
        # The rule splitter, or a model that found no fitting skill: let Layer 2's
        # own goal resolution decide, guardrail and all.
        return plan_command(
            subtask.command, db, config, provider=provider, retriever=retriever, now=now
        )

    # The model chose from the catalog; record how well the words alone would
    # have matched, so a plan never claims more confidence than it earned.
    try:
        ranked = retriever.score(subtask.command)
    except Exception:  # e.g. a vector index whose embedding endpoint is not wired up
        ranked = []
    lexical = next((c.score for c in ranked if c.skill == skill["name"]), 0.0)
    goal = Candidate(
        skill=skill["name"],
        key=skill["_key"],
        scope=skill.get("skill_scope", ""),
        score=lexical,
        description=skill.get("description", "") or "",
    )
    return plan_goal(
        subtask.command, goal, MATCH_METHOD_DECOMPOSER, db, config, provider=provider, now=now
    )


__all__ = ["CompoundPlan", "SubtaskResult", "goal_steps", "merge", "plan_compound"]
