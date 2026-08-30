#!/usr/bin/env python3
"""Offline self-check: run the harness against an in-memory chai KG.

Exercises the real config, validation, read and output code with no ArangoDB
present, so the expected terminal shape can be inspected before a live run.
Set OUTPUT_FORMAT=json to see the pipeable form.

    python demo_chai_offline.py
"""

from __future__ import annotations

import os
import sys

from kg_read_harness.cli import run
from kg_read_harness.config import load_config
from kg_read_harness.errors import HarnessError
from tests import chai_fixture as chai
from tests.fake_arango import make_db


def main() -> int:
    env = chai.env(
        **{
            key: value
            for key, value in os.environ.items()
            if key in ("OUTPUT_FORMAT", "LIMIT", "ENTITY_TYPE_FILTER", "RELATION_TYPE")
        }
    )
    config = load_config(env)
    db = make_db(chai.entities(), chai.relations())
    try:
        return run(config, db=db)
    except HarnessError as error:
        print(error.render(), file=sys.stderr)
        return error.exit_code


if __name__ == "__main__":
    sys.exit(main())
