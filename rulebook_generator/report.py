"""Terminal rendering of a generation run."""

from __future__ import annotations

import json
import sys
from typing import IO

from kg_read_harness.output import glyphs

from .config import GeneratorConfig
from .pipeline import GenerationResult
from .schema import ACCEPT, FLAG

RULE_WIDTH = 72

def _verdict_line(verdict: str, reason: str, dash: str) -> str:
    if verdict == ACCEPT:
        return f"ACCEPT   {dash} passed every check; safe to ingest"
    if verdict == FLAG:
        return f"FLAG     {dash} usable, but a human should look before this is trusted"
    return f"REJECT   {dash} {reason}"


def print_report(
    config: GeneratorConfig, result: GenerationResult, stream: IO[str] | None = None,
    show_markdown: bool = False,
) -> None:
    stream = stream if stream is not None else sys.stdout
    g = glyphs(stream)

    manual = bool(result.transcript and result.transcript.source == "manual")
    print(
        f"Rulebook Generator {g['em']} "
        + ("documentation to rulebook" if manual else "video to rulebook"),
        file=stream,
    )
    print("=" * RULE_WIDTH, file=stream)
    width = max(len(label) for label, _ in config.describe())
    for label, value in config.describe():
        print(f"  {label:<{width}}  {value}", file=stream)
    if result.transcript:
        source_label = "document" if manual else "video"
        body_label = "text" if manual else "transcript"
        print(f"  {source_label:<{width}}  {result.transcript.url or result.transcript.video_id}",
              file=stream)
        print(f"  {body_label:<{width}}  {result.transcript.words} words"
              + ("  (truncated)" if result.truncated else ""), file=stream)
    print("=" * RULE_WIDTH, file=stream)

    if result.rulebook is not None:
        book = result.rulebook
        print("", file=stream)
        print(f"Extracted {g['em']} {book.skill}", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        print(f"  primitives  {len(book.primitives)}", file=stream)
        print(f"  states      {len(book.states)}", file=stream)
        print(f"  objects     {len(book.objects)}", file=stream)
        gloss = (
            "   (mostly stated - read off the documentation)"
            if manual
            else "   (mostly inferred - the transcript rarely states these)"
        )
        print(f"  requires    {sum(len(p.requires) for p in book.primitives)}" + gloss,
              file=stream)
        print(f"  produces    {sum(len(p.produces) for p in book.primitives)}", file=stream)
        if result.from_cache:
            print("  source      cached (no model call)", file=stream)
        elif result.reprompted:
            print("  source      model, after one reprompt", file=stream)

    report = result.report
    if report is not None:
        print("", file=stream)
        print(f"Validation gate {g['em']} {report.verdict}", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        if not report.issues:
            print("  no issues: DAG, no orphan preconditions, valid topological order",
                  file=stream)
        for issue in report.issues:
            mark = "FATAL" if issue.fatal else "flag "
            print(f"  [{mark}] {issue.code}", file=stream)
            print(f"          {issue.detail}", file=stream)

        if report.plan:
            print("", file=stream)
            print(f"Plan the rulebook implies {g['em']} {len(report.plan)} steps", file=stream)
            print("-" * RULE_WIDTH, file=stream)
            for position, step in enumerate(report.plan, start=1):
                print(f"  {position:>2}. {step}", file=stream)

    print("", file=stream)
    print("=" * RULE_WIDTH, file=stream)
    print("  " + _verdict_line(result.verdict, result.reason, g["em"]), file=stream)
    if result.verdict == FLAG:
        print(f"  {result.reason}", file=stream)
    if result.written_to:
        print(f"  written to {result.written_to}", file=stream)
    if result.ingest_detail:
        print(f"  ingest: {result.ingest_detail}", file=stream)

    if show_markdown and result.markdown:
        print("", file=stream)
        print("-" * RULE_WIDTH, file=stream)
        print(result.markdown, file=stream)
    stream.flush()


def dump_json(result: GenerationResult, stream: IO[str] | None = None) -> None:
    stream = stream if stream is not None else sys.stdout
    json.dump(result.to_dict(), stream, indent=2, ensure_ascii=False)
    stream.write("\n")
    stream.flush()
