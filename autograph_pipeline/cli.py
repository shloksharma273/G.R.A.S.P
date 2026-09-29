"""Entry point: a module of rulebooks through AutoGraph and into the PlanGraph.

A plain run is a dry run - it connects, reads the project's state and reports
what each stage would do. `--write` does it.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import IO, Any, Sequence

from kg_read_harness.errors import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_UNEXPECTED,
    ConfigError,
    HarnessError,
)
from kg_read_harness.output import glyphs
from rulebook_generator.direct import find_rulebooks

from . import __version__
from .client import Platform
from .config import load_pipeline_config
from .pipeline import (
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PLANNED,
    STATUS_SKIPPED,
    Pipeline,
    PipelineResult,
    Rulebook,
)
from .plangraph import bridge_builder

DEFAULT_CATEGORY = "rulebooks"

EPILOG = """\
examples:
  python build_kg.py generated/ --project openAMR --category nav            # dry run
  python build_kg.py generated/ --project openAMR --category nav --write    # build
  python build_kg.py generated/ --project openAMR --category nav --write --rebuild
  python build_kg.py dataset/ --project kitchen --write --provision --no-plangraph

stages, each skipped when the project overview says it is already current:
  connect      the project's AutoGraph service (--provision deploys one)
  upload       rulebooks -> File Manager, scope [project, category]
  corpus       POST /v1/corpus/builds
  strategize   POST /v1/rag-strategizer/analyze   (complexity very_high)
  ontology     PATCH each cluster -> FullGraphRAG [SKILL, PRIMITIVE, OBJECT, STATE]
  kg           POST /v1/orchestrate
  plangraph    Stations 1-5 over {project}_kg

configuration: ARANGO_URL, ARANGO_DB and credentials, as for the other stations.
  AUTOGRAPH_SERVICE_PATH   use this service path instead of the project's record
  AUTOGRAPH_COMPLEXITY     very_high (default) | high | moderate | low | very_low
  AUTOGRAPH_ONTOLOGY       SKILL,PRIMITIVE,OBJECT,STATE (default)
  AUTOGRAPH_REPLICAS       importer replicas (1)
  AUTOGRAPH_MODEL_FROM     --provision: copy model settings from this project
  AUTOGRAPH_POLL_SECONDS   status poll interval (10)

exit codes: 0 done (or dry run complete) · 2 configuration · 3 auth
            4 unreachable · 10 a stage failed
"""

MARKS = {
    STATUS_DONE: "✓",
    STATUS_SKIPPED: "·",
    STATUS_PLANNED: "○",
    STATUS_FAILED: "✗",
}
ASCII_MARKS = {STATUS_DONE: "+", STATUS_SKIPPED: "=", STATUS_PLANNED: "o", STATUS_FAILED: "x"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="build-kg",
        description=(
            "Upload rulebooks into one AutoGraph module, build its corpus graph, "
            "strategies and knowledge graph with the bridge's ontology, then its "
            "PlanGraph. A plain run is a dry run."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("target", nargs="+", help="rulebook .md file(s), or a directory")
    parser.add_argument("--project", help="the AutoGraph project (default: PROJECT_NAME)")
    parser.add_argument(
        "--category",
        default=DEFAULT_CATEGORY,
        help=f"the module every rulebook goes in (default: {DEFAULT_CATEGORY})",
    )
    parser.add_argument("--write", action="store_true", help="apply; without it, a dry run")
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="delete the category first (corpus, strategies, KG partitions and files)",
    )
    parser.add_argument(
        "--provision",
        action="store_true",
        help="create the project and deploy its AutoGraph service if missing",
    )
    parser.add_argument(
        "--fps-user",
        help="--provision: the ArangoDB user File Parsing resumes builds as "
        "(default: found in the database)",
    )
    parser.add_argument(
        "--no-plangraph", action="store_true", help="stop once the knowledge graph is built"
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--version", action="version", version=f"build-kg {__version__}")
    return parser


def load_rulebooks(targets: Sequence[str]) -> list[Rulebook]:
    paths: list[Any] = []
    for target in targets:
        paths.extend(find_rulebooks(target))
    if not paths:
        raise ConfigError(
            f"no rulebook found at {', '.join(targets)}.",
            "point at a .md file, or a directory holding them.",
        )
    return [Rulebook.from_path(p) for p in paths]


def report(result: PipelineResult, stream: IO[str]) -> None:
    g = glyphs(stream)
    marks = MARKS if g["em"] == "—" else ASCII_MARKS  # the stream can encode unicode
    print(f"Rulebooks {g['em']} AutoGraph {g['em']} PlanGraph", file=stream)
    print("=" * 72, file=stream)
    print(f"  project   {result.project}", file=stream)
    print(f"  category  {result.category}", file=stream)
    if result.service:
        print(f"  service   {result.service}", file=stream)
    print("-" * 72, file=stream)
    for stage in result.stages:
        print(f"  {marks.get(stage.status, '?')} {stage.stage:<30} {stage.detail}", file=stream)
    print("-" * 72, file=stream)
    if result.failed:
        print(f"  stopped at: {result.failed_stage}", file=stream)
        if result.error is not None and result.error.hint:
            print(f"  likely fix: {result.error.hint}", file=stream)
    elif not result.write:
        print("  DRY RUN - nothing was uploaded, built or written. Pass --write.", file=stream)
    else:
        print("  done.", file=stream)
    stream.flush()


def run(
    targets: Sequence[str],
    project: str | None = None,
    category: str = DEFAULT_CATEGORY,
    write: bool = False,
    rebuild: bool = False,
    provision: bool = False,
    fps_user: str | None = None,
    plangraph: bool = True,
    output_format: str = "table",
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    platform: Platform | None = None,
    env: Any = None,
    build_plangraph: Any = None,
) -> int:
    import os

    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    env = os.environ if env is None else env

    project = (project or env.get("PROJECT_NAME") or "").strip()
    if not project:
        raise ConfigError("no project named.", "pass --project, or set PROJECT_NAME.")
    rulebooks = load_rulebooks(targets)
    config = load_pipeline_config(env)
    if platform is None:
        platform = Platform(
            url=config.url,
            database=config.database,
            username=config.username,
            password=config.password,
            token=config.auth_token,
        )
    if plangraph and build_plangraph is None:
        build_plangraph = bridge_builder(env)

    def progress(stage: str, message: str) -> None:
        if output_format == "table":
            print(f"    {stage}: {message}", file=stderr, flush=True)

    result = Pipeline(
        platform,
        config,
        project,
        category.strip(),
        rulebooks,
        write=write,
        rebuild=rebuild,
        provision=provision,
        fps_user=fps_user,
        build_plangraph=build_plangraph if plangraph else None,
        on_progress=progress,
    ).run()

    if output_format == "json":
        json.dump(result.to_dict(), stdout, indent=2, ensure_ascii=False)
        stdout.write("\n")
    else:
        report(result, stdout)

    if result.error is not None:
        return result.error.exit_code
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(
            targets=args.target,
            project=args.project,
            category=args.category,
            write=args.write,
            rebuild=args.rebuild,
            provision=args.provision,
            fps_user=args.fps_user,
            plangraph=not args.no_plangraph,
            output_format=args.format,
        )
    except HarnessError as error:
        print(error.render(), file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("interrupted.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as error:
        print(f"unexpected error: {error.__class__.__name__}: {error}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
