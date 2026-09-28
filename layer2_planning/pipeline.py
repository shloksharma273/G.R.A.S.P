"""The four-stage planning pipeline (PRD Section 5).

    command -> goal resolution -> subgraph traversal -> topological sort -> plan.json

Retrieval is a small first move; the graph does the heavy lifting; the LLM only
finishes. Read-only throughout (FR-8): Layer 2 walks the PlanGraph and never
writes to it.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from llm_disambiguator.provider import Provider

from .compose import METHOD_TEMPLATE, compose
from .config import PlannerConfig
from .order import CyclicPlan, order_plan
from .plan import Clarification, Plan, build_plan
from .retrieve import Candidate, resolve_goal
from .traverse import IncompletePlanGraph, retrieve_subgraph


def plan_command(
    command: str,
    db: Any,
    config: PlannerConfig,
    provider: Provider | None = None,
    retriever: Any = None,
    now: str | None = None,
) -> Plan | Clarification:
    """Turn a natural-language command into a plan, or ask for clarification."""
    schema = config.schema

    # --- Stage 1: goal resolution ------------------------------------------
    resolution = resolve_goal(
        command,
        db,
        schema,
        threshold=config.threshold,
        top_k=config.top_k,
        tie_margin=config.tie_margin,
        retriever=retriever,
    )
    if not resolution.resolved:
        return Clarification(
            command=command,
            reason=resolution.reason,
            candidates=[c.to_dict() for c in resolution.candidates],
            ambiguous=resolution.ambiguous,
        )

    goal = resolution.goal
    assert goal is not None
    return plan_goal(command, goal, resolution.method, db, config, provider=provider, now=now)


def plan_goal(
    command: str,
    goal: Candidate,
    match_method: str,
    db: Any,
    config: PlannerConfig,
    provider: Provider | None = None,
    now: str | None = None,
) -> Plan:
    """Stages 2-4 for a goal that is already decided.

    `plan_command` reaches here through goal resolution. The task decomposer
    reaches here directly, having had the model pick the skill from the catalog -
    the stages that supply correctness and order are the same either way.
    """
    schema = config.schema

    # --- Stage 2: subgraph retrieval ---------------------------------------
    subgraph = retrieve_subgraph(
        db, schema, goal.key, goal.skill, goal.scope, depth=config.max_depth
    )

    # --- Stage 3: ordering --------------------------------------------------
    ordering = order_plan(subgraph)

    # --- Stage 4: composition (thin) ---------------------------------------
    if provider is None and config.use_llm and config.llm is not None:
        provider = Provider(config.llm)
    composition = compose(
        ordering.steps,
        subgraph,
        provider=provider if config.use_llm else None,
        model=config.llm.model if config.llm else None,
    )

    meta: dict[str, Any] = {
        "skill_scope": subgraph.skill_scope,
        "generated_at": now or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_id": composition.model if composition.method != METHOD_TEMPLATE else None,
        "match_confidence": round(goal.score, 4),
        "match_method": match_method,
        "composer": composition.method,
        "ordering": ordering.to_dict(),
    }
    if composition.reprompted:
        meta["composer_reprompted"] = True
    if composition.fallback_reason:
        meta["composer_fallback_reason"] = composition.fallback_reason

    orphans = subgraph.orphan_preconditions()
    if orphans:
        # Section 10: surface a warning; the plan may be incomplete.
        meta["orphan_preconditions"] = [list(pair) for pair in orphans]
        meta["warning"] = (
            f"{len(orphans)} precondition(s) are required by a step but produced by "
            "nothing in this task, so the plan may not be executable as written."
        )

    return build_plan(
        command=command,
        goal=goal.skill,
        steps=ordering.steps,
        descriptions=composition.descriptions,
        subgraph=subgraph,
        meta=meta,
    )


__all__ = ["plan_command", "plan_goal", "CyclicPlan", "IncompletePlanGraph"]
