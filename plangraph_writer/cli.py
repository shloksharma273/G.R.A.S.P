"""Entry point for Station 5 — the only command in this project that writes.

Because it writes, and because a scoped rebuild deletes, the default is
deliberately cautious: `--dry-run` plans the entire write, reads back what would
be purged, and touches nothing. `--write` is required to actually apply it.

By default it runs the whole bridge first (Stations 1-4) and writes what they
produce; `--from-json` takes Station 4's output instead.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import IO, Any, Sequence

from kg_read_harness.client import connect
from kg_read_harness.errors import (
    EXIT_INTERRUPTED,
    EXIT_UNEXPECTED,
    ConfigError,
    HarnessError,
)

from . import __version__
from .build import build
from .config import load_writer_config
from .partition import partition_by_skill, skills_in
from .records import PlanGraphBuild, WriteReport
from .report import dump_json, exit_code_for, print_report
from .writer import write_plangraph

EPILOG = """\
input:
  (default)      run Stations 1-4 first, then write what they produce.
  --from-json    Station 4's --format json output ('-' for stdin):

                     python normalize_kg.py --format json > s4.json
                     python write_plangraph.py --from-json s4.json          # dry run
                     python write_plangraph.py --from-json s4.json --write  # apply

safety:
  Station 5 is the only part of the bridge that mutates the database, and a
  scoped rebuild DELETES the task's existing subgraph before rewriting it. So a
  plain run is a DRY RUN: it plans everything, reports what would be purged and
  written, and touches nothing. Pass --write to apply.

  Writes are confined to the PlanGraph's own collections by an allowlist. Any
  attempt to touch AutoGraph's collections - or anything else in the database -
  raises rather than proceeding.

configuration (environment variables):
  ARANGO_* / PROJECT_NAME       as Station 1 (see read_kg.py --help)
  ARANGO_WRITE_USERNAME         optional  a writing user, if distinct from Station 1's
  ARANGO_WRITE_PASSWORD         optional  required with the above
  PLANGRAPH_PREFIX              optional  default PROJECT_NAME
  SKILL_SCOPE                   optional  default: inferred from the input's skill
  PLANGRAPH_BUILD_ID            optional  default: a hash of the input
  PLANGRAPH_EMBEDDING_FIELD     optional  default 'description'
  AUTOGRAPH_URL                 optional  embed-field endpoint; unset = index not built
  AUTOGRAPH_API_KEY             optional  bearer token for the above
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plangraph-writer",
        description=(
            "Write Station 4's finalized and derived edges into the PlanGraph: typed "
            "vertices, typed edges, a named graph, and the Skills vector index."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--from-json", metavar="PATH", help="Station 4 JSON ('-' for stdin)")
    parser.add_argument(
        "--write",
        action="store_true",
        help="actually write. Without this the run is a dry run and nothing is touched.",
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--no-listing", action="store_true", help="summary only (table format)")
    parser.add_argument(
        "--scope", metavar="NAME", help="skill scope; overrides SKILL_SCOPE and inference"
    )
    parser.add_argument(
        "--all-skills",
        action="store_true",
        help=(
            "write every skill in the input as its own scope. Use when one AutoGraph "
            "project holds several rulebooks."
        ),
    )
    parser.add_argument("--version", action="version", version=f"plangraph-writer {__version__}")
    return parser


def _read_json(source: str, stdin: IO[str] | None = None) -> Any:
    if source == "-":
        return json.load(stdin if stdin is not None else sys.stdin)
    with open(source, encoding="utf-8") as handle:
        return json.load(handle)


def station4_streams(payload: Any) -> tuple[list[Any], list[Any]]:
    """The finalized and derived arrays of a Station 4 result."""
    if not isinstance(payload, dict) or "finalized" not in payload:
        raise ConfigError(
            "the input must be a Station 4 result with 'finalized' and 'derived' arrays.",
            "produce it with: python normalize_kg.py --format json > s4.json",
        )
    return list(payload.get("finalized") or []), list(payload.get("derived") or [])


def load_streams(from_json: str | None, stdin: IO[str] | None = None) -> tuple[list[Any], list[Any]]:
    if from_json:
        return station4_streams(_read_json(from_json, stdin))

    from direction_normalizer import normalize
    from direction_normalizer.cli import load_stamped

    result = normalize(load_stamped(None, stdin))
    return list(result.finalized), list(result.derived)


def run(
    from_json: str | None = None,
    write: bool = False,
    output_format: str = "table",
    listing: bool = True,
    scope: str | None = None,
    all_skills: bool = False,
    stdout: IO[str] | None = None,
    stdin: IO[str] | None = None,
    db: Any = None,
    streams: tuple[list[Any], list[Any]] | None = None,
) -> int:
    stdout = stdout if stdout is not None else sys.stdout

    config = load_writer_config(dry_run=not write)
    finalized, derived = streams if streams is not None else load_streams(from_json, stdin)
    handle = connect(config.arango) if db is None else db

    if all_skills:
        return _write_every_skill(finalized, derived, handle, config, output_format, listing, stdout)

    plan: PlanGraphBuild = build(
        finalized,
        derived,
        config.schema,
        skill_scope=scope or config.skill_scope,
        build_id=config.build_id,
    )
    report: WriteReport = write_plangraph(plan, handle, config)

    if output_format == "json":
        dump_json(plan, report, stdout)
    else:
        print_report(config, plan, report, stdout, listing=listing)
    return exit_code_for(report)


def _write_every_skill(finalized, derived, handle, config, output_format, listing, stdout) -> int:
    """One scoped write per skill, from a project holding several rulebooks."""
    partitions = partition_by_skill(finalized, derived)
    if not partitions:
        raise ConfigError(
            "no skill in the input decomposes into any primitive, so there is "
            "nothing to write.",
            "check that AutoGraph extracted SKILL -> PRIMITIVE relationships; a skill "
            "with no decomposition cannot be planned.",
        )

    results = []
    worst = 0
    for partition in partitions:
        plan = build(
            partition.finalized,
            partition.derived,
            config.schema,
            skill_scope=partition.scope,
            build_id=config.build_id,
        )
        report = write_plangraph(plan, handle, config)
        results.append((partition, plan, report))
        worst = max(worst, exit_code_for(report))

    if output_format == "json":
        json.dump(
            {
                "skills": [
                    {"skill": p.skill, "scope": p.scope, "plan": b.to_dict(), "write": r.to_dict()}
                    for p, b, r in results
                ]
            },
            stdout,
            indent=2,
            ensure_ascii=False,
        )
        stdout.write("\n")
    else:
        for index, (partition, plan, report) in enumerate(results):
            if index:
                print("", file=stdout)
            print(f"### {partition.skill}  (scope: {partition.scope})", file=stdout)
            print_report(config, plan, report, stdout, listing=listing)
    return worst


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(
            from_json=args.from_json,
            write=args.write,
            output_format=args.format,
            listing=not args.no_listing,
            scope=args.scope,
            all_skills=args.all_skills,
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
        print(
            "interrupted. A scoped rebuild is transactional where supported, so the "
            "previous subgraph is intact; re-run to complete it.",
            file=sys.stderr,
        )
        return EXIT_INTERRUPTED
    except Exception as error:
        print(f"unexpected error: {error.__class__.__name__}: {error}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
