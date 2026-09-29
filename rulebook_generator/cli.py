"""Entry point for the Rulebook Generator.

    python generate_rulebook.py https://www.youtube.com/watch?v=...

Upstream of everything else: its only output is a rulebook in the format the
pipeline already consumes. Nothing downstream changes.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import IO, Any, Sequence

from kg_read_harness.errors import EXIT_INTERRUPTED, EXIT_OK, EXIT_UNEXPECTED, HarnessError

from . import __version__
from .cache import PayloadCache
from .config import load_generator_config
from .ingest import ingest, write_rulebook
from .pipeline import generate
from .report import dump_json, print_report
from .schema import ACCEPT, FLAG, REJECT
from .transcript import from_file, from_youtube

#: A rejected video is a valid outcome, not a crash — it gets its own code so a
#: batch run can tell "this is not a recipe" from "the tool broke".
EXIT_REJECTED = 3
EXIT_FLAGGED = 4

EPILOG = """\
examples:
  python generate_rulebook.py https://youtu.be/VIDEOID
  python generate_rulebook.py VIDEOID --show                # print the markdown
  python generate_rulebook.py --transcript captions.txt     # no network needed
  python generate_rulebook.py --transcript manual.md --manual   # from documentation
  python generate_rulebook.py VIDEOID --write --ingest      # link-in to plan-out
  python generate_rulebook.py --transcript driver.txt --code --split --write
                                                            # one rulebook per task

one rulebook per task (--split):
  A reference rulebook of every operation is reconstructed first, the model
  picks the tasks a user would ask for, and each task is cut out of the
  reference by following its goal step's preconditions back. Every rulebook is
  graded separately; --write saves each one strictness allows.

the gate:
  Every generated rulebook is run through this project's own bridge - Station 2
  types its relationships, Station 4 derives the ordering and guards the cycle -
  and graded: DAG, no orphan preconditions, a producible goal state, a valid
  topological order. Verdict is accept / flag_for_review / reject.

  Only an `accept` is ever auto-ingested. That is not configurable, because the
  preconditions in a generated rulebook are inferred rather than stated, and a
  wrong precondition is a plan a robot would try to execute.

