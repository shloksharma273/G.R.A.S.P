"""Station 4: direction normalization, state chaining, reconciliation, cycles."""

from __future__ import annotations

import unittest

from direction_normalizer import normalize, ordering_graph
from direction_normalizer.model import InputEdge
from direction_normalizer.policy import (
    CONFIDENCE,
    CONFIRMED_CONFIDENCE,
    METHOD_DERIVED,
    METHOD_DESCRIPTION,
    METHOD_ORIENTATION,
    METHOD_TYPE_IMPLIED,
)
from rule_preclassifier.model import ConservationError

from .station4_fixture import precedes, produces, requires, stamped


def arrows(result):
    return {(edge.head, edge.tail) for edge in result.finalized}


def derived_arrows(result):
    return {(edge.head, edge.tail) for edge in result.derived}


class TypeImpliedDirectionTests(unittest.TestCase):
    """FR-2: force the canonical arrow, flipping anything written backwards."""

    def test_decomposes_to_points_skill_to_primitive(self):
        result = normalize([stamped("make_chai", "SKILL", "place_pan", "PRIMITIVE", "decomposes_to")])
        edge = result.finalized[0]
        self.assertEqual(edge.arrow, ("make_chai", "place_pan"))
        self.assertEqual(edge.direction_method, METHOD_TYPE_IMPLIED)
        self.assertFalse(edge.flipped)

    def test_reversed_decomposes_to_is_flipped(self):
        result = normalize([stamped("place_pan", "PRIMITIVE", "make_chai", "SKILL", "decomposes_to")])
        edge = result.finalized[0]
        self.assertEqual(edge.arrow, ("make_chai", "place_pan"))
        self.assertTrue(edge.flipped)

    def test_uses_points_primitive_to_object(self):
        result = normalize([stamped("strainer", "OBJECT", "strain", "PRIMITIVE", "uses")])
        self.assertEqual(result.finalized[0].arrow, ("strain", "strainer"))
        self.assertTrue(result.finalized[0].flipped)

    def test_requires_points_primitive_to_state(self):
        result = normalize([stamped("pan_on_stove", "STATE", "add_water", "PRIMITIVE", "requires")])
        self.assertEqual(result.finalized[0].arrow, ("add_water", "pan_on_stove"))

    def test_produces_points_primitive_to_state(self):
        result = normalize([stamped("place_pan", "PRIMITIVE", "pan_on_stove", "STATE", "produces")])
        self.assertEqual(result.finalized[0].arrow, ("place_pan", "pan_on_stove"))
        self.assertFalse(result.finalized[0].flipped)

    def test_lower_case_types_are_understood(self):
        result = normalize([stamped("place_pan", "tool", "pan_on_stove", "state", "produces")])
        self.assertEqual(result.finalized[0].direction_method, METHOD_TYPE_IMPLIED)

    def test_flipping_never_mutates_the_bundle(self):
        edge = stamped("place_pan", "PRIMITIVE", "make_chai", "SKILL", "decomposes_to")
        result = normalize([edge])
        self.assertEqual(result.finalized[0].bundle.source.name, "place_pan")
        self.assertEqual(result.finalized[0].head, "make_chai")

    def test_no_reversed_edges_remain(self):
        """Acceptance criterion 3."""
        edges = [
            stamped("place_pan", "PRIMITIVE", "make_chai", "SKILL", "decomposes_to", key="r1"),
            stamped("strainer", "OBJECT", "strain", "PRIMITIVE", "uses", key="r2"),
            stamped("pan_on_stove", "STATE", "add_water", "PRIMITIVE", "requires", key="r3"),
        ]
        result = normalize(edges)
        for edge in result.finalized:
            self.assertEqual(edge.head, edge.head.strip())
        self.assertEqual(arrows(result), {
            ("make_chai", "place_pan"), ("strain", "strainer"), ("add_water", "pan_on_stove"),
        })
        self.assertEqual(result.flipped_count(), 3)


