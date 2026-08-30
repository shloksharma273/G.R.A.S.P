"""The parked-bundle taxonomy (PRD Section 9) — one reason code each, by priority."""

from __future__ import annotations

import unittest

from rule_preclassifier import classify
from rule_preclassifier.table import GROUP_MEANING, REASON_PRIORITY, STATION_3_REASONS

from .station2_fixture import bundle


def park(*bundles):
    """Classify and return the parked records."""
    return classify(list(bundles)).parked


def one(*bundles):
    parked = park(*bundles)
    assert len(parked) == 1, f"expected exactly one parked bundle, got {len(parked)}"
    return parked[0]


class ExactlyOneReasonTests(unittest.TestCase):
    def test_every_parked_bundle_carries_exactly_one_code(self):
        parked = park(
            bundle("stove", "OBJECT", "stove_on", "STATE", relation_key="r1"),
            bundle("a", "WIDGET", "b", "PRIMITIVE", relation_key="r2"),
            bundle("loop", "PRIMITIVE", "loop", "PRIMITIVE", relation_key="r3"),
        )
        for item in parked:
            self.assertIn(item.reason_code, REASON_PRIORITY)
            self.assertIsInstance(item.reason_code, str)

    def test_reason_codes_are_all_reachable(self):
        """Every code Station 2 owns must be producible, or the taxonomy is fiction."""
        produced = {
            one(bundle("stove", "OBJECT", "stove_on", "STATE")).reason_code,
            one(bundle("a", "WIDGET", "b", "PRIMITIVE")).reason_code,
            one(bundle("loop", "PRIMITIVE", "loop", "PRIMITIVE")).reason_code,
            one(bundle("add_water", "OBJECT", "stove_on", "STATE")).reason_code,
            one(bundle("a", "PRIMITIVE", "b", "STATE", description="")).reason_code,
            one(bundle("a", "PRIMITIVE", "b", "PRIMITIVE", description="")).reason_code,
            one(bundle("x_Entities/gone", "PRIMITIVE", "b", "STATE")).reason_code,
        }
        produced |= {
            item.reason_code
            for item in park(
                bundle("a", "PRIMITIVE", "b", "PRIMITIVE", relation_key="r1"),
                bundle("b", "PRIMITIVE", "a", "PRIMITIVE", relation_key="r2"),
            )
        }
        produced |= {
            item.reason_code
            for item in park(
                bundle("THE CUP", "OBJECT", "tea_in_cup", "STATE", relation_key="r1"),
                bundle("the_cup", "OBJECT", "stove_on", "STATE", relation_key="r2"),
            )
        }
        self.assertEqual(produced, set(REASON_PRIORITY) - STATION_3_REASONS)


class GroupBTests(unittest.TestCase):
    def test_missing_type_for_off_ontology_value(self):
        item = one(bundle("a", "WIDGET", "b", "PRIMITIVE"))
        self.assertEqual(item.reason_code, "missing_type")
        self.assertEqual(item.group, "B")

    def test_missing_type_for_absent_value(self):
        self.assertEqual(one(bundle("a", "", "b", "PRIMITIVE")).reason_code, "missing_type")

    def test_missing_type_for_station_1_unknown_marker(self):
        self.assertEqual(one(bundle("a", "UNKNOWN", "b", "PRIMITIVE")).reason_code, "missing_type")

    def test_suspect_type_when_an_action_is_typed_object(self):
        item = one(bundle("add_water", "OBJECT", "stove_on", "STATE"))
        self.assertEqual(item.reason_code, "suspect_type")
        self.assertEqual(item.group, "B")
        self.assertIn("add_water", item.detail)

    def test_suspect_type_does_not_fire_on_a_genuine_object(self):
        self.assertEqual(one(bundle("stove", "OBJECT", "stove_on", "STATE")).reason_code, "unmapped_pair")

    def test_suspect_type_never_overrides_a_good_classification(self):
        # 'strain' is a PRIMITIVE here, and the pair is mapped: no parking at all.
        result = classify([bundle("strain", "PRIMITIVE", "strainer", "OBJECT")])
        self.assertEqual(result.parked, [])


class GroupCTests(unittest.TestCase):
    def test_self_loop(self):
        item = one(bundle("simmer", "PRIMITIVE", "simmer", "PRIMITIVE"))
        self.assertEqual(item.reason_code, "self_loop")
        self.assertEqual(item.group, "C")

    def test_self_loop_is_caught_across_spellings(self):
        self.assertEqual(one(bundle("THE CUP", "OBJECT", "the_cup", "OBJECT")).reason_code, "self_loop")

    def test_conflicting_edge_parks_both_directions(self):
        parked = park(
            bundle("place_pan", "PRIMITIVE", "add_water", "PRIMITIVE", relation_key="r1"),
            bundle("add_water", "PRIMITIVE", "place_pan", "PRIMITIVE", relation_key="r2"),
        )
        self.assertEqual(len(parked), 2)
        self.assertEqual({item.reason_code for item in parked}, {"conflicting_edge"})

    def test_a_self_loop_is_not_reported_as_a_conflict(self):
        self.assertEqual(one(bundle("simmer", "PRIMITIVE", "simmer", "PRIMITIVE")).reason_code, "self_loop")

    def test_ambiguous_direction_for_a_precedes_edge_with_no_text(self):
        item = one(bundle("place_pan", "PRIMITIVE", "add_water", "PRIMITIVE", description=""))
        self.assertEqual(item.reason_code, "ambiguous_direction")
        self.assertEqual(item.group, "C")

    def test_a_described_precedes_edge_still_stamps(self):
        result = classify([bundle("place_pan", "PRIMITIVE", "add_water", "PRIMITIVE", "comes first")])
        self.assertEqual(result.parked, [])
        self.assertEqual(result.stamped[0].edge_type, "precedes")


