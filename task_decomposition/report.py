"""Terminal rendering of a compound plan."""

from __future__ import annotations

import json
import sys
from typing import IO

from kg_read_harness.output import glyphs
from layer2_planning.plan import Clarification, Plan
from layer2_planning.report import RULE_WIDTH, print_clarification, print_plan

from .compound import CompoundPlan


def _values(parameters: dict) -> str:
    return ", ".join(f"{key}={value}" for key, value in parameters.items())


def print_compound(plan: CompoundPlan, stream: IO[str] | None = None) -> None:
    stream = stream if stream is not None else sys.stdout
    g = glyphs(stream)
    decomposition = plan.decomposition

    print(f"Compound plan {g['em']} {len(plan.subtasks)} task(s)", file=stream)
    print("=" * RULE_WIDTH, file=stream)
    print(f'  command            "{plan.command}"', file=stream)
    print(f"  split by           {decomposition.method}"
          + (f" ({decomposition.model})" if decomposition.method == "llm" and decomposition.model else "")
          + (f" {g['em']} {decomposition.fallback_reason}" if decomposition.fallback_reason else ""),
          file=stream)
    print(f"  executable         {'yes' if plan.executable else 'no, a task needs clarifying'}",
          file=stream)
    print("=" * RULE_WIDTH, file=stream)

    for result in plan.subtasks:
        sub = result.subtask
        print("", file=stream)
        header = f"  Task {result.index}: \"{sub.command}\""
        if result.planned:
            header += f" {g['turn']} {result.result.goal}"
        if sub.parameters:
            header += f"  [{_values(sub.parameters)}]"
        print(header, file=stream)

        if not result.planned:
            print(f"      needs clarifying: {result.result.reason}", file=stream)
            continue
        for skip in (s for s in plan.skipped if s["subtask"] == result.index):
            print(f"      skip {skip['action']} ({skip['reason']})", file=stream)
        for step in (s for s in plan.steps if s["subtask"] == result.index):
            print(f"  {step['order']:>4}. {step['action']}", file=stream)
            print(f"        {step['description']}", file=stream)
            if step.get("interface"):
                call = step["interface"]
                print(f"        {g['turn']} {call['kind'] + ':':<10}{call['name']}"
                      + (f" ({call['type']})" if call.get("type") else ""), file=stream)
            if step.get("parameters"):
                print(f"        {g['turn']} {'with:':<10}{_values(step['parameters'])}", file=stream)

    print("", file=stream)
    print(f"  {len(plan.steps)} step(s) to run; {len(plan.skipped)} repeated bring-up step(s) "
          "left out.", file=stream)
    stream.flush()


def print_result(config, result, stream: IO[str] | None = None) -> None:
    """Whichever shape came back: a compound plan, a plan, or a clarification."""
    if isinstance(result, CompoundPlan):
        print_compound(result, stream)
    elif isinstance(result, Clarification):
        print_clarification(result, stream)
    else:
        assert isinstance(result, Plan)
        print_plan(config, result, stream)
        parameters = result.meta.get("parameters")
        if parameters:
            print(f"  with: {_values(parameters)}", file=stream if stream is not None else sys.stdout)


def dump_json(result, stream: IO[str] | None = None) -> None:
    stream = stream if stream is not None else sys.stdout
    json.dump(result.to_dict(), stream, indent=2)
    stream.write("\n")
    stream.flush()
