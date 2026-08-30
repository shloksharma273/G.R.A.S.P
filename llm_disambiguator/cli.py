"""Entry point for Station 3 (PRD Section 5).

By default it runs the whole bridge so far: Station 1 reads the KG, Station 2
classifies, and Station 3 resolves the deferred bucket. `--from-json` takes
Station 2's JSON output instead, and `--from-bundles` takes Station 1's, so each
seam can be exercised on its own.

All I/O lives here and in `provider.py`/`cache.py`; `disambiguator.disambiguate()`
takes its provider and cache as arguments, which is what makes it testable with
no network.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import IO, Any, Iterable, Sequence

from kg_read_harness.bundle import Bundle
from kg_read_harness.client import connect
from kg_read_harness.config import load_config
from kg_read_harness.errors import (
    EXIT_INTERRUPTED,
    EXIT_OK,
    EXIT_UNEXPECTED,
    ConfigError,
    HarnessError,
)
from kg_read_harness.read import read_relationship_bundles
from kg_read_harness.validate import validate_kg
from rule_preclassifier import classify
from rule_preclassifier.cli import bundles_from_json

from . import __version__
from .cache import VerdictCache
from .config import load_llm_config
from .disambiguator import disambiguate
from .report import dump_json, print_report

EPILOG = """\
input (pick one):
  (default)        run Station 1 + Station 2 first, then disambiguate what they defer.
                   Uses the Station 1 environment variables (ARANGO_URL, ARANGO_DB, ...).
  --from-bundles   a Station 1 JSON listing; Station 2 runs in-process.
  --from-json      a Station 2 JSON result; its 'deferred' bucket is used directly.

configuration (environment variables only - the key is never a command-line flag):
  LLM_API_KEY / OPENROUTER_API_KEY / OPENAI_API_KEY   required (first one set wins)
  LLM_BASE_URL              optional  default https://openrouter.ai/api/v1
  LLM_MODEL                 optional  default anthropic/claude-opus-5
  LLM_TEMPERATURE           optional  default 0
  LLM_CONFIDENCE_THRESHOLD  optional  default 0.75
  LLM_BATCH_SIZE            optional  default 10
  LLM_MAX_REQUESTS          optional  default 50   (per-run spend cap; 0 = no calls)
  LLM_LEXICAL_PREPASS       optional  default on
  LLM_CACHE / LLM_CACHE_PATH  optional  default on, .grasp_cache/station3.json
  LLM_MAX_RETRIES           optional  default 3
  LLM_MAX_TOKENS            optional  default 4000
  LLM_REASONING             optional  default off

Re-running with a warm cache issues zero requests and reproduces the same output.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="llm-disambiguator",
        description=(
            "Decide whether each deferred PRIMITIVE-STATE edge is a precondition "
            "(requires) or an effect (produces). Abstains rather than guessing."
        ),
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--from-json", metavar="PATH", help="a Station 2 JSON result ('-' for stdin)"
    )
    source.add_argument(
        "--from-bundles", metavar="PATH", help="a Station 1 JSON listing ('-' for stdin)"
    )
    parser.add_argument("--format", choices=("table", "json"), default="table")
    parser.add_argument("--no-listing", action="store_true", help="summary only (table format)")
    parser.add_argument(
        "--no-cache", action="store_true", help="ignore and do not write the verdict cache"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="lexical pre-pass and cache only; make no LLM call (sets the cap to 0)",
    )
    parser.add_argument("--version", action="version", version=f"llm-disambiguator {__version__}")
    return parser


def _read_json(source: str, stdin: IO[str] | None = None) -> Any:
    if source == "-":
        return json.load(stdin if stdin is not None else sys.stdin)
    with open(source, encoding="utf-8") as handle:
        return json.load(handle)


def deferred_from_station2_json(payload: Any) -> list[Bundle]:
    """The deferred bucket of a Station 2 result, as plain bundles."""
    if not isinstance(payload, dict) or not isinstance(payload.get("deferred"), list):
        raise ConfigError(
            "the Station 2 JSON must be an object with a 'deferred' array.",
            "produce it with: python classify_kg.py --format json > classified.json",
        )
    return bundles_from_json(payload["deferred"])


def load_deferred(
    from_json: str | None = None,
    from_bundles: str | None = None,
    stdin: IO[str] | None = None,
) -> list[Bundle]:
    """Station 2's deferred bucket, from whichever seam was chosen."""
    if from_json:
        return deferred_from_station2_json(_read_json(from_json, stdin))

    if from_bundles:
        bundles = bundles_from_json(_read_json(from_bundles, stdin))
    else:
        config = load_config()
        db = connect(config)
        validate_kg(db, config)
        bundles = list(read_relationship_bundles(db, config))

    return [item.bundle for item in classify(bundles).deferred]


def run(
    from_json: str | None = None,
    from_bundles: str | None = None,
    output_format: str = "table",
    listing: bool = True,
    no_cache: bool = False,
    dry_run: bool = False,
    stdout: IO[str] | None = None,
    stderr: IO[str] | None = None,
    stdin: IO[str] | None = None,
    deferred: Iterable[Any] | None = None,
    provider: Any = None,
) -> int:
    stdout = stdout if stdout is not None else sys.stdout
    stderr = stderr if stderr is not None else sys.stderr

    config = load_llm_config()
    if no_cache:
        config = replace_config(config, cache_enabled=False)
    if dry_run:
        config = replace_config(config, max_requests=0)

    items = list(deferred) if deferred is not None else load_deferred(from_json, from_bundles, stdin)
    result = disambiguate(
        items,
        config,
        provider=provider,
        cache=VerdictCache(config.cache_path, config.cache_enabled),
    )

    if output_format == "json":
        dump_json(result, stdout)
    else:
        print_report(config, result, stdout, listing=listing)
    return EXIT_OK


def replace_config(config, **changes):
    """`dataclasses.replace` without importing it at every call site.

    --dry-run sets `max_requests` to 0, so the provider's single cap check is the
    one place that decides whether any call happens.
    """
    import dataclasses

    return dataclasses.replace(config, **changes)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return run(
            from_json=args.from_json,
            from_bundles=args.from_bundles,
            output_format=args.format,
            listing=not args.no_listing,
            no_cache=args.no_cache,
            dry_run=args.dry_run,
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
        print("interrupted; Station 3 writes nothing to the graph.", file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as error:
        print(f"unexpected error: {error.__class__.__name__}: {error}", file=sys.stderr)
        return EXIT_UNEXPECTED


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