class GroupDTests(unittest.TestCase):
    def test_empty_description_blocks_deferral(self):
        # Station 3 would have nothing to read, so it parks instead of deferring.
        item = one(bundle("place_pan", "PRIMITIVE", "pan_on_stove", "STATE", description=""))
        self.assertEqual(item.reason_code, "empty_description")
        self.assertEqual(item.group, "D")

    def test_whitespace_only_description_counts_as_empty(self):
        self.assertEqual(
            one(bundle("a", "PRIMITIVE", "b", "STATE", description="   \n ")).reason_code,
            "empty_description",
        )

    def test_dangling_endpoint_from_a_raw_document_id(self):
        item = one(bundle("chai_Entities/deleted", "PRIMITIVE", "b", "STATE"))
        self.assertEqual(item.reason_code, "dangling_endpoint")
        self.assertEqual(item.group, "D")

    def test_dangling_endpoint_from_station_1_unnamed_marker(self):
        self.assertEqual(one(bundle("(unnamed)", "PRIMITIVE", "b", "STATE")).reason_code, "dangling_endpoint")

    def test_alias_mismatch(self):
        parked = park(
            bundle("THE CUP", "OBJECT", "tea_in_cup", "STATE", relation_key="r1"),
            bundle("the_cup", "OBJECT", "stove_on", "STATE", relation_key="r2"),
        )
        self.assertEqual({item.reason_code for item in parked}, {"alias_mismatch"})
        self.assertEqual(parked[0].group, "D")

    def test_alias_mismatch_never_overrides_a_good_classification(self):
        result = classify(
            [
                bundle("THE CUP", "OBJECT", "serve", "PRIMITIVE", relation_key="r1"),
                bundle("the_cup", "OBJECT", "strain", "PRIMITIVE", relation_key="r2"),
            ]
        )
        self.assertEqual(result.parked, [])
        self.assertEqual(len(result.stamped), 2)


class GroupATests(unittest.TestCase):
    def test_unmapped_pairs_are_the_normal_case(self):
        for source_type, target_type in (
            ("OBJECT", "STATE"),
            ("STATE", "STATE"),
            ("SKILL", "OBJECT"),
            ("SKILL", "STATE"),
            ("OBJECT", "OBJECT"),
            ("SKILL", "SKILL"),
        ):
            item = one(bundle("x", source_type, "y", target_type))
            self.assertEqual(item.reason_code, "unmapped_pair", f"{source_type}->{target_type}")
            self.assertEqual(item.group, "A")


class PriorityTests(unittest.TestCase):
    """Section 9: B -> C -> D -> A; the most actionable code wins."""

    def test_prd_worked_example_mistyped_beats_directionless(self):
        # Mis-typed *and* directionless: reports the fixable suspect_type.
        item = one(bundle("add_water", "OBJECT", "boil_water", "OBJECT", description=""))
        self.assertEqual(item.reason_code, "suspect_type")

    def test_missing_type_beats_self_loop(self):
        self.assertEqual(one(bundle("x", "WIDGET", "x", "WIDGET")).reason_code, "missing_type")

    def test_self_loop_beats_empty_description(self):
        self.assertEqual(
            one(bundle("a", "PRIMITIVE", "a", "STATE", description="")).reason_code, "self_loop"
        )

    def test_dangling_endpoint_beats_unmapped_pair(self):
        self.assertEqual(
            one(bundle("x_Entities/gone", "OBJECT", "b", "STATE")).reason_code, "dangling_endpoint"
        )

    def test_unmapped_pair_loses_to_everything(self):
        self.assertEqual(one(bundle("add_water", "OBJECT", "y", "STATE")).reason_code, "suspect_type")


class ParkedPayloadTests(unittest.TestCase):
    """Section 8: the parked pile is a diagnostic, not a graveyard."""

    def test_carries_the_full_shared_payload(self):
        item = one(bundle("stove", "OBJECT", "stove_on", "STATE", "a note", relation_key="r42"))
        payload = item.to_dict()
        self.assertEqual(payload["source"], {"name": "stove", "type": "OBJECT"})
        self.assertEqual(payload["target"], {"name": "stove_on", "type": "STATE"})
        self.assertEqual(payload["description"], "a note")
        self.assertEqual(payload["relation_key"], "r42")
        self.assertEqual(payload["station"], "station2:rule_preclassifier")

    def test_carries_its_group_and_what_that_group_means(self):
        payload = one(bundle("stove", "OBJECT", "stove_on", "STATE")).to_dict()
        self.assertEqual(payload["group"], "A")
        self.assertEqual(payload["group_meaning"], GROUP_MEANING["A"])

    def test_detail_explains_the_code(self):
        self.assertTrue(one(bundle("stove", "OBJECT", "stove_on", "STATE")).detail)


if __name__ == "__main__":
    unittest.main()
