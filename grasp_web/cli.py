"""Entry point for the planning UI."""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from typing import Mapping, Sequence

from kg_read_harness.client import connect
from kg_read_harness.errors import EXIT_INTERRUPTED, EXIT_OK, EXIT_UNEXPECTED, HarnessError
from layer2_planning.config import load_planner_config

from . import __version__
from .api import PlannerRegistry, PlannerService
from .builder import BuildService
from .server import make_server

EPILOG = """\
examples:
  python serve_grasp.py                    # http://127.0.0.1:8080
  python serve_grasp.py --project px4Planner
  python serve_grasp.py --port 9000 --open
  python serve_grasp.py --no-llm           # templated phrasing; same step order

Reads the same environment as the CLI (ARANGO_*, PROJECT_NAME, PLANGRAPH_PREFIX,
PLANNER_*, and an LLM key if you want model-written step wording).

--project serves a different PlanGraph without editing the environment: it names
the {project}_PlanGraph to plan over, so one checkout can serve the drone graph
and the kitchen one from two terminals.

The server is read-only over the PlanGraph and binds to localhost unless --host
says otherwise.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="grasp-web",
        description="Serve the planning UI: ask in plain English, get an ordered plan.",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--project",
        help="serve {PROJECT}_PlanGraph instead of the configured project",
    )
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1")
    parser.add_argument("--port", type=int, default=8080, help="default 8080")
    parser.add_argument("--no-llm", action="store_true", help="templated step wording")
    parser.add_argument("--open", action="store_true", help="open a browser once serving")
    parser.add_argument("--quiet", action="store_true", help="do not log requests")
    parser.add_argument("--version", action="version", version=f"grasp-web {__version__}")
    return parser


def serving_env(project: str | None, env: Mapping[str, str] | None = None) -> Mapping[str, str]:
    """The environment to plan against, with --project applied.

    The prefix normally falls back to the project name, but PLANGRAPH_PREFIX
    overrides it when set - so naming a project has to override both, or the
    flag would be quietly ignored on a machine that happens to set the prefix.
    """
    env = os.environ if env is None else env
    if not project:
        return env
    return {**env, "PROJECT_NAME": project, "PLANGRAPH_PREFIX": project}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_planner_config(serving_env(args.project), use_llm=not args.no_llm)
        db = connect(config.arango)
        service = PlannerService(db, config)
        health = service.health()

        # One connection, shared three ways: `service` is the default planner
        # (and what --project still selects), `registry` makes one per project
        # chosen in the browser, and `builder` lists and builds them.
        registry = PlannerRegistry(db, config)
        builder = BuildService(connect=lambda: db)

        server = make_server(
            service, args.host, args.port, quiet=args.quiet,
            builder=builder, registry=registry,
        )
        url = f"http://{args.host}:{args.port}/"
        print("G.R.A.S.P — planning UI")
        print(f"  database   {health['database']}")
        print(f"  graph      {health['graph']}")
        print(f"  skills     {health['skills']}")
        print(f"  retrieval  {health['retrieval']}"
              + ("" if health["vector_index"] else "  (no vector index yet)"))
        print(f"  phrasing   {health['phrasing']}"
              + (f" ({health['model']})" if health["model"] else ""))

        listing = builder.projects()
        rows = listing.get("projects", [])
        if rows:
            print(f"\n  projects in {listing['database']}:")
            for row in rows:
                state = (
                    "plannable" if row["plannable"]
                    else "buildable" if row["buildable"]
                    else row["stage"]
                )
                print(f"    {row['name']:<28} {state}")
        print(f"\n  serving {url}   (ctrl-c to stop)")
        print(f"  start at {url}projects\n")

        if rows and not listing.get("writes", False):
            print("  No write credentials, so no project can be built from the UI.")
            print("  Set ARANGO_WRITE_USERNAME / ARANGO_WRITE_PASSWORD to enable it.\n")
        elif health["skills"] == 0 and not rows:
            print("  This PlanGraph holds no skills yet. Run the bridge first:")
            print("      python write_plangraph.py --write --all-skills\n")

        if args.open:
            webbrowser.open(url)
        server.serve_forever()
        return EXIT_OK
    except HarnessError as error:
        print(error.render(), file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("\nstopped; nothing was written.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except OSError as error:
        print(f"could not serve on {args.host}:{args.port} ({error.strerror}).", file=sys.stderr)
        print("  likely fix: another process is on that port; try --port 8081.", file=sys.stderr)
        return EXIT_UNEXPECTED
    except Exception as error:
        print(f"unexpected error: {error.__class__.__name__}: {error}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