class StateChainingTests(unittest.TestCase):
    """FR-3 and acceptance criterion 1: ordering from shared states."""

    def test_producer_precedes_requirer(self):
        result = normalize([
            produces("place_pan", "pan_on_stove", key="r1"),
            requires("add_water", "pan_on_stove", key="r2"),
        ])
        self.assertEqual(derived_arrows(result), {("place_pan", "add_water")})

    def test_derived_edge_records_its_source_state_and_edges(self):
        result = normalize([
            produces("place_pan", "pan_on_stove", key="p1"),
            requires("add_water", "pan_on_stove", key="q1"),
        ])
        edge = result.derived[0]
        self.assertEqual(edge.via_state, "pan_on_stove")
        self.assertEqual(edge.producing_relation_key, "p1")
        self.assertEqual(edge.requiring_relation_key, "q1")
        self.assertIn("pan_on_stove", edge.trace)

    def test_derived_edges_carry_structural_confidence(self):
        result = normalize([
            produces("a", "s", key="r1"), requires("b", "s", key="r2"),
        ])
        self.assertEqual(result.derived[0].confidence, CONFIDENCE[METHOD_DERIVED])
        self.assertEqual(result.derived[0].direction_method, METHOD_DERIVED)

    def test_full_cross_product_when_many_produce_and_many_require(self):
        """Section 9: the cross-product is the correct answer, not an explosion."""
        result = normalize([
            produces("p1", "s", key="a"), produces("p2", "s", key="b"),
            requires("r1", "s", key="c"), requires("r2", "s", key="d"),
        ])
        self.assertEqual(
            derived_arrows(result),
            {("p1", "r1"), ("p1", "r2"), ("p2", "r1"), ("p2", "r2")},
        )

    def test_state_produced_but_never_required_derives_nothing(self):
        result = normalize([produces("turn_off_stove", "stove_off", key="r1")])
        self.assertEqual(result.derived, [])

    def test_state_required_but_never_produced_derives_nothing(self):
        result = normalize([requires("serve", "tea_in_cup", key="r1")])
        self.assertEqual(result.derived, [])

    def test_self_chain_is_skipped_and_recorded(self):
        """Section 9: an action that both produces and requires a state."""
        result = normalize([
            produces("stir", "mixed", key="r1"), requires("stir", "mixed", key="r2"),
        ])
        self.assertEqual(result.derived, [])
        self.assertEqual(result.self_chains, [("stir", "mixed")])

    def test_chaining_reads_the_canonical_direction_not_the_written_one(self):
        # requires written backwards: chaining must still find it.
        result = normalize([
            produces("place_pan", "pan_on_stove", key="r1"),
            stamped("pan_on_stove", "STATE", "add_water", "PRIMITIVE", "requires", key="r2"),
        ])
        self.assertEqual(derived_arrows(result), {("place_pan", "add_water")})

    def test_duplicate_arrows_are_merged(self):
        """Two shared states between the same pair still make one edge."""
        result = normalize([
            produces("a", "s1", key="r1"), requires("b", "s1", key="r2"),
            produces("a", "s2", key="r3"), requires("b", "s2", key="r4"),
        ])
        self.assertEqual(len(result.derived), 1)
        self.assertEqual(result.derived[0].arrow, ("a", "b"))

    def test_names_are_matched_across_spellings(self):
        result = normalize([
            produces("PLACE PAN", "PAN ON STOVE", key="r1"),
            requires("ADD WATER", "pan_on_stove", key="r2"),
        ])
        self.assertEqual(len(result.derived), 1)


