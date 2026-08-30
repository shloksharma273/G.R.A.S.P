#!/usr/bin/env python3
"""Offline self-check for Station 3: no ArangoDB, no API key, no network.

Runs Stations 1 and 2 against the in-memory chai KG, then Station 3 with the
request cap at 0 — so the lexical pre-pass and the cache do all the work and any
attempt to reach the model would be refused. Scores the result against the chai
answer key.

    python demo_disambiguate_offline.py
"""

from __future__ import annotations

import sys

from llm_disambiguator import disambiguate, print_report
from llm_disambiguator.config import load_llm_config
from rule_preclassifier import classify
from tests.chai_answer_key import PRECISION_TARGET, score
from tests.station2_fixture import chai_bundles


def main() -> int:
    config = load_llm_config(
        {
            "LLM_API_KEY": "offline-demo-no-call-is-made",
            "LLM_MODEL": "offline/lexical-only",
            "LLM_MAX_REQUESTS": "0",  # nothing may be sent
            "LLM_CACHE": "0",
        }
    )
    deferred = [item.bundle for item in classify(chai_bundles()).deferred]
    result = disambiguate(deferred, config)

    print_report(config, result, listing="--no-listing" not in sys.argv[1:])

    report = score(result)
    print("")
    print(f"Accuracy vs. the chai answer key: {report['correct']}/{report['stamped']} stamped "
          f"correct, precision {report['precision']:.0%}, coverage {report['coverage']:.0%}")
    if report["mistakes"]:
        for head, tail, got, expected, method, _why in report["mistakes"]:
            print(f"  WRONG {head} -> {tail}: said {got}, key says {expected} [{method}]")
    return 0 if report["precision"] >= PRECISION_TARGET else 1


if __name__ == "__main__":
    sys.exit(main())
