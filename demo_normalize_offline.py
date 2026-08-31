#!/usr/bin/env python3
"""Offline self-check for Station 4: no ArangoDB, no API key, no network.

Runs Station 2 against the in-memory chai KG and supplies Station 3's
requires/produces labels from the chai answer key, so Station 4 is exercised on a
complete input rather than on however much Station 3's pre-pass happened to
settle. Then normalizes, chains, and prints the plan the graph implies.

    python demo_normalize_offline.py
    python demo_normalize_offline.py --no-listing
"""

from __future__ import annotations

import sys

from direction_normalizer import normalize, ordering_graph, print_report
from tests.station4_fixture import chai_stamped, violated_constraints


def main() -> int:
    argv = sys.argv[1:]
    edges, _classified = chai_stamped()
    result = normalize(edges)

    print_report(result, listing="--no-listing" not in argv)

    order = ordering_graph(result).topological_order()
    if order is None:
        print("\nFAIL: the ordering graph is cyclic")
        return 1

    violations = violated_constraints(order)
    print("")
    print(
        f"The rulebook states {result.overlap()['explicit_precedes']} ordering edge(s); "
        f"state chaining derived {len(result.derived)}."
    )
    if violations:
        for earlier, later in violations:
            print(f"  WRONG ORDER: {earlier} should come before {later}")
        return 1
    print("Every ordering constraint in the rulebook is respected by the derived plan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
