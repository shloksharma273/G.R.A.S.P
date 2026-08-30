"""Routing, conservation, orientation and purity — the core of Section 13."""

from __future__ import annotations

import ast
import pathlib
import unittest
from collections import Counter

import rule_preclassifier

from rule_preclassifier import classify
from rule_preclassifier.model import ConservationError
from rule_preclassifier.table import DECOMPOSES_TO, PRECEDES, PRODUCES, REQUIRES, USES

from .station2_fixture import CHAI_EXPECTED, bundle, chai_bundles


class DecisionTableRoutingTests(unittest.TestCase):
    def test_skill_primitive_stamps_decomposes_to(self):
        result = classify([bundle("make_chai", "SKILL", "place_pan", "PRIMITIVE")])
        self.assertEqual(len(result.stamped), 1)
        self.assertEqual(result.stamped[0].edge_type, DECOMPOSES_TO)

    def test_primitive_primitive_stamps_precedes(self):
        result = classify([bundle("place_pan", "PRIMITIVE", "add_water", "PRIMITIVE")])
        self.assertEqual(result.stamped[0].edge_type, PRECEDES)

    def test_primitive_object_stamps_uses(self):
        result = classify([bundle("strain", "PRIMITIVE", "strainer", "OBJECT")])
        self.assertEqual(result.stamped[0].edge_type, USES)

    def test_primitive_state_defers_unresolved(self):
        result = classify([bundle("place_pan", "PRIMITIVE", "pan_on_stove", "STATE")])
        self.assertEqual(len(result.deferred), 1)
        self.assertEqual(result.deferred[0].candidate_edge_types, (REQUIRES, PRODUCES))
        self.assertEqual(result.stamped, [])

    def test_deferred_bundle_is_not_resolved_by_station_2(self):
        # FR-4: Station 2 must not read the description to pick a side.
        effect = classify([bundle("place_pan", "PRIMITIVE", "pan_on_stove", "STATE", "the effect is")])
        precondition = classify(
            [bundle("add_water", "PRIMITIVE", "pan_on_stove", "STATE", "requires as a precondition")]
        )
        self.assertEqual(
            effect.deferred[0].candidate_edge_types, precondition.deferred[0].candidate_edge_types
        )

    def test_unmapped_pair_parks(self):
        result = classify([bundle("stove", "OBJECT", "stove_on", "STATE")])
        self.assertEqual(len(result.parked), 1)
        self.assertEqual(result.parked[0].reason_code, "unmapped_pair")


class OrderIndependenceTests(unittest.TestCase):
    """FR-2: membership is tested order-independently, so a reversed edge matches."""

    def test_reversed_skill_primitive_still_types(self):
        forward = classify([bundle("make_chai", "SKILL", "place_pan", "PRIMITIVE")])
        reverse = classify([bundle("place_pan", "PRIMITIVE", "make_chai", "SKILL")])
        self.assertEqual(forward.stamped[0].edge_type, reverse.stamped[0].edge_type)

    def test_reversed_primitive_object_still_types(self):
        reverse = classify([bundle("strainer", "OBJECT", "strain", "PRIMITIVE")])
        self.assertEqual(reverse.stamped[0].edge_type, USES)

    def test_reversed_primitive_state_still_defers(self):
        reverse = classify([bundle("pan_on_stove", "STATE", "place_pan", "PRIMITIVE")])
        self.assertEqual(len(reverse.deferred), 1)


class OrientationTests(unittest.TestCase):
    """Section 7: Station 2 records orientation, it does not finalize it."""

    def test_decomposes_to_head_is_the_skill(self):
        result = classify([bundle("make_chai", "SKILL", "place_pan", "PRIMITIVE")])
        orientation = result.stamped[0].orientation
        self.assertEqual((orientation.head, orientation.tail), ("make_chai", "place_pan"))
        self.assertFalse(orientation.reversed_from_input)

    def test_reversed_input_is_recorded_not_silently_flipped(self):
        result = classify([bundle("place_pan", "PRIMITIVE", "make_chai", "SKILL")])
        orientation = result.stamped[0].orientation
        self.assertEqual((orientation.head, orientation.tail), ("make_chai", "place_pan"))
        self.assertTrue(orientation.reversed_from_input)

    def test_original_source_is_never_mutated(self):
        original = bundle("place_pan", "PRIMITIVE", "make_chai", "SKILL")
        result = classify([original])
        kept = result.stamped[0].bundle
        self.assertEqual(kept.source.name, "place_pan")
        self.assertEqual(kept.target.name, "make_chai")
        self.assertIs(kept, original)

    def test_uses_head_is_the_primitive(self):
        result = classify([bundle("strainer", "OBJECT", "strain", "PRIMITIVE")])
        orientation = result.stamped[0].orientation
        self.assertEqual((orientation.head, orientation.tail), ("strain", "strainer"))

    def test_precedes_direction_is_left_to_station_4(self):
        result = classify([bundle("place_pan", "PRIMITIVE", "add_water", "PRIMITIVE")])
        orientation = result.stamped[0].orientation
        self.assertIsNone(orientation.head)
        self.assertFalse(orientation.is_resolved)
        self.assertEqual(orientation.decided_by, "deferred_to_station_4")


class TypeCanonicalizationTests(unittest.TestCase):
    def test_lower_case_ontology_types_are_matched(self):
        result = classify([bundle("make_chai", "skill", "place_pan", "PRIMITIVE")])
        self.assertEqual(result.stamped[0].edge_type, DECOMPOSES_TO)

    def test_tool_is_read_as_primitive(self):
        result = classify([bundle("make_chai", "skill", "PLACE PAN", "tool")])
        self.assertEqual(result.stamped[0].edge_type, DECOMPOSES_TO)

    def test_normalizations_are_reported(self):
        result = classify([bundle("make_chai", "skill", "PLACE PAN", "tool")])
        self.assertEqual(
            dict(result.normalized_types), {"skill -> SKILL": 1, "tool -> PRIMITIVE": 1}
        )

    def test_unknown_vocabulary_parks_rather_than_guessing(self):
        result = classify([bundle("a", "WIDGET", "b", "PRIMITIVE")])
        self.assertEqual(result.parked[0].reason_code, "missing_type")


