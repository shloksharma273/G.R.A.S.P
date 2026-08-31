#!/usr/bin/env python3
"""Offline self-check for Station 5: no real database, no network.

Runs Stations 2-4 against the in-memory chai KG (with Station 3's labels from the
answer key), then writes the PlanGraph into a writable in-memory ArangoDB double
and reads it back. The double is seeded with the other projects' collections that
share the live pilot database and raises if any of them is touched, so this also
exercises the isolation guarantee.

    python demo_write_offline.py
"""

from __future__ import annotations

import sys

from direction_normalizer import normalize
from plangraph_writer import build, print_report, write_plangraph
from plangraph_writer.config import load_writer_config
from plangraph_writer.readback import read_subgraph
from tests.fake_writable_arango import FOREIGN_COLLECTIONS, make_db
from tests.station4_fixture import chai_stamped, violated_constraints

ENV = {
    "ARANGO_URL": "http://offline.invalid:8529",
    "ARANGO_DB": "test_shlok",
    "ARANGO_USERNAME": "root",
    "ARANGO_PASSWORD": "",
    "PROJECT_NAME": "roboticsPlanner",
}


def main() -> int:
    config = load_writer_config(ENV, dry_run=False)
    station4 = normalize(chai_stamped()[0])
    plan = build(station4.finalized, station4.derived, config.schema)

    db = make_db()
    report = write_plangraph(plan, db, config)
    print_report(config, plan, report, listing="--no-listing" not in sys.argv[1:])

    # Re-run: a scoped rebuild must not accumulate anything.
    again = write_plangraph(plan, db, config)
    print("")
    print(f"Re-run: purged {again.purged_total}, wrote {again.written_total}, "
          f"verified identical: {again.verified == report.verified}")

    subgraph = read_subgraph(db, config.schema, plan.skill_scope)
    order = subgraph.topological_order()
    print(f"Read back: skill={subgraph.skill}, {len(subgraph.primitives)} primitives, "
          f"{len(subgraph.precedes)} precedes edges")
    print(f"Precondition-closed: {subgraph.is_precondition_closed()}")
    print(f"Plan: {' -> '.join(order)}")

    problems = violated_constraints(order)
    untouched = all(len(db.collection(name).documents) == 1 for name in FOREIGN_COLLECTIONS)
    print(f"Other projects' collections untouched: {untouched}")
    if problems:
        for earlier, later in problems:
            print(f"  WRONG ORDER: {earlier} should come before {later}")
    return 0 if not problems and untouched and again.verified == report.verified else 1


if __name__ == "__main__":
    sys.exit(main())
