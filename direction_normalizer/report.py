"""Station 4's run summary (FR-8).

Counts of finalized, derived and parked, plus the derived-vs-explicit overlap —
which is the number worth reading, because it says how much of the execution
order the rulebook actually stated versus how much the graph worked out.
"""

from __future__ import annotations

import json
import sys
from typing import IO

from kg_read_harness.output import glyphs
from rule_preclassifier.table import GROUP_MEANING, group_of

from .model import NormalizationResult
from .normalizer import ordering_graph

RULE_WIDTH = 72


def print_header(result: NormalizationResult, stream: IO[str]) -> None:
    g = glyphs(stream)
    print(
        f"Direction Normalizer {g['em']} Bridge Station 4 (canonical direction + ordering)",
        file=stream,
    )
    print("=" * RULE_WIDTH, file=stream)
    print(f"  stamped edges in      {result.total_input}", file=stream)
    print(f"  flipped to canonical  {result.flipped_count()}", file=stream)
    print(f"  derived by chaining   {len(result.derived)}", file=stream)
    print("=" * RULE_WIDTH, file=stream)


def print_listing(result: NormalizationResult, stream: IO[str]) -> None:
    g = glyphs(stream)
    print("", file=stream)
    print(f"Derived ordering {g['em']} state chaining", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    for index, edge in enumerate(result.derived, start=1):
        mark = " (confirms explicit)" if edge.confirms_explicit else ""
        print(f"[{index:>4}] {edge.head} {g['arrow']} {edge.tail}{mark}", file=stream)
        print(
            f"       {g['turn']} via {edge.via_state}  conf={edge.confidence:.2f}", file=stream
        )

    if result.finalized:
        print("", file=stream)
        print(f"Finalized edges {g['em']} canonical direction", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        for index, edge in enumerate(result.finalized, start=1):
            flag = "  [flipped]" if edge.flipped else ""
            print(
                f"[{index:>4}] {edge.edge_type:<14} {edge.head} {g['arrow']} {edge.tail}{flag}",
                file=stream,
            )

    if result.parked:
        print("", file=stream)
        print(f"Parked {g['em']} quality review", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        for item in result.parked:
            print(
                f"       {item.bundle.source.name} {g['arrow']} {item.bundle.target.name}"
                f"  {item.reason_code} [{item.group}]",
                file=stream,
            )
            print(f"       {g['turn']} {item.detail}", file=stream)


def print_summary(result: NormalizationResult, stream: IO[str]) -> None:
    g = glyphs(stream)

    print("", file=stream)
    print(f"Summary {g['em']} streams", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    print(
        f"  {'finalized':<10} {len(result.finalized):>5}   {g['to']} Station 5 (write)",
        file=stream,
    )
    print(
        f"  {'derived':<10} {len(result.derived):>5}   {g['to']} Station 5 (new precedes edges)",
        file=stream,
    )
    print(f"  {'parked':<10} {len(result.parked):>5}   {g['to']} quality review", file=stream)

    types = result.counts_by_edge_type()
    if types:
        print("", file=stream)
        print(f"Summary {g['em']} edge types", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        width = max(len(name) for name in types)
        for name, count in sorted(types.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {name:<{width}} {count:>5}", file=stream)

    methods = result.counts_by_direction_method()
    if methods:
        print("", file=stream)
        print(f"Summary {g['em']} how direction was decided", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        width = max(len(name) for name in methods)
        for name, count in sorted(methods.items(), key=lambda kv: (-kv[1], kv[0])):
            print(f"  {name:<{width}} {count:>5}", file=stream)

    overlap = result.overlap()
    print("", file=stream)
    print(f"Summary {g['em']} derived vs. explicit ordering", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    print(f"  {'derived total':<28} {overlap['derived_total']:>5}", file=stream)
    print(f"  {'confirming an explicit edge':<28} {overlap['derived_confirming_explicit']:>5}", file=stream)
    print(f"  {'new (never written down)':<28} {overlap['derived_new']:>5}", file=stream)
    print(f"  {'explicit precedes edges':<28} {overlap['explicit_precedes']:>5}", file=stream)
    print(f"  {'  of those, unsupported':<28} {overlap['explicit_unsupported']:>5}", file=stream)

    reasons = result.counts_by_reason_code()
    if reasons:
        print("", file=stream)
        print(f"Summary {g['em']} parked by reason code", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        width = max(len(name) for name in reasons)
        for name, count in sorted(reasons.items(), key=lambda kv: (-kv[1], kv[0])):
            group = group_of(name)
            print(f"  {name:<{width}} {count:>5}   [{group}] {GROUP_MEANING[group]}", file=stream)

    if result.self_chains or result.refused_derived:
        print("", file=stream)
        print(f"Summary {g['em']} skipped", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        for primitive, state in result.self_chains:
            print(f"  self chain: {primitive} both produces and requires {state}", file=stream)
        for edge in result.refused_derived:
            print(f"  refused (would cycle): {edge.head} {g['to']} {edge.tail}", file=stream)

    print("", file=stream)
    print(
        f"  conservation: {result.total_input} in {g['to']} {result.total_output} out  (holds)"
        f"  +{len(result.derived)} derived",
        file=stream,
    )
    stream.flush()


def print_plan(result: NormalizationResult, stream: IO[str]) -> None:
    """The topological order of the ordering graph (acceptance criterion 2).

    A verification view, not the planner: Layer 2 owns real planning. But if the
    chaining is right, this reads as the recipe.
    """
    g = glyphs(stream)
    order = ordering_graph(result).topological_order()
    print("", file=stream)
    print(f"Topological order {g['em']} the plan the graph implies", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    if order is None:
        print("  the ordering graph is cyclic; no valid sequence exists", file=stream)
        return
    for position, name in enumerate(order, start=1):
        print(f"  {position:>3}. {name}", file=stream)
    stream.flush()


def print_report(
    result: NormalizationResult,
    stream: IO[str] | None = None,
    listing: bool = True,
    plan: bool = True,
) -> None:
    stream = stream if stream is not None else sys.stdout
    print_header(result, stream)
    if listing:
        print_listing(result, stream)
    print_summary(result, stream)
    if plan:
        print_plan(result, stream)


def dump_json(result: NormalizationResult, stream: IO[str] | None = None) -> None:
    stream = stream if stream is not None else sys.stdout
    payload = result.to_dict()
    payload["topological_order"] = ordering_graph(result).topological_order()
    json.dump(payload, stream, indent=2, ensure_ascii=False)
    stream.write("\n")
    stream.flush()
