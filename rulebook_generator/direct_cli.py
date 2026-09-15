"""Entry point for the direct ingest — a rulebook file into the PlanGraph.

Like Station 5, and for the same reason, a plain run is a **dry run**: a scoped
rebuild deletes a task's existing subgraph before rewriting it, so `--write` has
to be asked for.
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
    ConfigError,
    HarnessError,
)
from kg_read_harness.output import glyphs
from plangraph_writer.config import load_writer_config

from . import __version__
from .direct import IngestRun, find_rulebooks, ingest_all

EXIT_NOTHING_WRITTEN = 3

EPILOG = """\
examples:
  python ingest_rulebook.py generated/                      # dry run, every rulebook
  python ingest_rulebook.py generated/ --write              # apply
  python ingest_rulebook.py a.md b.md --write
  python ingest_rulebook.py generated/ --write --force      # write rejected ones too

what this skips:
  AutoGraph and Station 1 - the bundles are built from the rulebook rather than
  extracted from a knowledge graph - and Station 3, because the rulebook already
  says which state is a precondition and which is an effect.

  Stations 2, 4 and 5 run in full: every relationship is typed, the ordering is
  derived and cycle-guarded, and the subgraph is written under the skill's own
  scope.

  So this measures the rulebook, not the extraction. It is the fast path, and a
  rulebook that lands cleanly here can still lose something through AutoGraph.

configuration: the same environment as write_plangraph.py (ARANGO_*,
PROJECT_NAME, PLANGRAPH_PREFIX). No LLM key is needed.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ingest-rulebook",
        description=(
            "Write a rulebook straight into the PlanGraph, skipping AutoGraph. "
            "A plain run is a dry run."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", nargs="+", help="rulebook .md file(s), or a directory")
    parser.add_argument("--write", action="store_true", help="apply; without it, a dry run")
    parser.add_argument(
        "--force",
        action="store_true",
        help="write even a rulebook the validation gate rejects",
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--version", action="version", version=f"ingest-rulebook {__version__}")
    return parser


def report(run: IngestRun, dry_run: bool, stream: IO[str]) -> None:
    g = glyphs(stream)
    print(f"Rulebook {g['em']} PlanGraph   (AutoGraph skipped)", file=stream)
    print("=" * 72, file=stream)

    for result in run.results:
        if result.skipped:
            print(f"  SKIP   {result.path}", file=stream)
            print(f"         {result.skipped}", file=stream)
            continue

        verdict = result.report.verdict if result.report else "?"
        counts = result.write.verified if result.write else {}
        print(f"  {'PLAN' if dry_run else 'WROTE'}  {result.rulebook.skill}", file=stream)
        print(
            f"         {len(result.plan.vertices)} vertices, {len(result.plan.edges)} edges, "
            f"{result.derived} precedes derived   [{verdict}]",
            file=stream,
        )
        if result.report and result.report.issues:
            for issue in result.report.issues:
                print(f"         {'fatal' if issue.fatal else 'flag '} {issue.code}", file=stream)
        if counts and not dry_run:
            edges = next((v for k, v in counts.items() if k.endswith("PlanEdges")), 0)
            print(f"         verified: {edges} edges in scope {result.scope!r}", file=stream)

    print("-" * 72, file=stream)
    print(
        f"  {len(run.written)} rulebook(s) {'would be written' if dry_run else 'written'}, "
        f"{len(run.skipped)} skipped",
        file=stream,
    )
    if dry_run and run.written:
        print("  DRY RUN — nothing was created, deleted or written. Pass --write.", file=stream)
    stream.flush()


def run(
    targets: Sequence[str],
    write: bool = False,
    force: bool = False,
    output_format: str = "table",
    stdout: IO[str] | None = None,
    db: Any = None,
) -> int:
    stdout = stdout if stdout is not None else sys.stdout

    paths: list[Any] = []
    for target in targets:
        paths.extend(find_rulebooks(target))
    if not paths:
        raise ConfigError(
            f"no rulebook found at {', '.join(targets)}.",
            "point at a .md file, or a directory holding them.",
        )

    config = load_writer_config(dry_run=not write)
    handle = connect(config.arango) if db is None else db
    result = ingest_all(paths, handle, config, require_verdict=not force)

    if output_format == "json":
        json.dump(result.to_dict(), stdout, indent=2, ensure_ascii=False)
        stdout.write("\n")
    else:
        report(result, dry_run=not write, stream=stdout)

    return EXIT_OK if result.written else EXIT_NOTHING_WRITTEN


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(
            targets=args.target,
            write=args.write,
            force=args.force,
            output_format=args.format,
        )
    except HarnessError as error:
        print(error.render(), file=sys.stderr)
        return error.exit_code
    except FileNotFoundError as error:
        print(f"error: {error.strerror}: {error.filename}", file=sys.stderr)
        return EXIT_UNEXPECTED
    except KeyboardInterrupt:
        print("interrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as error:
        print(f"unexpected error: {error.__class__.__name__}: {error}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
