#!/usr/bin/env python3
"""Launcher for Layer 2 — natural-language command to plan.json.

Read-only over the PlanGraph.
"""

import sys

from layer2_planning.cli import main

if __name__ == "__main__":
    sys.exit(main())
