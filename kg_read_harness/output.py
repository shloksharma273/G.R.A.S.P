"""Terminal output: the listing and the summary (FR-6, FR-7, Section 9).

Two views, two formats:

* `table` — one readable line (or short block) per bundle, then a type-pair
  summary table. Everything goes to stdout.
* `json`  — the listing is a JSON array on stdout, suitable for piping into later
  bridge stations; the header, warnings and summary go to stderr so the array
  stays machine-clean.
"""

from __future__ import annotations

import json
import sys
import textwrap
from collections import Counter
from dataclasses import dataclass
from typing import Any, IO, Iterable

from .bundle import Bundle
from .config import Config

#: A bundle stays on one line while it fits this width; otherwise the description
#: moves to an indented block underneath.
MAX_LINE_WIDTH = 118

#: Width used to wrap long descriptions.
WRAP_WIDTH = 96

_UNICODE = {"arrow": "──▶", "dash": "──", "turn": "↳", "to": "→", "em": "—"}
_ASCII = {"arrow": "-->", "dash": "--", "turn": ">", "to": "->", "em": "-"}


def glyphs(stream: IO[str]) -> dict[str, str]:
    """Prefer box-drawing glyphs, fall back to ASCII if the stream can't encode them."""
    encoding = getattr(stream, "encoding", None) or "ascii"
    try:
        "".join(_UNICODE.values()).encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return _ASCII
    return _UNICODE


def collapse(text: str) -> str:
    """One-line form of a free-text description."""
    return " ".join(text.split())


class Summary:
    """Counts of bundles per (source_type → target_type) pair (FR-7)."""

    def __init__(self) -> None:
        self.pairs: Counter[tuple[str, str]] = Counter()
        self.total = 0

    def add(self, bundle: Bundle) -> None:
        self.pairs[bundle.type_pair] += 1
        self.total += 1

    def rows(self) -> list[tuple[str, str, int]]:
        """Pairs ordered by count (descending), then alphabetically — stable output."""
        ordered = sorted(self.pairs.items(), key=lambda item: (-item[1], item[0]))
        return [(source, target, count) for (source, target), count in ordered]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "by_type_pair": [
                {"source_type": s, "target_type": t, "count": c} for s, t, c in self.rows()
            ],
        }


@dataclass
class _WriterBase:
    stream: IO[str]
    count: int = 0

    def write(self, bundle: Bundle) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class TableWriter(_WriterBase):
    """Human-readable listing: `source (TYPE) --[description]--> target (TYPE)`."""

    def write(self, bundle: Bundle) -> None:
        g = glyphs(self.stream)
        self.count += 1
        index = f"[{self.count:>4}]"
        description = collapse(bundle.description)
        source = f"{bundle.source.name} ({bundle.source.type})"
        target = f"{bundle.target.name} ({bundle.target.type})"

        if description:
            inline = f"{index} {source} {g['dash']}[ {description} ]{g['arrow']} {target}"
            if len(inline) <= MAX_LINE_WIDTH:
                print(inline, file=self.stream)
                return

        print(f"{index} {source} {g['arrow']} {target}", file=self.stream)
        if description:
            indent = " " * (len(index) + 1)
            for line in textwrap.wrap(description, width=WRAP_WIDTH):
                print(f"{indent}{g['turn']} {line}", file=self.stream)
                g = {**g, "turn": " "}  # only the first wrapped line gets the marker
        else:
            print(f"{' ' * (len(index) + 1)}{g['turn']} (no description)", file=self.stream)

    def close(self) -> None:
        self.stream.flush()


class JsonWriter(_WriterBase):
    """Streaming JSON array — bounded memory, valid JSON even when empty."""

    def write(self, bundle: Bundle) -> None:
        prefix = "[\n" if self.count == 0 else ",\n"
        self.stream.write(prefix)
        self.stream.write(
            textwrap.indent(json.dumps(bundle.to_dict(), indent=2, ensure_ascii=False), "  ")
        )
        self.count += 1

    def close(self) -> None:
        self.stream.write("\n]\n" if self.count else "[]\n")
        self.stream.flush()


def make_writer(config: Config, stdout: IO[str] | None = None) -> _WriterBase:
    stream = stdout if stdout is not None else sys.stdout
    if config.output_format == "json":
        return JsonWriter(stream)
    return TableWriter(stream)


def side_channel(config: Config, stdout: IO[str] | None = None, stderr: IO[str] | None = None) -> IO[str]:
    """Where headers, warnings and the summary go.

    In JSON mode that is stderr, so stdout stays a pipeable array; in table mode
    it is stdout alongside the listing.
    """
    if config.output_format == "json":
        return stderr if stderr is not None else sys.stderr
    return stdout if stdout is not None else sys.stdout


def print_header(config: Config, report: Any, stream: IO[str]) -> None:
    """Effective settings and KG scale. Contains no credential (Section 10)."""
    print("Knowledge Graph Read Harness — Bridge Station 1 (Read)", file=stream)
    print("=" * 72, file=stream)
    width = max(len(label) for label, _ in config.describe())
    for label, value in config.describe():
        print(f"  {label:<{width}}  {value}", file=stream)
    if report is not None:
        print(
            f"  {'entities':<{width}}  {report.entity_count}",
            file=stream,
        )
        print(
            f"  {'relations':<{width}}  {report.relation_count} total, "
            f"{report.matching_relation_count} of type {config.relation_type}",
            file=stream,
        )
    print("=" * 72, file=stream)
    print("", file=stream)


def print_warnings(warnings: Iterable[str], stream: IO[str]) -> None:
    for warning in warnings:
        print(f"warning: {warning}", file=stream)


def print_summary(summary: Summary, stream: IO[str], skipped: int = 0) -> None:
    """The type-pair table and grand total (FR-7)."""
    g = glyphs(stream)
    rows = summary.rows()
    source_width = max([len("SOURCE TYPE")] + [len(r[0]) for r in rows])
    target_width = max([len("TARGET TYPE")] + [len(r[1]) for r in rows])
    count_width = max([len("COUNT")] + [len(str(r[2])) for r in rows])

    print("", file=stream)
    print("Summary — bundles by entity-type pair", file=stream)
    print("-" * 72, file=stream)
    print(
        f"  {'SOURCE TYPE':<{source_width}}  {g['to']}  "
        f"{'TARGET TYPE':<{target_width}}  {'COUNT':>{count_width}}",
        file=stream,
    )
    for source, target, count in rows:
        print(
            f"  {source:<{source_width}}  {g['to']}  "
            f"{target:<{target_width}}  {count:>{count_width}}",
            file=stream,
        )
    print("-" * 72, file=stream)
    label_width = source_width + target_width + len(g["to"]) + 4
    print(f"  {'TOTAL':<{label_width}}  {summary.total:>{count_width}}", file=stream)
    if skipped:
        print(
            f"  {'skipped (dangling endpoints)':<{label_width}}  {skipped:>{count_width}}",
            file=stream,
        )
    stream.flush()
