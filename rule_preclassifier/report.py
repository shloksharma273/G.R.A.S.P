"""The run summary (FR-7) — counts by edge type, bucket and reason code.

The only side effect Station 2 is allowed: writing this to a stream. Section 9's
"reading the pile" guidance is printed alongside the group counts, because the
distribution is the point — it is what tells the developer whether to fix the
ontology, the rulebook, or the source data.
"""

from __future__ import annotations

import json
import sys
from typing import IO

from kg_read_harness.output import glyphs

from .model import BUCKET_DEFERRED, BUCKET_PARKED, BUCKET_STAMPED, ClassificationResult
from .table import GROUP_MEANING, group_of

RULE_WIDTH = 72

_BUCKET_DESTINATION = {
    BUCKET_STAMPED: "-> Station 4 (normalize direction)",
    BUCKET_DEFERRED: "-> Station 3 (LLM disambiguate)",
    BUCKET_PARKED: "-> quality review",
}


def print_header(result: ClassificationResult, stream: IO[str]) -> None:
    g = glyphs(stream)
    print(
        f"Rule Pre-Classifier {g['em']} Bridge Station 2 (deterministic edge typing)",
        file=stream,
    )
    print("=" * RULE_WIDTH, file=stream)
    print(f"  bundles in            {result.total_input}", file=stream)
    for rewrite, count in sorted(result.normalized_types.items()):
        print(f"  type normalized       {rewrite}  ({count} endpoint(s))", file=stream)
    print("=" * RULE_WIDTH, file=stream)


def print_listing(result: ClassificationResult, stream: IO[str]) -> None:
    """One line per annotated record, grouped by bucket."""
    g = glyphs(stream)
    print("", file=stream)
    index = 0
    for edge in result.stamped:
        index += 1
        note = edge.edge_type
        if edge.orientation.is_resolved:
            note += f"  [{edge.orientation.head} {g['to']} {edge.orientation.tail}]"
            if edge.orientation.reversed_from_input:
                note += " (reversed)"
        else:
            note += "  [direction deferred]"
        _line(stream, index, "STAMP ", edge.bundle, note, g)
    for item in result.deferred:
        index += 1
        _line(stream, index, "DEFER ", item.bundle, " / ".join(item.candidate_edge_types) + " ?", g)
    for item in result.parked:
        index += 1
        _line(stream, index, "PARK  ", item.bundle, f"{item.reason_code} [{item.group}]", g)


def _line(stream: IO[str], index: int, tag: str, bundle, note: str, g: dict[str, str]) -> None:
    print(
        f"[{index:>4}] {tag} {bundle.source.name} ({bundle.source.type}) {g['arrow']} "
        f"{bundle.target.name} ({bundle.target.type})",
        file=stream,
    )
    print(f"       {' ' * len(tag)} {g['turn']} {note}", file=stream)


def print_summary(result: ClassificationResult, stream: IO[str]) -> None:
    """Counts per bucket, per edge type and per reason code (FR-7)."""
    g = glyphs(stream)

    print("", file=stream)
    print(f"Summary {g['em']} buckets", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    for bucket, count in result.counts_by_bucket().items():
        print(f"  {bucket:<10} {count:>5}   {_BUCKET_DESTINATION[bucket]}", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    print(f"  {'TOTAL':<10} {result.total_output:>5}", file=stream)

    edge_types = result.counts_by_edge_type()
    if edge_types:
        print("", file=stream)
        print(f"Summary {g['em']} edge types", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        width = max(len(name) for name in edge_types)
        for name, count in sorted(edge_types.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {name:<{width}} {count:>5}", file=stream)

    reasons = result.counts_by_reason_code()
    if reasons:
        print("", file=stream)
        print(f"Summary {g['em']} parked by reason code", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        width = max(len(name) for name in reasons)
        for name, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {name:<{width}} {count:>5}   [{group_of(name)}]", file=stream)
        print("", file=stream)
        print("  reading the pile:", file=stream)
        for group, count in sorted(result.counts_by_group().items()):
            print(f"    {group}  {count:>4}   {GROUP_MEANING[group]}", file=stream)

    print("", file=stream)
    print(
        f"  conservation: {result.total_input} in {g['to']} {result.total_output} out  (holds)",
        file=stream,
    )
    stream.flush()


def print_report(result: ClassificationResult, stream: IO[str] | None = None, listing: bool = True) -> None:
    stream = stream if stream is not None else sys.stdout
    print_header(result, stream)
    if listing:
        print_listing(result, stream)
    print_summary(result, stream)


def dump_json(result: ClassificationResult, stream: IO[str] | None = None) -> None:
    stream = stream if stream is not None else sys.stdout
    json.dump(result.to_dict(), stream, indent=2, ensure_ascii=False)
    stream.write("\n")
    stream.flush()
