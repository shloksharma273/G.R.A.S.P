#!/usr/bin/env python3
"""Convenience launcher: `python read_kg.py` == `python -m kg_read_harness`."""

import sys

from kg_read_harness.cli import main

if __name__ == "__main__":
    sys.exit(main())
