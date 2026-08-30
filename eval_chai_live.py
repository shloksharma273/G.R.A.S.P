#!/usr/bin/env python3
"""Score Station 3 against the chai answer key (PRD Section 13, criterion 1).

Unlike the offline eval in `tests/test_chai_eval.py`, this one actually calls the
model, so it is the measurement of the LLM path. It reports precision over
stamped labels and coverage over the whole deferred set, and prints every
disagreement with the key so a wrong stamp can be read rather than inferred.

    python eval_chai_live.py                    # live KG via Stations 1 + 2
    python eval_chai_live.py --offline          # the in-memory chai KG
    python eval_chai_live.py --no-prepass       # score the model alone

Requires an LLM key in the environment; see .env.example.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys

from llm_disambiguator import disambiguate, load_llm_config
from llm_disambiguator.cache import VerdictCache
from rule_preclassifier import classify
from tests.chai_answer_key import COVERAGE_TARGET, PRECISION_TARGET, score


def deferred_bundles(offline: bool):
    if offline:
        from tests.station2_fixture import chai_bundles

        bundles = chai_bundles()
    else:
        from kg_read_harness.client import connect
        from kg_read_harness.config import load_config
        from kg_read_harness.read import read_relationship_bundles
        from kg_read_harness.validate import validate_kg

        config = load_config()
        db = connect(config)
        validate_kg(db, config)
        bundles = list(read_relationship_bundles(db, config))
    return [item.bundle for item in classify(bundles).deferred]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="use the in-memory chai KG")
    parser.add_argument("--no-prepass", action="store_true", help="disable the lexical pre-pass")
    parser.add_argument("--no-cache", action="store_true", help="force fresh model calls")
    args = parser.parse_args()

    config = load_llm_config()
    if args.no_prepass:
        config = dataclasses.replace(config, lexical_prepass=False)
    if args.no_cache:
        config = dataclasses.replace(config, cache_enabled=False)

    deferred = deferred_bundles(args.offline)
    result = disambiguate(
        deferred, config, cache=VerdictCache(config.cache_path, config.cache_enabled)
    )
    report = score(result)

    print(f"Station 3 accuracy vs. the chai answer key ({config.model})")
    print("=" * 72)
    print(f"  deferred in     {report['total']}")
    print(f"  stamped         {report['stamped']}")
    print(f"  parked          {report['parked']}")
    print(f"  correct         {report['correct']}")
    print(f"  wrong           {report['wrong']}")
    if report["unscored"]:
        print(f"  unscored        {report['unscored']}  (pair not in the answer key)")
    print("-" * 72)
    print(f"  precision       {report['precision']:.1%}   (target {PRECISION_TARGET:.0%})")
    print(f"  coverage        {report['coverage']:.1%}   (target {COVERAGE_TARGET:.0%})")
    print(f"  method split    {dict(result.counts_by_method())}")
    mean = result.mean_confidence()
    print(f"  mean confidence {mean:.3f}" if mean is not None else "  mean confidence   n/a")
    print(f"  llm requests    {result.calls.requests}  (cache hits {result.calls.cache_hits})")

    if report["mistakes"]:
        print("-" * 72)
        print("  disagreements with the key:")
        for head, tail, got, expected, method, why in report["mistakes"]:
            print(f"    {head} -> {tail}: said {got}, key says {expected}  [{method}]")
            if why:
                print(f"      {why}")

    if result.parked:
        print("-" * 72)
        print("  parked:")
        for item in result.parked:
            print(f"    {item.bundle.source.name} -> {item.bundle.target.name}: {item.reason_code}")
            print(f"      {item.detail}")

    passed = (
        report["precision"] >= PRECISION_TARGET and report["coverage"] >= COVERAGE_TARGET
    )
    print("=" * 72)
    print("  PASS" if passed else "  BELOW TARGET")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