class ExplicitPrecedesTests(unittest.TestCase):
    """FR-4: the trust hierarchy, in order."""

    def test_priority_1_state_chaining_decides(self):
        result = normalize([
            produces("a", "s", key="r1"), requires("b", "s", key="r2"),
            precedes("a", "b", "some prose", key="r3"),
        ])
        edge = [e for e in result.finalized if e.edge_type == "precedes"][0]
        self.assertEqual(edge.direction_method, METHOD_DERIVED)
        self.assertTrue(edge.confirmed_by_chaining)

    def test_priority_2_forward_cue(self):
        result = normalize([precedes("a", "b", "a happens before b", key="r1")])
        edge = result.finalized[0]
        self.assertEqual(edge.arrow, ("a", "b"))
        self.assertEqual(edge.direction_method, METHOD_DESCRIPTION)

    def test_priority_2_backward_cue_flips(self):
        result = normalize([precedes("a", "b", "a happens after b", key="r1")])
        edge = result.finalized[0]
        self.assertEqual(edge.arrow, ("b", "a"))
        self.assertEqual(edge.direction_method, METHOD_DESCRIPTION)
        self.assertTrue(edge.flipped)

    def test_conflicting_cues_fall_through_to_orientation(self):
        result = normalize([precedes("a", "b", "before and after are both here", key="r1")])
        edge = result.finalized[0]
        self.assertEqual(edge.direction_method, METHOD_ORIENTATION)

    def test_priority_3_orientation_is_a_weak_tiebreaker(self):
        result = normalize([precedes("a", "b", "no ordering words at all", key="r1")])
        edge = result.finalized[0]
        self.assertEqual(edge.arrow, ("a", "b"))
        self.assertEqual(edge.confidence, CONFIDENCE[METHOD_ORIENTATION])
        self.assertLess(edge.confidence, CONFIDENCE[METHOD_DESCRIPTION])

    def test_priority_4_parks_when_orientation_is_unusable(self):
        result = normalize([precedes("a", "a", "", key="r1")])
        self.assertEqual(result.parked[0].reason_code, "ambiguous_direction")
        self.assertEqual(result.finalized, [])

    def test_confidence_rises_with_trust(self):
        self.assertGreater(CONFIDENCE[METHOD_DERIVED], CONFIDENCE[METHOD_DESCRIPTION])
        self.assertGreater(CONFIDENCE[METHOD_DESCRIPTION], CONFIDENCE[METHOD_ORIENTATION])


class ReconciliationTests(unittest.TestCase):
    """FR-5 / Section 5: agreement confirms, conflict prefers derived."""

    def test_agreement_raises_the_derived_edge_confidence(self):
        result = normalize([
            produces("a", "s", key="r1"), requires("b", "s", key="r2"),
            precedes("a", "b", "prose", key="r3"),
        ])
        self.assertEqual(result.derived[0].confidence, CONFIRMED_CONFIDENCE)
        self.assertTrue(result.derived[0].confirms_explicit)

    def test_agreement_does_not_duplicate_the_arrow(self):
        result = normalize([
            produces("a", "s", key="r1"), requires("b", "s", key="r2"),
            precedes("a", "b", "prose", key="r3"),
        ])
        self.assertEqual(result.precedes_edges(), [("a", "b")])

    def test_conflict_keeps_derived_and_parks_the_explicit(self):
        result = normalize([
            produces("a", "s", key="r1"), requires("b", "s", key="r2"),
            precedes("b", "a", "written the other way", key="r3"),
        ])
        self.assertEqual(derived_arrows(result), {("a", "b")})
        self.assertEqual(result.parked[0].reason_code, "conflicting_edge")
        self.assertEqual([e for e in result.finalized if e.edge_type == "precedes"], [])

    def test_the_parked_loser_is_not_dropped(self):
        """Acceptance criterion 4: parked, not silently discarded."""
        result = normalize([
            produces("a", "s", key="r1"), requires("b", "s", key="r2"),
            precedes("b", "a", "backwards", key="r3"),
        ])
        self.assertEqual(result.parked[0].relation_key, "r3")
        self.assertIn("chaining", result.parked[0].detail)

    def test_explicit_edge_with_no_chain_support_is_kept_at_lower_confidence(self):
        result = normalize([precedes("x", "y", "x before y", key="r1")])
        edge = result.finalized[0]
        self.assertFalse(edge.confirmed_by_chaining)
        self.assertEqual(edge.confidence, CONFIDENCE[METHOD_DESCRIPTION])

    def test_overlap_counts_are_reported(self):
        result = normalize([
            produces("a", "s", key="r1"), requires("b", "s", key="r2"),
            precedes("a", "b", "prose", key="r3"),
            precedes("y", "z", "y before z", key="r4"),
        ])
        overlap = result.overlap()
        self.assertEqual(overlap["derived_confirming_explicit"], 1)
        self.assertEqual(overlap["derived_new"], 0)
        self.assertEqual(overlap["explicit_unsupported"], 1)