class ConservationTests(unittest.TestCase):
    """FR-6: every input leaves in exactly one bucket."""

    def test_holds_on_the_chai_set(self):
        result = classify(chai_bundles())
        self.assertEqual(result.total_input, result.total_output)

    def test_holds_on_a_mixed_set(self):
        bundles = [
            bundle("make_chai", "SKILL", "place_pan", "PRIMITIVE", relation_key="r1"),
            bundle("place_pan", "PRIMITIVE", "pan_on_stove", "STATE", relation_key="r2"),
            bundle("stove", "OBJECT", "stove_on", "STATE", relation_key="r3"),
            bundle("a", "WIDGET", "b", "PRIMITIVE", relation_key="r4"),
            bundle("loop", "PRIMITIVE", "loop", "PRIMITIVE", relation_key="r5"),
        ]
        result = classify(bundles)
        self.assertEqual(result.total_input, 5)
        self.assertEqual(result.total_output, 5)

    def test_no_bundle_appears_twice(self):
        result = classify(chai_bundles())
        keys = [record.bundle.relation_key for record in result.all_records()]
        self.assertEqual(len(keys), len(set(keys)))

    def test_empty_input_is_conserved(self):
        result = classify([])
        self.assertEqual(result.counts_by_bucket(), {"stamped": 0, "deferred": 0, "parked": 0})

    def test_violation_raises_rather_than_passing_quietly(self):
        result = classify([bundle("make_chai", "SKILL", "place_pan", "PRIMITIVE")])
        result.total_input = 99
        with self.assertRaises(ConservationError):
            result.assert_conservation()


class PurityTests(unittest.TestCase):
    """FR-8 / Section 11: pure function, no side effects, no randomness."""

    def test_identical_input_yields_identical_output(self):
        bundles = chai_bundles()
        first = classify(bundles).to_dict()
        second = classify(bundles).to_dict()
        self.assertEqual(first, second)

    def test_ordering_is_stable_across_runs(self):
        bundles = chai_bundles()
        keys = lambda result: [r.bundle.relation_key for r in result.all_records()]
        self.assertEqual(keys(classify(bundles)), keys(classify(bundles)))

    def test_input_list_is_not_mutated(self):
        bundles = chai_bundles()
        before = list(bundles)
        classify(bundles)
        self.assertEqual(bundles, before)

    def test_accepts_a_generator_without_re_reading_it(self):
        bundles = chai_bundles()
        result = classify(b for b in bundles)
        self.assertEqual(result.total_input, len(bundles))

    #: Anything that would make the station impure if it were imported.
    FORBIDDEN_IMPORTS = frozenset(
        {"arango", "requests", "urllib", "urllib3", "http", "socket", "random",
         "time", "datetime", "subprocess", "openai", "anthropic", "pathlib", "os"}
    )

    def test_imports_nothing_impure(self):
        """FR-8, checked on the import graph rather than on prose."""
        for module in self._station_modules():
            for name in self._imported_modules(module):
                self.assertNotIn(
                    name.split(".")[0],
                    self.FORBIDDEN_IMPORTS,
                    f"{module} imports {name}, which makes Station 2 impure",
                )

    def test_opens_no_file(self):
        for module in self._station_modules():
            tree = ast.parse(module.read_text(encoding="utf-8"))
            called = {
                node.func.id
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            }
            self.assertNotIn("open", called, f"{module} opens a file")
            self.assertNotIn("print", called, f"{module} writes to a stream")

    @staticmethod
    def _station_modules():
        """The pure core: everything except `cli`/`report`, which own the I/O."""
        package = pathlib.Path(rule_preclassifier.__file__).parent
        return [
            package / name
            for name in ("classifier.py", "detect.py", "table.py", "model.py")
        ]

    @staticmethod
    def _imported_modules(path):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    yield alias.name
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                yield node.module


class ChaiAcceptanceTests(unittest.TestCase):
    """Section 13, criterion 1: the chai set routes per the answer key."""

    def setUp(self):
        self.result = classify(chai_bundles())

    def test_bucket_sizes_match_the_answer_key(self):
        self.assertEqual(self.result.counts_by_bucket(), CHAI_EXPECTED)

    def test_every_skill_primitive_is_decomposes_to(self):
        stamped = {
            edge.bundle.type_pair: edge.edge_type
            for edge in self.result.stamped
        }
        self.assertEqual(stamped[("SKILL", "PRIMITIVE")], DECOMPOSES_TO)
        self.assertEqual(stamped[("PRIMITIVE", "OBJECT")], USES)

    def test_edge_type_counts(self):
        counts = Counter(edge.edge_type for edge in self.result.stamped)
        self.assertEqual(counts, Counter({DECOMPOSES_TO: 11, USES: 8}))

    def test_all_twenty_primitive_state_edges_defer(self):
        self.assertEqual(len(self.result.deferred), 20)
        for item in self.result.deferred:
            self.assertEqual(item.bundle.type_pair, ("PRIMITIVE", "STATE"))

    def test_nothing_parks(self):
        self.assertEqual(self.result.parked, [])

    def test_provenance_survives_into_every_bucket(self):
        for record in self.result.all_records():
            self.assertTrue(record.relation_key)


if __name__ == "__main__":
    unittest.main()
