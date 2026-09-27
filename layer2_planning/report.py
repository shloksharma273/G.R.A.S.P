"""Terminal rendering of a plan or a clarification."""

from __future__ import annotations

import json
import sys
from typing import IO

from kg_read_harness.output import glyphs

from .config import PlannerConfig
from .plan import Clarification, Plan

RULE_WIDTH = 72


def print_plan(config: PlannerConfig, plan: Plan, stream: IO[str] | None = None) -> None:
    stream = stream if stream is not None else sys.stdout
    g = glyphs(stream)

    print(f"Plan {g['em']} {plan.goal}", file=stream)
    print("=" * RULE_WIDTH, file=stream)
    print(f'  command            "{plan.command}"', file=stream)
    print(f"  goal               {plan.goal}", file=stream)
    print(f"  match              {plan.meta['match_confidence']:.2f} "
          f"({plan.meta['match_method']})", file=stream)
    print(f"  composer           {plan.meta['composer']}"
          + (f" ({plan.meta['model_id']})" if plan.meta.get("model_id") else ""), file=stream)
    ordering = plan.meta["ordering"]
    print(f"  ordering           {ordering['constraints']} constraint(s): "
          f"{ordering['from_precedes_edges']} from precedes edges, "
          f"{ordering['from_state_chain']} from the state chain", file=stream)
    print("=" * RULE_WIDTH, file=stream)
    print("", file=stream)

    for step in plan.steps:
        print(f"  {step.order:>2}. {step.action}", file=stream)
        print(f"      {step.description}", file=stream)
        if step.requires:
            print(f"      {g['turn']} requires: {', '.join(step.requires)}", file=stream)
        if step.produces:
            print(f"      {g['turn']} produces: {', '.join(step.produces)}", file=stream)
        if step.uses:
            print(f"      {g['turn']} uses:     {', '.join(step.uses)}", file=stream)
        if step.interface:
            call = step.interface["name"] + (f" ({step.interface['type']})" if step.interface.get("type") else "")
            print(f"      {g['turn']} {step.interface['kind'] + ':':<10}{call}", file=stream)

    if plan.meta.get("warning"):
        print("", file=stream)
        print(f"  warning: {plan.meta['warning']}", file=stream)
        for primitive, state in plan.meta.get("orphan_preconditions", []):
            print(f"    {primitive} requires {state}, which nothing produces", file=stream)

    print("", file=stream)
    print(f"  {len(plan.steps)} step(s); order is graph-derived, "
          f"only the wording is {'model-written' if plan.meta['composer'] == 'llm' else 'templated'}.",
          file=stream)
    stream.flush()


def print_clarification(
    clarification: Clarification, stream: IO[str] | None = None
) -> None:
    stream = stream if stream is not None else sys.stdout
    g = glyphs(stream)

    print(f"Clarification needed {g['em']} no plan was produced", file=stream)
    print("=" * RULE_WIDTH, file=stream)
    print(f'  command   "{clarification.command}"', file=stream)
    print("", file=stream)
    print(f"  {clarification.reason}", file=stream)
    if clarification.candidates:
        print("", file=stream)
        print("  closest skills:", file=stream)
        for candidate in clarification.candidates:
            print(f"    {candidate['score']:.2f}  {candidate['skill']}", file=stream)
    stream.flush()


def dump_json(result, stream: IO[str] | None = None) -> None:
    stream = stream if stream is not None else sys.stdout
    json.dump(result.to_dict(), stream, indent=2, ensure_ascii=False)
    stream.write("\n")
    stream.flush()
