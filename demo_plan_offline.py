#!/usr/bin/env python3
"""Offline self-check for Layer 2: no database, no network, no API key.

Runs the whole bridge over every rulebook in the corpus into one in-memory
PlanGraph, then plans a natural command against each. The LLM is off, which is
the point: the order below is entirely graph-derived, so if these read as
recipes, the ordering came from the precondition graph and not from a model.

    python demo_plan_offline.py
"""

from __future__ import annotations

import sys

from layer2_planning import load_planner_config, plan_command
from layer2_planning.plan import Clarification
from tests.corpus_fixture import ENV, book_named, corpus_db

COMMANDS = [
    "make me a masala chai",
    "I want a cup of pour over coffee",
    "fold my t-shirt",
    "make the bed",
    "water the houseplants",
    "cook a burger",
    "please reticulate the splines",     # deliberately unrelated
]


def main() -> int:
    db, _writer_config, books = corpus_db()
    config = load_planner_config(ENV, use_llm=False)
    print(f"PlanGraph holds {len(books)} skills: {', '.join(b.skill for b in books)}")
    print(f"Composer: templated (no LLM) - every order below is graph-derived.\n")

    failures = 0
    for command in COMMANDS:
        result = plan_command(command, db, config)
        if isinstance(result, Clarification):
            print(f'"{command}"')
            print(f"   CLARIFY: {result.reason.splitlines()[0]}")
            print(f"   candidates: {', '.join(c['skill'] for c in result.candidates)}\n")
            continue

        print(f'"{command}"  ->  {result.goal}  (match {result.meta["match_confidence"]:.2f})')
        for step in result.steps:
            print(f"   {step.order:>2}. {step.description}")

        where = {s.action: s.order for s in result.steps}
        broken = [
            (a, b)
            for a, b in book_named(result.goal).expected_order_constraints()
            if where[a] > where[b]
        ]
        for a, b in broken:
            print(f"   WRONG ORDER: {a} should precede {b}")
        failures += len(broken)
        print()

    print("Every ordering constraint in every rulebook is respected."
          if not failures else f"{failures} ordering violation(s).")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
