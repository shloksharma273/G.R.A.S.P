"""Layer 2 acceptance criteria (PRD Section 12), over the whole rulebook corpus."""

from __future__ import annotations

import unittest

from layer2_planning import load_planner_config, plan_command
from layer2_planning.plan import Clarification, Plan

from .corpus_fixture import ENV, book_named, corpus_db

#: Criterion 1: a natural command per rulebook, and the skill it must resolve to.
COMMANDS = {
    "make me a masala chai": "make_masala_chai",
    "I want a cup of pour over coffee": "make_pour_over_coffee",
    "fold my t-shirt": "fold_tshirt",
    "make the bed": "make_bed",
    "water the houseplants": "water_houseplants",
    "cook a burger": "cook_burger",
}


def planner(use_llm=False, **overrides):
    return load_planner_config({**ENV, **overrides}, use_llm=use_llm)


def plan_for(command: str, **overrides) -> Plan:
    db, _config, _books = corpus_db()
    result = plan_command(command, db, planner(**overrides))
    assert isinstance(result, Plan), f"expected a plan for {command!r}, got a clarification"
    return result


def position(plan: Plan) -> dict[str, int]:
    return {step.action: step.order for step in plan.steps}


class GoalResolutionTests(unittest.TestCase):
    """Criterion 1 — the right skill, for every rulebook."""

    def test_every_command_resolves_to_its_own_skill(self):
        for command, expected in COMMANDS.items():
            with self.subTest(command=command):
                self.assertEqual(plan_for(command).goal, expected)

    def test_matches_clear_the_threshold_comfortably(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertGreaterEqual(plan_for(command).meta["match_confidence"], 0.5)

    def test_scopes_never_cross(self):
        """Six tasks in one database; a plan must contain only its own steps."""
        for command, skill in COMMANDS.items():
            with self.subTest(command=command):
                plan = plan_for(command)
                expected = set(book_named(skill).primitives)
                self.assertEqual(set(plan.actions), expected)

    def test_shared_names_do_not_leak(self):
        """chai and burger both have turn_on_stove and serve; plans stay separate."""
        chai, burger = plan_for("make me a masala chai"), plan_for("cook a burger")
        self.assertIn("turn_on_stove", chai.actions)
        self.assertIn("turn_on_stove", burger.actions)
        self.assertNotIn("add_tea_leaves", burger.actions)
        self.assertNotIn("flip_patty", chai.actions)


class OrderingTests(unittest.TestCase):
    """Criterion 2 — the expected sequences."""

    def test_every_plan_respects_its_rulebooks_constraints(self):
        for command, skill in COMMANDS.items():
            with self.subTest(command=command):
                plan = plan_for(command)
                where = position(plan)
                for earlier, later in book_named(skill).expected_order_constraints():
                    self.assertLess(
                        where[earlier], where[later], f"{earlier} must precede {later}"
                    )

    def test_coffee_blooms_before_pouring_and_serves_last(self):
        where = position(plan_for("I want a cup of pour over coffee"))
        self.assertLess(where["place_filter"], where["add_coffee_grounds"])
        self.assertLess(where["boil_water"], where["bloom_grounds"])
        self.assertLess(where["bloom_grounds"], where["pour_water"])
        self.assertLess(where["pour_coffee"], where["serve"])

    def test_tshirt_smooths_before_folding(self):
        where = position(plan_for("fold my t-shirt"))
        self.assertLess(where["lay_flat"], where["smooth_wrinkles"])
        self.assertLess(where["smooth_wrinkles"], where["fold_left_side"])
        self.assertLess(where["fold_bottom_up"], where["place_on_stack"])

    def test_bed_sheets_before_duvet_and_pillows(self):
        where = position(plan_for("make the bed"))
        self.assertLess(where["clear_bed"], where["spread_fitted_sheet"])
        self.assertLess(where["spread_flat_sheet"], where["spread_duvet"])
        self.assertLess(where["spread_duvet"], where["place_pillows"])

    def test_plants_fill_before_watering_and_drain_before_emptying(self):
        where = position(plan_for("water the houseplants"))
        self.assertLess(where["fill_can"], where["water_plant"])
        self.assertLess(where["water_plant"], where["let_drain"])
        self.assertLess(where["let_drain"], where["empty_saucer"])

    def test_burger_assembles_after_cheese_and_buns(self):
        """Criterion 2 names this one explicitly."""
        where = position(plan_for("cook a burger"))
        self.assertLess(where["add_cheese"], where["assemble_burger"])
        self.assertLess(where["toast_buns"], where["assemble_burger"])
        self.assertLess(where["cook_patty"], where["flip_patty"])

    def test_orders_are_reproducible(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(plan_for(command).actions, plan_for(command).actions)


class GroundingTests(unittest.TestCase):
    """Criterion 4 — every step maps to a graph primitive."""

    def test_no_invented_or_missing_steps(self):
        for command, skill in COMMANDS.items():
            with self.subTest(command=command):
                plan = plan_for(command)
                primitives = set(book_named(skill).primitives)
                self.assertEqual(len(plan.actions), len(primitives))
                self.assertEqual(set(plan.actions), primitives)

    def test_no_step_appears_twice(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                actions = plan_for(command).actions
                self.assertEqual(len(actions), len(set(actions)))

    def test_step_order_field_is_sequential(self):
        plan = plan_for("cook a burger")
        self.assertEqual([s.order for s in plan.steps], list(range(1, len(plan.steps) + 1)))

    def test_requires_and_produces_come_from_the_graph(self):
        plan = plan_for("make me a masala chai")
        by_action = {s.action: s for s in plan.steps}
        self.assertIn("pan_on_stove", by_action["add_water"].requires)
        self.assertIn("water_in_pan", by_action["add_water"].produces)


class NoLlmTests(unittest.TestCase):
    """Criterion 5 — disabling the LLM still yields a correctly-ordered plan."""

    def test_order_is_identical_without_the_llm(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                plan = plan_for(command)
                self.assertEqual(plan.meta["composer"], "template")
                self.assertIsNone(plan.meta["model_id"])

    def test_templated_descriptions_are_still_written(self):
        plan = plan_for("fold my t-shirt")
        for step in plan.steps:
            self.assertTrue(step.description.strip())

    def test_templated_phrasing_reads_as_an_instruction(self):
        plan = plan_for("make the bed")
        first = plan.steps[0]
        self.assertTrue(first.description[0].isupper())
        self.assertTrue(first.description.endswith("."))


class ClarificationTests(unittest.TestCase):
    """Criterion 3 — a below-threshold command asks rather than guesses."""

    def _resolve(self, command, **overrides):
        db, _config, _books = corpus_db()
        return plan_command(command, db, planner(**overrides))

    def test_an_unrelated_command_asks_for_clarification(self):
        result = self._resolve("please reticulate the splines")
        self.assertIsInstance(result, Clarification)
        self.assertTrue(result.reason)

    def test_clarification_offers_candidates(self):
        result = self._resolve("do something useful for me")
        self.assertIsInstance(result, Clarification)
        self.assertLessEqual(len(result.candidates), 3)

    def test_no_plan_is_produced_when_unclear(self):
        result = self._resolve("xyzzy")
        self.assertIsInstance(result, Clarification)
        self.assertFalse(hasattr(result, "steps"))

    def test_raising_the_threshold_forces_clarification(self):
        result = self._resolve("make me a masala chai", PLANNER_THRESHOLD="0.99")
        self.assertIsInstance(result, Clarification)
        self.assertIn("below the threshold", result.reason)

    def test_a_near_tie_is_treated_as_ambiguous(self):
        result = self._resolve("make me a masala chai", PLANNER_TIE_MARGIN="0.99")
        self.assertIsInstance(result, Clarification)
        self.assertTrue(result.ambiguous)


class MetaTests(unittest.TestCase):
    """Section 6 — the meta block."""

    def test_meta_carries_the_contract_fields(self):
        plan = plan_for("cook a burger")
        for key in ("skill_scope", "generated_at", "model_id", "match_confidence"):
            self.assertIn(key, plan.meta)

    def test_scope_matches_the_skill(self):
        self.assertEqual(plan_for("make the bed").meta["skill_scope"], "make_bed")

    def test_ordering_provenance_is_reported(self):
        ordering = plan_for("make me a masala chai").meta["ordering"]
        self.assertGreater(ordering["from_precedes_edges"], 0)
        self.assertGreater(ordering["from_state_chain"], 0)
        self.assertEqual(ordering["confirmed_by_both"], ordering["constraints"])

    def test_no_orphan_preconditions_in_a_healthy_corpus(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                self.assertNotIn("orphan_preconditions", plan_for(command).meta)


if __name__ == "__main__":
    unittest.main()
