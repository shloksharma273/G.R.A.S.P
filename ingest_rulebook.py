#!/usr/bin/env python3
"""Launcher: a rulebook file straight into the PlanGraph, skipping AutoGraph."""

import sys

from rulebook_generator.direct_cli import main

if __name__ == "__main__":
    sys.exit(main())
