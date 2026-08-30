"""One-shot CLI entry point (Section 5) with actionable errors (FR-8).

Flow: load config from the environment, connect read-only, validate the KG, stream
bundles to the terminal, print the type-pair summary, exit 0. Any failure prints
what failed plus the most likely fix and exits with a distinct non-zero code.
"""

from __future__ import annotations

import argparse
import sys
from typing import IO, Sequence

from . import __version__
from .client import connect
from .config import Config, load_config
from .errors import EXIT_INTERRUPTED, EXIT_OK, EXIT_UNEXPECTED, EmptyResultError, HarnessError
from .output import (
    Summary,
    make_writer,
    print_header,
    print_summary,
    print_warnings,
    side_channel,
)
from .read import ReadStats, read_relationship_bundles
from .validate import ValidationReport, validate_kg

EPILOG = """\
configuration (environment variables only — no secrets on the command line):

  ARANGO_URL          required   endpoint, e.g. http://localhost:8529
  ARANGO_DB           required   project database holding the KG
  ARANGO_USERNAME     required*  DB user with read access (e.g. root)
  ARANGO_PASSWORD     required*  password for that user
  ARANGO_AUTH_TOKEN   optional   JWT bearer, alternative to username/password
  PROJECT_NAME        required   used to derive default collection names
  ENTITY_COLLECTION   optional   default {PROJECT_NAME}_Entities
  RELATION_COLLECTION optional   default {PROJECT_NAME}_Relations
  ENTITY_TYPE_FIELD   optional   attribute holding entity type; default entity_type
  DESCRIPTION_FIELD   optional   attribute holding relation text; default description
  RELATION_TYPE       optional   edge type to read; default RELATED_TO
  ENTITY_TYPE_FILTER  optional   comma list, e.g. SKILL,PRIMITIVE,OBJECT,STATE
  OUTPUT_FORMAT       optional   table | json; default table
  LIMIT               optional   max rows for dev; default 0 (all)
  ENTITY_NAME_FIELD   optional   attribute holding entity name; default name
  RELATION_TYPE_FIELD optional   attribute holding relation type; default type

  * either ARANGO_USERNAME + ARANGO_PASSWORD, or ARANGO_AUTH_TOKEN.

This harness is read-only: it never writes, updates, deletes, or changes schema.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kg-read-harness",
        description=(
            "Read every entity-to-entity relationship from an AutoGraph knowledge graph "
            "and print it as a typed bundle. Read-only; configured entirely by "
            "environment variables."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"kg-read-harness {__version__}")
    return parser


def run(
    config: Config,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    db: object | None = None,
) -> int:
    """Execute one read pass. Returns the process exit code."""
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr
    notes = side_channel(config, stdout, stderr)

    handle = connect(config) if db is None else db
    report: ValidationReport = validate_kg(handle, config)

    print_header(config, report, notes)
    print_warnings(report.warnings, notes)

    summary = Summary()
    stats = ReadStats()
    writer = make_writer(config, stdout)
    try:
        for bundle in read_relationship_bundles(
            handle, config, stats, on_warning=lambda msg: print(f"warning: {msg}", file=notes)
        ):
            writer.write(bundle)
            summary.add(bundle)
    finally:
        writer.close()

    if summary.total == 0:
        raise EmptyResultError(
            f"no relationships of type {config.relation_type!r} were found in "
            f"{config.relation_collection!r}"
            + (
                f" matching entity types {', '.join(config.entity_type_filter)}"
                if config.entity_type_filter
                else ""
            )
            + ".",
            "relation types actually present: "
            f"{report.observed_types_summary()}. Either this is a VectorRAG-only build, "
            "or RELATION_TYPE / RELATION_TYPE_FIELD / ENTITY_TYPE_FILTER name something "
            "the KG does not contain — confirm in the Graph Explorer.",
        )

    print_summary(summary, notes, skipped=stats.skipped_dangling)
    if stats.skipped_dangling:
        print(
            f"\n{stats.skipped_dangling} relationship(s) were skipped because an endpoint "
            "no longer resolves.",
            file=notes,
        )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)

    try:
        config = load_config()
        return run(config)
    except HarnessError as error:
        print(error.render(), file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print("interrupted; nothing was written (the harness is read-only).", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as error:  # unexpected: still exit non-zero, still be useful
        print(
            f"unexpected error: {error.__class__.__name__}: {error}\n"
            "  likely fix: re-run with the collection and attribute names confirmed "
            "against the live instance; if it persists this is a harness bug.",
            file=sys.stderr,
        )
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
