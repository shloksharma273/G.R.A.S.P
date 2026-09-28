"""Entry point for task decomposition — a compound command into one run.

Read-only over the PlanGraph, like Layer 2.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
from typing import IO, Any, Sequence

from kg_read_harness.client import connect
from kg_read_harness.errors import EXIT_INTERRUPTED, EXIT_OK, EXIT_UNEXPECTED, HarnessError
from layer2_planning.cli import EXIT_CLARIFICATION, EXIT_CYCLE, EXIT_INCOMPLETE_GRAPH
from layer2_planning.config import load_planner_config
from layer2_planning.order import CyclicPlan
from layer2_planning.plan import Clarification
from layer2_planning.traverse import IncompletePlanGraph

from . import __version__
from .compound import CompoundPlan, plan_compound
from .report import dump_json, print_result

EPILOG = """\
examples:
  python plan_task.py "go to 2,1 and then charge the robot"
  python plan_task.py "back up 0.3 m, rotate 90 degrees, then dock" --format json
  python plan_task.py "go to location then dock at charger" --no-llm

With an LLM configured (LLM_API_KEY / OPENROUTER_API_KEY), the model splits the
command, picks a skill for each part from the PlanGraph's catalog, and copies out
the values you stated. Without one, the command is cut on "then" / "and" / ";"
and each part is matched the way plan_command.py matches a command.

exit codes: 0 plan, 3 a task needs clarifying, 4 incomplete PlanGraph, 5 cycle.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grasp-plan-task",
        description="Split a compound command into tasks and plan them as one run.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", nargs="+", help="the natural-language command")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument(
        "--no-llm", action="store_true",
        help="split on connectors and phrase from templates; no model calls",
    )
    parser.add_argument("--threshold", type=float, metavar="X", help="override PLANNER_THRESHOLD")
    parser.add_argument("--version", action="version", version=f"grasp-plan-task {__version__}")
    return parser


def run(
    command: str,
    output_format: str = "table",
    use_llm: bool = True,
    threshold: float | None = None,
    stdout: IO[str] | None = None,
    db: Any = None,
    provider: Any = None,
) -> int:
    stdout = stdout if stdout is not None else sys.stdout
    config = load_planner_config(use_llm=use_llm)
    if threshold is not None:
        config = dataclasses.replace(config, threshold=threshold)

    handle = connect(config.arango) if db is None else db
    result = plan_compound(command, handle, config, provider=provider)

    if output_format == "json":
        dump_json(result, stdout)
    else:
        print_result(config, result, stdout)

    if isinstance(result, Clarification):
        return EXIT_CLARIFICATION
    if isinstance(result, CompoundPlan) and not result.executable:
        return EXIT_CLARIFICATION
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(
            command=" ".join(args.command),
            output_format=args.format,
            use_llm=not args.no_llm,
            threshold=args.threshold,
        )
    except IncompletePlanGraph as error:
        print(f"incomplete PlanGraph: {error}", file=sys.stderr)
        return EXIT_INCOMPLETE_GRAPH
    except CyclicPlan as error:
        print(f"cycle in the plan: {error}", file=sys.stderr)
        return EXIT_CYCLE
    except HarnessError as error:
        print(error.render(), file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("interrupted; this is read-only and wrote nothing.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as error:
        print(f"unexpected error: {error.__class__.__name__}: {error}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
