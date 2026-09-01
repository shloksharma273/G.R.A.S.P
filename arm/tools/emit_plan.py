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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("command", nargs="?", help='e.g. "make a magic sequence"')
    ap.add_argument("-o", "--out", type=Path, help="write plan.json here (default: stdout)")
    ap.add_argument("--list", action="store_true", help="list the skills and exit")
    args = ap.parse_args()

    paths = sorted(RULEBOOKS.glob("rulebook_*.md"))
    if not paths:
        print(f"no rulebooks in {RULEBOOKS}", file=sys.stderr)
        return 1
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

    config = load_planner_config(ENV, use_llm=False)
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