configuration (environment variables):
  LLM_API_KEY / OPENROUTER_API_KEY   required (the project's existing key)
  RULEBOOK_MODEL              optional  overrides LLM_MODEL for extraction
  RULEBOOK_STRICTNESS         optional  strict | normal | lenient (default normal)
                                        lenient also writes flagged rulebooks
  RULEBOOK_OUTPUT_DIR         optional  default 'generated'
  RULEBOOK_CACHE / _PATH      optional  default on; same video -> same rulebook
  RULEBOOK_AUTO_INGEST        optional  default off
  AUTOGRAPH_URL / _API_KEY    optional  needed only for --ingest

exit codes: 0 accept, 3 rejected, 4 flagged for review.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="generate-rulebook",
        description=(
            "Turn a captioned how-to video into a canonical rulebook, gated by "
            "round-trip validation through the project's own pipeline."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("url", nargs="?", help="a YouTube video/shorts URL, or a bare video id")
    parser.add_argument(
        "--transcript", metavar="PATH", help="use a local caption file instead of fetching"
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help=(
            "the --transcript file is written documentation, not speech: keep its "
            "parentheses and line structure, and read preconditions off the page "
            "rather than inferring them"
        ),
    )
    parser.add_argument(
        "--code",
        action="store_true",
        help="the --transcript file is source code: every step carries its interface",
    )
    parser.add_argument(
        "--split",
        action="store_true",
        help="a reference rulebook of every operation, then one rulebook per task",
    )
    parser.add_argument("--write", action="store_true", help="save the rulebook to the output dir")
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="on accept, push to AutoGraph (needs AUTOGRAPH_URL); implies --write",
    )
    parser.add_argument("--show", action="store_true", help="print the rulebook markdown")
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--no-cache", action="store_true", help="force a fresh extraction")
    parser.add_argument("--version", action="version", version=f"generate-rulebook {__version__}")
    return parser


def run(
    url: str | None = None,
    transcript_path: str | None = None,
    manual: bool = False,
    code: bool = False,
    split: bool = False,
    write: bool = False,
    do_ingest: bool = False,
    show: bool = False,
    output_format: str = "table",
    no_cache: bool = False,
    stdout: IO[str] | None = None,
    provider: Any = None,
) -> int:
    stdout = stdout if stdout is not None else sys.stdout

    config = load_generator_config()
    if no_cache:
        import dataclasses

        config = dataclasses.replace(config, cache_enabled=False)

    transcript = (
        from_file(transcript_path, url=url or "", manual=manual, code=code)
        if transcript_path
        else from_youtube(url or "")
    )

    if split:
        return run_split(transcript, config, write, output_format, stdout, provider)

    result = generate(
        transcript,
        config,
        provider=provider,
        cache=PayloadCache(config.cache_path, config.cache_enabled),
    )

    if (write or do_ingest) and result.writable and result.verdict in config.accepts:
        result.written_to = write_rulebook(
            result.markdown, result.rulebook.skill, config.output_dir
        )
    elif (write or do_ingest) and result.writable:
        result.ingest_detail = (
            f"not written: the verdict is {result.verdict!r} and strictness "
            f"{config.strictness!r} writes only {', '.join(config.accepts)}"
        )

    if do_ingest or config.may_ingest(result.verdict):
        # FR-7: a non-accept verdict is never ingested, whatever the flags say.
        if result.verdict != ACCEPT:
            result.ingest_detail = (
                f"not ingested: the verdict is {result.verdict!r}. Only an accepted "
                "rulebook is ever ingested - a wrong precondition becomes a plan a "
                "robot would try to run."
            )
        elif result.rulebook is not None:
            result.ingested, result.ingest_detail = ingest(
                result.markdown, result.rulebook.skill
            )

    if output_format == "json":
        dump_json(result, stdout)
    else:
        print_report(config, result, stdout, show_markdown=show)

    return {ACCEPT: EXIT_OK, FLAG: EXIT_FLAGGED, REJECT: EXIT_REJECTED}[result.verdict]


def run_split(transcript: Any, config: Any, write: bool, output_format: str, stdout: IO[str], provider: Any) -> int:
    """--split: the reference, then one graded rulebook per task."""
    from kg_read_harness.output import glyphs

    from .split import generate_split

    split = generate_split(
        transcript, config, provider=provider,
        cache=PayloadCache(config.cache_path, config.cache_enabled),
    )
    written: list[str] = []
    if write:
        for book in split.tasks:
            if book.writable and book.verdict in config.accepts:
                book.written_to = write_rulebook(book.markdown, book.rulebook.skill, config.output_dir)
                written.append(book.written_to)

    if output_format == "json":
        json.dump(split.to_dict(), stdout, indent=2, ensure_ascii=False)
        stdout.write("\n")
    else:
        g = glyphs(stdout)
        reference = split.reference
        print(f"Rulebook Generator {g['em']} one rulebook per task", file=stdout)
        print("=" * 72, file=stdout)
        if reference.rulebook is not None:
            print(
                f"  reference  {reference.rulebook.skill}: {len(reference.rulebook.primitives)} "
                f"steps [{reference.verdict}]",
                file=stdout,
            )
        if split.choice is not None:
            how = "chosen by the model" if split.choice.method == "llm" else (
                f"chosen by rule ({split.choice.fallback_reason})"
            )
            print(f"  tasks      {len(split.tasks)} {how}", file=stdout)
        print("-" * 72, file=stdout)
        for book in split.tasks:
            steps = " -> ".join(book.rulebook.primitive_names)
            print(f"  {book.verdict:<16} {book.rulebook.skill}", file=stdout)
            print(f"  {'':<16} {steps}", file=stdout)
        print("-" * 72, file=stdout)
        if split.reason:
            print(f"  {split.reason}", file=stdout)
        if written:
            print(f"  wrote {len(written)} rulebook(s) to {config.output_dir}", file=stdout)
        elif split.tasks and not write:
            print("  nothing written; pass --write to save the rulebooks strictness allows.", file=stdout)

    if not split.tasks:
        return EXIT_REJECTED
    return EXIT_OK if split.accepted else EXIT_FLAGGED


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.url and not args.transcript:
        build_parser().error("give a video URL or --transcript PATH")
    try:
        return run(
            url=args.url,
            transcript_path=args.transcript,
            manual=args.manual,
            code=args.code,
            split=args.split,
            write=args.write,
            do_ingest=args.ingest,
            show=args.show,
            output_format=args.format,
            no_cache=args.no_cache,
        )
    except HarnessError as error:
        print(error.render(), file=sys.stderr)
        return error.exit_code
    except FileNotFoundError as error:
        print(f"error: {error.strerror}: {error.filename}", file=sys.stderr)
        return EXIT_UNEXPECTED
    except json.JSONDecodeError as error:
        print(f"error: invalid JSON ({error}).", file=sys.stderr)
        return EXIT_UNEXPECTED
    except KeyboardInterrupt:
        print("interrupted; nothing was written.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as error:
        print(f"unexpected error: {error.__class__.__name__}: {error}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
