#!/usr/bin/env python3
"""Launcher for Bridge Station 5 — the PlanGraph Writer.

This is the only command in the project that writes to the database. A plain run
is a dry run; pass --write to apply.
"""

import sys

from plangraph_writer.cli import main

if __name__ == "__main__":
    sys.exit(main())
