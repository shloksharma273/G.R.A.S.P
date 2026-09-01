#!/usr/bin/env python3
"""Run G.R.A.S.P over the block rulebooks and emit plan.json.

Deliberately separate from the plan bridge. The bridge consumes the *contract*
(plan.json), never the graph or the database -- so it has no dependency on
G.R.A.S.P internals and can be tested against a hand-written plan.

Ingestion here uses G.R.A.S.P's own offline test fixture in place of AutoGraph
(the same path demo_plan_offline.py uses): rulebook markdown -> Station 1
bundles -> Stations 2-5 -> an in-memory PlanGraph -> Layer 2. No ArangoDB, no
network, no LLM -- the step order is graph-derived.

The block rulebooks live in this package rather than in dataset/, so the shared
corpus and its tests are untouched.

    ./tools/emit_plan.py "make a magic sequence" -o /tmp/plan.json
    ./tools/emit_plan.py --list
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARM = HERE.parent
REPO = ARM.parent                      # the G.R.A.S.P repo root
RULEBOOKS = ARM / "grasp_arm_bringup" / "rulebooks"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

from graspenv import DEMO_PREFIX, describe, live_env            # noqa: E402
from kg_read_harness.client import connect                      # noqa: E402
from layer2_planning import load_planner_config, plan_command   # noqa: E402
from layer2_planning.plan import Clarification                  # noqa: E402
from plangraph_writer.config import load_writer_config          # noqa: E402
from tests.corpus_fixture import ENV, plangraph_for             # noqa: E402
from tests.fake_writable_arango import make_db                  # noqa: E402
from tests.rulebook_fixture import parse                        # noqa: E402


def build_db(paths):
    """Every block rulebook in one in-memory PlanGraph, each in its own scope."""
    config = load_writer_config(ENV, dry_run=False)
    db = make_db()
    books = []
    for path in paths:
        book = parse(path)
        plangraph_for(book, db=db, config=config)
        books.append(book)
    return db, books


def live_db(env):
    """Open the real Arango named in .env."""
    from kg_read_harness.config import load_config
    return connect(load_config(env))


def write_live(paths, env, dry_run):
    """Stations 2-5 for each rulebook, into the real database.

    Writes under PLANGRAPH_PREFIX (blocksDemo_*), NOT the pilot's PROJECT_NAME
    prefix -- test_shlok is shared with other projects, and plannerTest_* is not
    ours to add skills to.

    Station 2 is the real rule preclassifier and Stations 4-5 are the real
    normaliser and writer. Station 3 is still bypassed: the edges it would
    adjudicate take their labels from each rulebook's answer key, exactly as in
    the offline path. The database is real; the extraction is not.
    """
    from direction_normalizer import normalize
    from plangraph_writer import build, write_plangraph
    from tests.corpus_fixture import stamped_edges

    config = load_writer_config(env, dry_run=dry_run)
    print("DRY RUN -- nothing will be written" if dry_run else "WRITING")
    for label, value in config.describe():
        print(f"  {label:22s} {value}")

    db = live_db(env)
    print()
    for path in paths:
        book = parse(path)
        edges, _ = stamped_edges(book)
        result = normalize(edges)
        plan = build(result.finalized, result.derived, config.schema,
                     skill_scope=book.skill)
        report = write_plangraph(plan, db, config)
        print(f"  {book.skill}")
        if report.schema_created:
            print(f"    collections created : {', '.join(report.schema_created)}")
        if report.graph_created:
            print(f"    named graph created : {config.schema.graph_name}")
        if report.purged_total:
            print(f"    purged (re-run)     : {report.purged_total} docs "
                  f"in this skill's scope")
        print(f"    written             : {report.written_total} docs  {dict(report.written)}")
        if report.verified:
            print(f"    verified in db      : {dict(report.verified)}")
        print(f"    vector index        : {report.vector_index}"
              + (f" ({report.vector_index_detail})" if report.vector_index_detail else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", help='e.g. "make a magic sequence"')
    ap.add_argument("-o", "--out", type=Path, help="write plan.json here (default: stdout)")
    ap.add_argument("--list", action="store_true", help="list the skills and exit")
    ap.add_argument("--live", action="store_true",
                    help="plan against the real Arango named in .env")
    ap.add_argument("--write", action="store_true",
                    help="with --live: write the rulebooks' PlanGraphs first")
    ap.add_argument("--dry-run", action="store_true",
                    help="with --write: report what would be written, write nothing")
    ap.add_argument("--prefix", default=DEMO_PREFIX,
                    help=f"PlanGraph collection prefix (default {DEMO_PREFIX})")
    args = ap.parse_args()

    paths = sorted(RULEBOOKS.glob("rulebook_*.md"))
    if not paths:
        print(f"no rulebooks in {RULEBOOKS}", file=sys.stderr)
        return 1
    if args.live:
        env = live_env(REPO, prefix=args.prefix)
        print(f"live: {describe(env)}")
        if args.write:
            write_live(paths, env, dry_run=args.dry_run)
            if args.dry_run:
                return 0
        db = live_db(env)
        books = [parse(p) for p in paths]
    else:
        env = ENV
        db, books = build_db(paths)

    if args.list:
        print(f"{len(books)} skill(s) in the PlanGraph:")
        for book in books:
            print(f"  {book.skill}")
            for prim in book.primitives:
                print(f"      {prim}")
        return 0

    if not args.command:
        ap.error("a command is required unless --list")

    config = load_planner_config(env, use_llm=False)
    result = plan_command(args.command, db, config)

    if isinstance(result, Clarification):
        print(f'no plan for "{args.command}"', file=sys.stderr)
        print(f"  {result.reason.splitlines()[0]}", file=sys.stderr)
        if result.candidates:
            print("  candidates: " +
                  ", ".join(c["skill"] for c in result.candidates), file=sys.stderr)
        # Still emit the clarification, so the bridge can refuse to move on it.
        payload = result.to_dict()
        text = json.dumps(payload, indent=2)
        (args.out.write_text(text + "\n") if args.out else print(text))
        return 2

    payload = result.to_dict()
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    if args.out:
        args.out.write_text(text + "\n")
        print(f"wrote {args.out}")
    else:
        print(text)
    print(f"\ngoal: {result.goal}   ({len(result.steps)} steps, "
          f"match {result.meta.get('match_confidence', float('nan')):.2f})",
          file=sys.stderr)
    for step in result.steps:
        print(f"  {step.order}. {step.action}   uses={step.uses}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