class CycleGuardTests(unittest.TestCase):
    """FR-6: never emit an edge that would close a cycle."""

    def test_explicit_edge_closing_a_cycle_is_parked(self):
        result = normalize([
            produces("a", "s1", key="r1"), requires("b", "s1", key="r2"),
            produces("b", "s2", key="r3"), requires("c", "s2", key="r4"),
            precedes("c", "a", "c before a", key="r5"),
        ])
        self.assertEqual(result.parked[0].reason_code, "creates_cycle")

    def test_the_cyclic_edge_is_not_added(self):
        result = normalize([
            produces("a", "s1", key="r1"), requires("b", "s1", key="r2"),
            precedes("b", "a", "b before a", key="r3"),
        ])
        self.assertNotIn(("b", "a"), result.precedes_edges())

    def test_the_graph_stays_sortable(self):
        result = normalize([
            produces("a", "s1", key="r1"), requires("b", "s1", key="r2"),
            produces("b", "s2", key="r3"), requires("c", "s2", key="r4"),
            precedes("c", "a", "c first", key="r5"),
        ])
        self.assertIsNotNone(ordering_graph(result).topological_order())

    def test_a_cyclic_chain_is_refused_and_reported(self):
        """Two actions each producing what the other requires is a modelling bug."""
        result = normalize([
            produces("a", "s1", key="r1"), requires("b", "s1", key="r2"),
            produces("b", "s2", key="r3"), requires("a", "s2", key="r4"),
        ])
        self.assertEqual(len(result.derived), 1)
        self.assertEqual(len(result.refused_derived), 1)
        self.assertIsNotNone(ordering_graph(result).topological_order())

    def test_no_cycle_is_ever_emitted(self):
        """Acceptance criterion 5."""
        result = normalize([
            produces("a", "s1", key="r1"), requires("b", "s1", key="r2"),
            produces("b", "s2", key="r3"), requires("a", "s2", key="r4"),
            precedes("b", "a", "backwards", key="r5"),
        ])
        self.assertIsNotNone(ordering_graph(result).topological_order())


class ConservationTests(unittest.TestCase):
    """FR-8: every input finalized or parked; derived counted separately."""

    def test_holds_on_a_mixed_set(self):
        result = normalize([
            stamped("make_chai", "SKILL", "place_pan", "PRIMITIVE", "decomposes_to", key="r1"),
            produces("place_pan", "pan_on_stove", key="r2"),
            requires("add_water", "pan_on_stove", key="r3"),
            precedes("x", "x", "", key="r4"),
        ])
        self.assertEqual(result.total_input, 4)
        self.assertEqual(result.total_output, 4)
        self.assertEqual(len(result.derived), 1)

    def test_derived_edges_are_not_counted_as_inputs(self):
        result = normalize([
            produces("a", "s", key="r1"), requires("b", "s", key="r2"),
        ])
        self.assertEqual(result.total_input, 2)
        self.assertEqual(result.total_output, 2)
        self.assertEqual(len(result.derived), 1)

    def test_empty_input(self):
        result = normalize([])
        self.assertEqual(result.counts_by_stream(), {"finalized": 0, "derived": 0, "parked": 0})

    def test_violation_raises(self):
        result = normalize([produces("a", "s", key="r1")])
        result.total_input = 99
        with self.assertRaises(ConservationError):
            result.assert_conservation()


