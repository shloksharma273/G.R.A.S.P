"""Acceptance criteria 1 and 2: the chai order the graph reconstructs.

The claim being tested is the project's central one — that execution order falls
out of the precondition graph without the rulebook ever stating it. So these tests
check the specific chains the PRD names, then check that a topological sort of the
whole derived graph reads as a valid recipe.

Nothing here calls a model: Station 3's labels come from the answer key, so this
measures Station 4 rather than re-measuring Station 3.
"""

from __future__ import annotations

import unittest

from direction_normalizer import normalize, ordering_graph

from .station4_fixture import chai_stamped, violated_constraints


class ChaiChainingTests(unittest.TestCase):
    def setUp(self):
        self.edges, self.classified = chai_stamped()
        self.result = normalize(self.edges)
        self.derived = {(e.head, e.tail): e for e in self.result.derived}

    def test_the_three_chains_the_prd_names(self):
        """Acceptance criterion 1, verbatim."""
        for head, tail, state in (
            ("place_pan", "add_water", "pan_on_stove"),
            ("boil_water", "add_tea_leaves", "water_boiling"),
            ("simmer", "strain", "tea_brewed"),
        ):
            self.assertIn((head, tail), self.derived, f"{head} -> {tail} was not derived")
            self.assertEqual(self.derived[(head, tail)].via_state, state)

    def test_every_derived_edge_is_traceable(self):
        for edge in self.result.derived:
            self.assertTrue(edge.via_state)
            self.assertTrue(edge.producing_relation_key)
            self.assertTrue(edge.requiring_relation_key)

    def test_the_ordering_is_entirely_new(self):
        """The rulebook states no precedes edge; all ordering is derived."""
        explicit = [e for e in self.classified.stamped if e.edge_type == "precedes"]
        self.assertEqual(explicit, [], "the chai KG should contain no explicit precedes edge")
        overlap = self.result.overlap()
        self.assertEqual(overlap["derived_new"], overlap["derived_total"])
        self.assertGreater(overlap["derived_total"], 0)

    def test_nothing_is_parked(self):
        self.assertEqual(self.result.parked, [])

    def test_conservation_holds(self):
        self.assertEqual(self.result.total_input, self.result.total_output)


class ChaiPlanTests(unittest.TestCase):
    """Acceptance criterion 2: a topological sort yields a valid recipe."""

    def setUp(self):
        self.result = normalize(chai_stamped()[0])
        self.graph = ordering_graph(self.result)
        self.order = self.graph.topological_order()

    def test_the_graph_is_acyclic(self):
        self.assertIsNotNone(self.order, "the derived ordering graph must be a DAG")

    def test_the_order_respects_every_constraint(self):
        self.assertEqual(violated_constraints(self.order), [])

    def test_the_recipe_starts_by_placing_the_pan(self):
        self.assertEqual(self.order[0], "place_pan")

    def test_the_recipe_ends_by_serving(self):
        self.assertEqual(self.order[-1], "serve")

    def test_every_ordered_primitive_appears_once(self):
        self.assertEqual(len(self.order), len(set(self.order)))

    def test_brewing_precedes_both_additions_and_simmering(self):
        position = {name: index for index, name in enumerate(self.order)}
        self.assertLess(position["add_tea_leaves"], position["add_milk"])
        self.assertLess(position["add_tea_leaves"], position["add_sugar"])
        self.assertLess(position["add_milk"], position["simmer"])
        self.assertLess(position["add_sugar"], position["simmer"])

    def test_the_sort_is_deterministic(self):
        self.assertEqual(self.graph.topological_order(), self.order)


if __name__ == "__main__":
    unittest.main()
