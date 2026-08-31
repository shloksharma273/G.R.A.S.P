"""Station 5's run summary (FR-7): what was planned, what was written, what verified."""

from __future__ import annotations

import json
import sys
from typing import IO

from kg_read_harness.output import glyphs

from .config import WriterConfig
from .embed import STATUS_BUILT, STATUS_NOT_CONFIGURED
from .records import PlanGraphBuild, WriteReport

RULE_WIDTH = 72


def print_header(config: WriterConfig, plan: PlanGraphBuild, stream: IO[str]) -> None:
    g = glyphs(stream)
    print(f"PlanGraph Writer {g['em']} Bridge Station 5 (schema, identity, write)", file=stream)
    print("=" * RULE_WIDTH, file=stream)
    width = max(len(label) for label, _ in config.describe())
    for label, value in config.describe():
        print(f"  {label:<{width}}  {value}", file=stream)
    print(f"  {'resolved scope':<{width}}  {plan.skill_scope}", file=stream)
    print(f"  {'build id':<{width}}  {plan.build_id}", file=stream)
    print("=" * RULE_WIDTH, file=stream)


def print_plan(plan: PlanGraphBuild, stream: IO[str]) -> None:
    g = glyphs(stream)
    print("", file=stream)
    print(f"Plan {g['em']} vertices", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    for entity_type, count in sorted(plan.counts_by_vertex_type().items()):
        print(f"  {entity_type:<12} {count:>5}", file=stream)

    print("", file=stream)
    print(f"Plan {g['em']} edges", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    for edge_type, count in sorted(plan.counts_by_edge_type().items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {edge_type:<16} {count:>5}", file=stream)

    print("", file=stream)
    print(f"Plan {g['em']} provenance", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    for method, count in sorted(plan.counts_by_method().items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {method:<16} {count:>5}", file=stream)

    if plan.rejected:
        print("", file=stream)
        print(f"Plan {g['em']} rejected", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        for edge_type, reason in plan.rejected:
            print(f"  {edge_type}: {reason}", file=stream)


def print_write(report: WriteReport, stream: IO[str]) -> None:
    g = glyphs(stream)

    if report.schema_created or report.graph_created or report.indexes_created:
        print("", file=stream)
        print(f"Schema {g['em']} created", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        for name in report.schema_created:
            print(f"  collection  {name}", file=stream)
        if report.graph_created:
            print("  graph       (named graph created)", file=stream)
        for name in report.indexes_created:
            print(f"  index       {name}", file=stream)

    label = "would purge" if report.dry_run else "purged"
    if report.purged:
        print("", file=stream)
        print(f"Scoped rebuild {g['em']} {label}", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        for collection, count in sorted(report.purged.items()):
            print(f"  {collection:<40} {count:>6}", file=stream)
        print(f"  {'TOTAL':<40} {report.purged_total:>6}", file=stream)

    label = "would write" if report.dry_run else "written"
    print("", file=stream)
    print(f"Documents {g['em']} {label}", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    for collection, count in sorted(report.written.items()):
        print(f"  {collection:<40} {count:>6}", file=stream)
    print(f"  {'TOTAL':<40} {report.written_total:>6}", file=stream)

    print("", file=stream)
    print(f"Verified {g['em']} read back from the database", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    for collection, count in sorted(report.verified.items()):
        if collection == "_graph":
            print(f"  {'named graph exists':<40} {'yes' if count else 'NO':>6}", file=stream)
        else:
            print(f"  {collection:<40} {count:>6}", file=stream)

    print("", file=stream)
    print(f"Vector index {g['em']} {report.vector_index}", file=stream)
    print("-" * RULE_WIDTH, file=stream)
    print(f"  {report.vector_index_detail}", file=stream)

    print("", file=stream)
    if report.dry_run:
        print("  DRY RUN — nothing was created, deleted or written.", file=stream)
    else:
        print(
            f"  write complete"
            + ("  (transactional)" if report.transactional else "  (no transaction support)"),
            file=stream,
        )
    stream.flush()


def print_report(
    config: WriterConfig,
    plan: PlanGraphBuild,
    report: WriteReport,
    stream: IO[str] | None = None,
    listing: bool = True,
) -> None:
    stream = stream if stream is not None else sys.stdout
    print_header(config, plan, stream)
    if listing:
        print_plan(plan, stream)
    print_write(report, stream)


def dump_json(
    plan: PlanGraphBuild, report: WriteReport, stream: IO[str] | None = None
) -> None:
    stream = stream if stream is not None else sys.stdout
    json.dump({"plan": plan.to_dict(), "write": report.to_dict()}, stream, indent=2, ensure_ascii=False)
    stream.write("\n")
    stream.flush()


def exit_code_for(report: WriteReport) -> int:
    """0 on success. A pending vector index is reported, not fatal (Section 11)."""
    if report.verified.get("_graph") == 0 and not report.dry_run:
        return 1
    return 0