class DeterminismTests(unittest.TestCase):
    """FR-7 / Section 8: pure graph logic, stable ordering, idempotent."""

    def setUp(self):
        self.edges = [
            produces("a", "s1", key="r1"), requires("b", "s1", key="r2"),
            produces("b", "s2", key="r3"), requires("c", "s2", key="r4"),
            stamped("skill", "SKILL", "a", "PRIMITIVE", "decomposes_to", key="r5"),
        ]

    def test_identical_input_yields_identical_output(self):
        self.assertEqual(normalize(self.edges).to_dict(), normalize(self.edges).to_dict())

    def test_derived_order_is_stable(self):
        first = [e.arrow for e in normalize(self.edges).derived]
        second = [e.arrow for e in normalize(list(reversed(self.edges))).derived]
        self.assertEqual(first, second)

    def test_topological_order_is_stable(self):
        graph = ordering_graph(normalize(self.edges))
        self.assertEqual(graph.topological_order(), graph.topological_order())

    def test_input_is_not_mutated(self):
        before = list(self.edges)
        normalize(self.edges)
        self.assertEqual(self.edges, before)

    def test_imports_nothing_impure(self):
        import ast
        import pathlib

        import direction_normalizer

        forbidden = {"arango", "requests", "urllib", "random", "time", "datetime", "os", "socket"}
        package = pathlib.Path(direction_normalizer.__file__).parent
        for name in ("normalizer.py", "chaining.py", "cycles.py", "policy.py", "model.py"):
            tree = ast.parse((package / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], forbidden, name)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    self.assertNotIn(node.module.split(".")[0], forbidden, name)


class InputAdapterTests(unittest.TestCase):
    """FR-1: Station 2 and Station 3 records, and their JSON forms."""

    def test_accepts_station_2_records(self):
        result = normalize([stamped("s", "SKILL", "p", "PRIMITIVE", "decomposes_to")])
        self.assertEqual(result.finalized[0].upstream_station, "station2:rule_preclassifier")

    def test_accepts_station_3_records(self):
        result = normalize([produces("a", "s")])
        self.assertEqual(result.finalized[0].upstream_station, "station3:llm_disambiguator")

    def test_accepts_json_rows(self):
        rows = [
            {
                "relation_key": "r1",
                "edge_type": "produces",
                "source": {"name": "a", "type": "PRIMITIVE"},
                "target": {"name": "s", "type": "STATE"},
                "description": "",
                "method": "llm",
                "confidence": 0.9,
                "station": "station3:llm_disambiguator",
            },
            {
                "relation_key": "r2",
                "edge_type": "requires",
                "source": {"name": "b", "type": "PRIMITIVE"},
                "target": {"name": "s", "type": "STATE"},
                "description": "",
                "method": "llm",
                "confidence": 0.9,
                "station": "station3:llm_disambiguator",
            },
        ]
        result = normalize(rows)
        self.assertEqual(derived_arrows(result), {("a", "b")})

    def test_station_2_categorical_confidence_is_not_a_number(self):
        result = normalize([stamped("s", "SKILL", "p", "PRIMITIVE", "decomposes_to")])
        self.assertIsNone(result.finalized[0].confidence)

    def test_station_3_numeric_confidence_survives(self):
        result = normalize([produces("a", "s")])
        self.assertEqual(result.finalized[0].confidence, 0.95)

    def test_input_edges_pass_through_unchanged(self):
        edge = InputEdge(
            bundle=produces("a", "s").bundle,
            edge_type="produces",
            head="a",
            tail="s",
            upstream_method="llm",
            upstream_station="station3:llm_disambiguator",
        )
        self.assertEqual(normalize([edge]).total_input, 1)


if __name__ == "__main__":
    unittest.main()
