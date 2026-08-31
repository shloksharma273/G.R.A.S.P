"""Entry point for Station 4.

By default it runs the whole bridge: Station 1 reads the KG, Station 2 classifies,
Station 3 disambiguates, and Station 4 normalizes direction and derives the
ordering. `--from-json` takes the stamped edges of Stations 2 and 3 from files
instead, so the seam can be exercised without a database or an API key.

All I/O lives here. `normalizer.normalize()` is pure graph logic.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import IO, Any, Iterable, Sequence

from kg_read_harness.errors import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_UNEXPECTED,
    ConfigError,
    HarnessError,
)

from . import __version__
from .adapt import stamped_edges_from_json
from .normalizer import normalize
from .report import dump_json, print_report

EPILOG = """\
input:
  (default)      run Stations 1, 2 and 3 first, then normalize what they stamp.
                 Needs the Station 1 environment variables and an LLM key.
  --from-json    one or more station JSON results ('-' for stdin). Pass Station 2's
                 and Station 3's together; their 'stamped' arrays are concatenated:

                     python classify_kg.py --format json > s2.json
                     python disambiguate_kg.py --from-json s2.json --format json > s3.json
                     python normalize_kg.py --from-json s2.json s3.json

  Station 2's stamped array carries decomposes_to / uses / precedes; Station 3's
  carries requires / produces. Chaining needs the requires/produces half, so
  passing Station 2's alone derives no ordering.

output:
  --format table   the derived ordering, the finalized edges, the summary, and a
                   topological sort of the ordering graph (default)
  --format json    finalized + derived + parked + the topological order

Station 4 is deterministic: no LLM, no randomness, no graph write.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="direction-normalizer",
        description=(
            "Fix every stamped edge to its canonical direction, then derive precedes "
            "ordering between primitives by state chaining."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--from-json",
        metavar="PATH",
        nargs="+",
        help="one or more station JSON results ('-' for stdin)",
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--no-listing", action="store_true", help="summary only (table format)")
    parser.add_argument(
        "--no-plan", action="store_true", help="skip the topological order (table format)"
    )
    parser.add_argument("--version", action="version", version=f"direction-normalizer {__version__}")
    return parser


def _read_json(source: str, stdin: IO[str] | None = None) -> Any:
    if source == "-":
        return json.load(stdin if stdin is not None else sys.stdin)
    with open(source, encoding="utf-8") as handle:
        return json.load(handle)


def load_stamped(sources: Sequence[str] | None, stdin: IO[str] | None = None) -> list[Any]:
    """Stamped edges from JSON files, or from a live Stations 1-3 run."""
    if sources:
        edges: list[Any] = []
        for source in sources:
            try:
                edges.extend(stamped_edges_from_json(_read_json(source, stdin)))
            except ValueError as error:
                raise ConfigError(
                    f"{source}: {error}.",
                    "pass the --format json output of classify_kg.py and/or "
                    "disambiguate_kg.py.",
                ) from None
        return edges

    from llm_disambiguator import disambiguate, load_llm_config
    from llm_disambiguator.cache import VerdictCache
    from rule_preclassifier import classify
    from rule_preclassifier.cli import load_bundles

    llm_config = load_llm_config()
    classified = classify(load_bundles(None, stdin))
    resolved = disambiguate(
        classified.deferred,
        llm_config,
        cache=VerdictCache(llm_config.cache_path, llm_config.cache_enabled),
    )
    return list(classified.stamped) + list(resolved.stamped)


def run(
    from_json: Sequence[str] | None = None,
    output_format: str = "table",
    listing: bool = True,
    plan: bool = True,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    stdin: IO[str] | None = None,
    edges: Iterable[Any] | None = None,
) -> int:
    stdout = stdout if stdout is not None else sys.stdout

    items = list(edges) if edges is not None else load_stamped(from_json, stdin)
    result = normalize(items)

    if output_format == "json":
        dump_json(result, stdout)
    else:
        print_report(result, stdout, listing=listing, plan=plan)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(
            from_json=args.from_json,
            output_format=args.format,
            listing=not args.no_listing,
            plan=not args.no_plan,
        )
    except HarnessError as error:
        print(error.render(), file=sys.stderr)
        return error.exit_code
    except FileNotFoundError as error:
        print(f"error: {error.strerror}: {error.filename}", file=sys.stderr)
        return EXIT_UNEXPECTED
    except json.JSONDecodeError as error:
        print(f"error: input is not valid JSON ({error}).", file=sys.stderr)
        return EXIT_UNEXPECTED
    except KeyboardInterrupt:
        print("interrupted; Station 4 writes nothing.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as error:
        print(f"unexpected error: {error.__class__.__name__}: {error}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
