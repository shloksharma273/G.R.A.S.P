"""Station 3's run summary (FR-8).

Section 13 requires the summary to report label counts, the lexical/llm method
split, reason codes and mean confidence. The call counters are printed alongside
because they are how the idempotency claim is checked: a warm re-run must show
zero requests.
"""

from __future__ import annotations

import json
import sys
from typing import IO

from kg_read_harness.output import glyphs
from rule_preclassifier.table import GROUP_MEANING, group_of

from .config import LLMConfig
from .model import DisambiguationResult

RULE_WIDTH = 72


def print_header(config: LLMConfig, result: DisambiguationResult, stream: IO[str]) -> None:
    g = glyphs(stream)
    print(f"LLM Disambiguator {g['em']} Bridge Station 3 (requires vs. produces)", file=stream)
    print("=" * RULE_WIDTH, file=stream)
    width = max(len(label) for label, _ in config.describe())
    for label, value in config.describe():
        print(f"  {label:<{width}}  {value}", file=stream)
    print(f"  {'deferred in':<{width}}  {result.total_input}", file=stream)
    print("=" * RULE_WIDTH, file=stream)


def print_listing(result: DisambiguationResult, stream: IO[str]) -> None:
    g = glyphs(stream)
    print("", file=stream)
    index = 0
    for edge in result.stamped:
        index += 1
        source = f"{edge.orientation.head or edge.bundle.source.name}"
        target = f"{edge.orientation.tail or edge.bundle.target.name}"
        marker = " (cached)" if edge.from_cache else ""
        print(f"[{index:>4}] {edge.edge_type.upper():<9} {source} {g['arrow']} {target}", file=stream)
        print(
            f"       {'':<9} {g['turn']} {edge.method} conf={edge.confidence:.2f}{marker}"
            + (f"  {g['em']} {edge.rationale}" if edge.rationale else ""),
            file=stream,
        )
    for item in result.parked:
        index += 1
        print(
            f"[{index:>4}] {'PARK':<9} {item.bundle.source.name} {g['arrow']} "
            f"{item.bundle.target.name}",
            file=stream,
        )
        print(
            f"       {'':<9} {g['turn']} {item.reason_code} [{item.group}] {g['em']} {item.detail}",
            file=stream,
        )


def print_summary(result: DisambiguationResult, stream: IO[str]) -> None:
    g = glyphs(stream)

    print("", file=stream)
    print(f"Summary {g['em']} buckets", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    print(
        f"  {'stamped':<10} {len(result.stamped):>5}   {g['to']} Station 4 (normalize direction)",
        file=stream,
    )
    print(f"  {'parked':<10} {len(result.parked):>5}   {g['to']} quality review", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    print(f"  {'TOTAL':<10} {result.total_output:>5}", file=stream)

    labels = result.counts_by_label()
    if labels:
        print("", file=stream)
        print(f"Summary {g['em']} labels", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        for name, count in sorted(labels.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {name:<12} {count:>5}", file=stream)

        methods = result.counts_by_method()
        print("", file=stream)
        print(f"Summary {g['em']} method", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        for name, count in sorted(methods.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {name:<12} {count:>5}", file=stream)
        mean = result.mean_confidence()
        print(f"  {'mean conf.':<12} {mean:>5.3f}" if mean is not None else "", file=stream)

    reasons = result.counts_by_reason_code()
    if reasons:
        print("", file=stream)
        print(f"Summary {g['em']} parked by reason code", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        width = max(len(name) for name in reasons)
        for name, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
            group = group_of(name)
            print(f"  {name:<{width}} {count:>5}   [{group}] {GROUP_MEANING[group]}", file=stream)

    calls = result.calls
    print("", file=stream)
    print(f"Summary {g['em']} LLM usage", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    print(f"  {'requests':<14} {calls.requests:>5}", file=stream)
    print(f"  {'retries':<14} {calls.retries:>5}", file=stream)
    print(f"  {'reprompts':<14} {calls.reprompts:>5}", file=stream)
    print(f"  {'items sent':<14} {calls.items_sent:>5}", file=stream)
    print(f"  {'cache hits':<14} {calls.cache_hits:>5}", file=stream)
    print(f"  {'cache misses':<14} {calls.cache_misses:>5}", file=stream)

    print("", file=stream)
    print(
        f"  conservation: {result.total_input} in {g['to']} {result.total_output} out  (holds)",
        file=stream,
    )
    stream.flush()


def print_report(
    config: LLMConfig,
    result: DisambiguationResult,
    stream: IO[str] | None = None,
    listing: bool = True,
) -> None:
    stream = stream if stream is not None else sys.stdout
    print_header(config, result, stream)
    if listing:
        print_listing(result, stream)
    print_summary(result, stream)


def dump_json(result: DisambiguationResult, stream: IO[str] | None = None) -> None:
    stream = stream if stream is not None else sys.stdout
    json.dump(result.to_dict(), stream, indent=2, ensure_ascii=False)
    stream.write("\n")
    stream.flush()
