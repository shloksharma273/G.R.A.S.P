#!/usr/bin/env python3
"""Launcher for task decomposition — a compound command into one ordered run.

Read-only over the PlanGraph.
"""

import sys

from task_decomposition.cli import main

if __name__ == "__main__":
    sys.exit(main())
