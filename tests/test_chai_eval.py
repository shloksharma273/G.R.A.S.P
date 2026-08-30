"""The chai accuracy eval (PRD Section 13, criterion 1) — offline, no network.

The LLM path cannot be scored offline without inventing its answers, so what this
measures is the part that is genuinely deterministic: the lexical pre-pass, run
over the real chai descriptions with the request cap at 0 so no call can happen.
Precision must be perfect — a wrongly stamped precondition corrupts the plan —
and coverage must clear the stated target.

`eval_chai_live.py` scores the LLM path against the same answer key.
"""

from __future__ import annotations

import unittest

from llm_disambiguator import disambiguate
from rule_preclassifier import classify

from . import fake_llm
from .chai_answer_key import ANSWER_KEY, COVERAGE_TARGET, PRECISION_TARGET, expected_label, score
from .station2_fixture import chai_bundles


def deferred_chai():
    return [item.bundle for item in classify(chai_bundles()).deferred]


def run_offline(**overrides):
    """The station with the LLM unreachable: pre-pass and cache only."""
    config = fake_llm.config(LLM_MAX_REQUESTS="0", **overrides)
    provider = fake_llm.FakeProvider(config, fake_llm.failing("no call may happen in this eval"))
    return disambiguate(deferred_chai(), config, provider=provider), provider


class AnswerKeyTests(unittest.TestCase):
    def test_key_covers_every_deferred_chai_pair(self):
        missing = [
            (b.source.name, b.target.name)
            for b in deferred_chai()
            if expected_label(b.source.name, b.target.name) is None
        ]
        self.assertEqual(missing, [], "the answer key does not cover every deferred pair")

    def test_key_has_both_labels(self):
        self.assertEqual(set(ANSWER_KEY.values()), {"requires", "produces"})


class OfflineEvalTests(unittest.TestCase):
    """Section 13, criterion 1, on the deterministic half of the pipeline."""

    def setUp(self):
        self.result, self.provider = run_offline()
        self.report = score(self.result)

    def test_no_llm_call_was_made(self):
        self.assertEqual(self.provider.requests_made, 0)

    def test_precision_meets_the_target(self):
        self.assertGreaterEqual(
            self.report["precision"],
            PRECISION_TARGET,
            f"wrongly stamped: {self.report['mistakes']}",
        )

    def test_nothing_is_stamped_with_the_wrong_label(self):
        self.assertEqual(self.report["wrong"], 0, self.report["mistakes"])

    def test_coverage_meets_the_target(self):
        self.assertGreaterEqual(self.report["coverage"], COVERAGE_TARGET)

    def test_both_labels_are_produced(self):
        self.assertEqual(set(self.result.counts_by_label()), {"requires", "produces"})

    def test_the_prd_worked_examples(self):
        """Section 13 names three explicitly."""
        stamped = {
            (edge.orientation.head, edge.orientation.tail): edge.edge_type
            for edge in self.result.stamped
        }
        self.assertEqual(stamped[("add_water", "pan_on_stove")], "requires")
        self.assertEqual(stamped[("add_water", "water_in_pan")], "produces")
        self.assertEqual(stamped[("boil_water", "water_boiling")], "produces")

    def test_conservation_holds(self):
        self.assertEqual(self.result.total_input, self.result.total_output)

    def test_anything_unresolved_is_parked_not_guessed(self):
        for item in self.result.parked:
            self.assertIn(item.reason_code, ("service_error", "empty_description"))


class PrepassDisabledTests(unittest.TestCase):
    """With the pre-pass off and no LLM, everything must park — never guess."""

    def test_nothing_is_stamped(self):
        result, provider = run_offline(LLM_LEXICAL_PREPASS="0")
        self.assertEqual(provider.requests_made, 0)
        self.assertEqual(result.stamped, [])
        self.assertEqual(len(result.parked), result.total_input)


if __name__ == "__main__":
    unittest.main()
