"""Entry point for Layer 2 — turn a command into a plan.

Read-only over the PlanGraph (FR-8): this command connects, traverses and reads,
and has no code path that writes.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import IO, Any, Sequence

from kg_read_harness.client import connect
from kg_read_harness.errors import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_UNEXPECTED,
    HarnessError,
)

from . import __version__
from .config import load_planner_config
from .order import CyclicPlan
from .pipeline import plan_command
from .plan import Clarification
from .report import dump_json, print_clarification, print_plan
from .traverse import IncompletePlanGraph

#: A clarification is a valid outcome, not a crash — but it is not a plan, so it
#: gets its own exit code for scripting.
EXIT_CLARIFICATION = 3
EXIT_INCOMPLETE_GRAPH = 4
EXIT_CYCLE = 5

EPILOG = """\
examples:
  python plan_command.py                          # interactive session
  python plan_command.py "make me a chai"
  python plan_command.py "fold my t-shirt" --format json > plan.json
  python plan_command.py "do the thing" --no-llm

configuration (environment variables):
  ARANGO_* / PROJECT_NAME    as Station 1 (see read_kg.py --help)
  PLANGRAPH_PREFIX           optional  default PROJECT_NAME
  PLANNER_THRESHOLD          optional  default 0.35  match score to accept a goal
  PLANNER_TIE_MARGIN         optional  default 0.05  closer than this = ambiguous
  PLANNER_TOP_K              optional  default 3     candidates shown on clarify
  PLANNER_MODEL              optional  overrides LLM_MODEL for phrasing only
  LLM_API_KEY / OPENROUTER_API_KEY   optional; without one, phrasing is templated

Step order is derived from the graph and is identical with or without the LLM.
The model only writes the `description` field; --no-llm proves it.

exit codes: 0 plan, 3 clarification needed, 4 incomplete PlanGraph, 5 cycle.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grasp-plan",
        description=(
            "Resolve a natural-language command to a skill in the PlanGraph, traverse "
            "its dependency subgraph, and emit an ordered plan.json."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "command",
        nargs="*",
        help="the natural-language command; omit it to open an interactive session",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="open an interactive session (the default when no command is given)",
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="skip the composer; templated phrasing over the same graph-derived order",
    )
    parser.add_argument(
        "--threshold", type=float, metavar="X", help="override PLANNER_THRESHOLD"
    )
    parser.add_argument("--version", action="version", version=f"grasp-plan {__version__}")
    return parser


def run(
    command: str,
    output_format: str = "table",
    use_llm: bool = True,
    threshold: float | None = None,
    stdout: IO[str] | None = None,
    db: Any = None,
    provider: Any = None,
    retriever: Any = None,
) -> int:
    stdout = stdout if stdout is not None else sys.stdout

    config = load_planner_config(use_llm=use_llm)
    if threshold is not None:
        import dataclasses

        config = dataclasses.replace(config, threshold=threshold)

    handle = connect(config.arango) if db is None else db
    result = plan_command(command, handle, config, provider=provider, retriever=retriever)

    if output_format == "json":
        dump_json(result, stdout)
    elif isinstance(result, Clarification):
        print_clarification(result, stdout)
    else:
        print_plan(config, result, stdout)

    return EXIT_CLARIFICATION if isinstance(result, Clarification) else EXIT_OK


def run_interactive(
    use_llm: bool = True,
    threshold: float | None = None,
    stdout: IO[str] | None = None,
    stdin: IO[str] | None = None,
    db: Any = None,
    provider: Any = None,
) -> int:
    """Connect once and answer questions until end of input."""
    import dataclasses

    from .shell import Shell, interact

    config = load_planner_config(use_llm=use_llm)
    if threshold is not None:
        config = dataclasses.replace(config, threshold=threshold)
    handle = connect(config.arango) if db is None else db
    return interact(Shell(handle, config, stdout=stdout, provider=provider), stdin=stdin)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.interactive or not args.command:
            return run_interactive(
                use_llm=not args.no_llm, threshold=args.threshold
            )
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
    except json.JSONDecodeError as error:
        print(f"error: invalid JSON ({error}).", file=sys.stderr)
        return EXIT_UNEXPECTED
    except KeyboardInterrupt:
        print("interrupted; Layer 2 is read-only and wrote nothing.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as error:
        print(f"unexpected error: {error.__class__.__name__}: {error}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
