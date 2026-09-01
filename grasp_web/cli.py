"""Entry point for the planning UI."""

from __future__ import annotations

import argparse
import sys
import webbrowser
from typing import Sequence

from kg_read_harness.client import connect
from kg_read_harness.errors import EXIT_INTERRUPTED, EXIT_OK, EXIT_UNEXPECTED, HarnessError
from layer2_planning.config import load_planner_config

from . import __version__
from .api import PlannerService
from .server import make_server

EPILOG = """\
examples:
  python serve_grasp.py                    # http://127.0.0.1:8080
  python serve_grasp.py --port 9000 --open
  python serve_grasp.py --no-llm           # templated phrasing; same step order

Reads the same environment as the CLI (ARANGO_*, PROJECT_NAME, PLANGRAPH_PREFIX,
PLANNER_*, and an LLM key if you want model-written step wording).

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
    parser.add_argument("--host", default="127.0.0.1", help="default 127.0.0.1")
    parser.add_argument("--port", type=int, default=8080, help="default 8080")
    parser.add_argument("--no-llm", action="store_true", help="templated step wording")
    parser.add_argument("--open", action="store_true", help="open a browser once serving")
    parser.add_argument("--quiet", action="store_true", help="do not log requests")
    parser.add_argument("--version", action="version", version=f"grasp-web {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_planner_config(use_llm=not args.no_llm)
        service = PlannerService(connect(config.arango), config)
        health = service.health()

        server = make_server(service, args.host, args.port, quiet=args.quiet)
        url = f"http://{args.host}:{args.port}/"
        print("G.R.A.S.P — planning UI")
        print(f"  database   {health['database']}")
        print(f"  graph      {health['graph']}")
        print(f"  skills     {health['skills']}")
        print(f"  retrieval  {health['retrieval']}"
              + ("" if health["vector_index"] else "  (no vector index yet)"))
        print(f"  phrasing   {health['phrasing']}"
              + (f" ({health['model']})" if health["model"] else ""))
        print(f"\n  serving {url}   (ctrl-c to stop)\n")

        if health["skills"] == 0:
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
