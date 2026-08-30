#!/usr/bin/env python3
"""Offline self-check for Station 2: classify the in-memory chai bundles.

Runs the real Station 1 read and the real Station 2 classifier with no ArangoDB
present, so the acceptance criteria of PRD Section 13 can be inspected directly:
SKILL->PRIMITIVE stamps as decomposes_to, PRIMITIVE->STATE defers, and
conservation holds. Accepts the same --format / --no-listing flags as the CLI.

    python demo_classify_offline.py
    python demo_classify_offline.py --format json
"""

from __future__ import annotations

import sys

from rule_preclassifier.cli import build_parser, run
from tests.station2_fixture import chai_bundles


def main() -> int:
    args = build_parser().parse_args()
    return run(
        bundles=chai_bundles(),
        output_format=args.format,
        listing=not args.no_listing,
    )


if __name__ == "__main__":
    sys.exit(main())
