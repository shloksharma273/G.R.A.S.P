"""Entry point for Station 2 (PRD Section 5).

Station 2 "runs as an in-process step immediately after Station 1 in the same
batch job", so that is the default: read the KG, classify, print the summary.
`--from-json` replaces the live read with a Station 1 JSON listing, which is how
the station is exercised offline.

All I/O lives here. `classifier.classify()` stays pure (FR-8); this module is the
only part of the station that touches a database, a file or a stream.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import IO, Iterable, Sequence

from kg_read_harness.bundle import Bundle, Entity
from kg_read_harness.client import connect
from kg_read_harness.config import load_config
from kg_read_harness.errors import EXIT_INTERRUPTED, EXIT_OK, EXIT_UNEXPECTED, ConfigError, HarnessError
from kg_read_harness.read import read_relationship_bundles
from kg_read_harness.validate import validate_kg

from . import __version__
from .classifier import classify
from .report import dump_json, print_report

EPILOG = """\
input:
  by default Station 2 runs Station 1 first, so the same environment variables
  apply (ARANGO_URL, ARANGO_DB, PROJECT_NAME, ... — see `python read_kg.py --help`).

  with --from-json it reads a Station 1 listing instead and touches no database:

      OUTPUT_FORMAT=json python read_kg.py > bundles.json
      python classify_kg.py --from-json bundles.json

output:
  --format table  the annotated listing plus the summary (default)
  --format json   the three buckets and the summary as one JSON object on stdout;
                  nothing else is printed there, so it pipes into Station 3/4.

Station 2 is a pure function of its input: no LLM, no writes, no randomness.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rule-preclassifier",
        description=(
            "Assign a planning edge type to every Station 1 bundle from its entity-type "
            "pair alone. Stamps what is certain, defers PRIMITIVE-STATE to Station 3, "
            "parks the rest with one reason code."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--from-json",
        metavar="PATH",
        help="read Station 1 bundles from a JSON file ('-' for stdin) instead of the KG",
    )
    parser.add_argument(
        "--format",
        choices=("table", "json"),
        default="table",
        help="output form; default table",
    )
    parser.add_argument(
        "--no-listing",
        action="store_true",
        help="print only the summary, not the per-bundle listing (table format)",
    )
    parser.add_argument("--version", action="version", version=f"rule-preclassifier {__version__}")
    return parser


def bundles_from_json(payload: object) -> list[Bundle]:
    """Rebuild Station 1 bundles from its JSON listing (the Section 7 contract)."""
    if not isinstance(payload, list):
        raise ConfigError(
            "the Station 1 JSON must be an array of bundles.",
            "produce it with: OUTPUT_FORMAT=json python read_kg.py > bundles.json",
        )
    bundles = []
    for position, row in enumerate(payload):
        try:
            bundles.append(
                Bundle(
                    relation_key=str(row["relation_key"]),
                    source=Entity(str(row["source"]["name"]), str(row["source"]["type"])),
                    target=Entity(str(row["target"]["name"]), str(row["target"]["type"])),
                    description=str(row.get("description", "")),
                )
            )
        except (KeyError, TypeError) as error:
            raise ConfigError(
                f"bundle at index {position} does not match the Station 1 contract "
                f"(missing {error}).",
                "regenerate it with OUTPUT_FORMAT=json python read_kg.py.",
            ) from None
    return bundles


def load_bundles(source: str | None, stdin: IO[str] | None = None) -> Iterable[Bundle]:
    """Bundles from a Station 1 JSON listing, or from a live Station 1 read."""
    if source is None:
        config = load_config()
        db = connect(config)
        validate_kg(db, config)
        return list(read_relationship_bundles(db, config))

    if source == "-":
        stream = stdin if stdin is not None else sys.stdin
        payload = json.load(stream)
    else:
        with open(source, encoding="utf-8") as handle:
            payload = json.load(handle)
    return bundles_from_json(payload)


def run(
    from_json: str | None = None,
    output_format: str = "table",
    listing: bool = True,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    stdin: IO[str] | None = None,
    bundles: Iterable[Bundle] | None = None,
) -> int:
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr

    items = list(bundles) if bundles is not None else list(load_bundles(from_json, stdin))
    result = classify(items)

    if output_format == "json":
        dump_json(result, stdout)
    else:
        print_report(result, stdout, listing=listing)
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(
            from_json=args.from_json,
            output_format=args.format,
            listing=not args.no_listing,
        )
    except HarnessError as error:
        print(error.render(), file=sys.stderr)
        return error.exit_code
    except FileNotFoundError as error:
        print(f"error: {error.strerror}: {error.filename}", file=sys.stderr)
        return EXIT_UNEXPECTED
    except json.JSONDecodeError as error:
        print(
            f"error: the Station 1 input is not valid JSON ({error}).\n"
            "  likely fix: regenerate it with OUTPUT_FORMAT=json python read_kg.py.",
            file=sys.stderr,
        )
        return EXIT_UNEXPECTED
    except KeyboardInterrupt:
        print("interrupted; Station 2 writes nothing.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as error:
        print(f"unexpected error: {error.__class__.__name__}: {error}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
