"""The decision table and reason taxonomy are configuration — check their shape.

These tests exist so that "adding an ontology type is adding a row" stays true:
they fail loudly if a row or reason code is added without its group, priority or
orientation metadata.
"""

from __future__ import annotations

import unittest

from rule_preclassifier import table


class DecisionTableTests(unittest.TestCase):
    def test_covers_every_pair_in_the_prd(self):
        self.assertEqual(
            set(table.DECISION_TABLE),
            {
                ("PRIMITIVE", "SKILL"),
                ("PRIMITIVE", "PRIMITIVE"),
                ("OBJECT", "PRIMITIVE"),
                ("PRIMITIVE", "STATE"),
            },
        )

    def test_keys_are_sorted_so_the_pair_is_unordered(self):
        for key in table.DECISION_TABLE:
            self.assertEqual(list(key), sorted(key), f"{key} is not stored sorted")

    def test_lookup_is_order_independent(self):
        self.assertIs(table.lookup("SKILL", "PRIMITIVE"), table.lookup("PRIMITIVE", "SKILL"))
        self.assertIs(table.lookup("PRIMITIVE", "STATE"), table.lookup("STATE", "PRIMITIVE"))

    def test_only_primitive_state_defers(self):
        deferring = [key for key, rule in table.DECISION_TABLE.items() if rule.outcome == table.DEFER]
        self.assertEqual(deferring, [("PRIMITIVE", "STATE")])

    def test_deferred_row_offers_both_candidates(self):
        rule = table.lookup("PRIMITIVE", "STATE")
        self.assertEqual(rule.edge_type, (table.REQUIRES, table.PRODUCES))

    def test_orientation_is_implied_only_where_the_prd_says(self):
        implied = {
            key for key, rule in table.DECISION_TABLE.items() if rule.orientation_is_implied
        }
        self.assertEqual(implied, {("PRIMITIVE", "SKILL"), ("OBJECT", "PRIMITIVE")})

    def test_precedes_leaves_direction_open(self):
        rule = table.lookup("PRIMITIVE", "PRIMITIVE")
        self.assertEqual(rule.edge_type, table.PRECEDES)
        self.assertFalse(rule.orientation_is_implied)

    def test_head_and_tail_types_belong_to_their_pair(self):
        for key, rule in table.DECISION_TABLE.items():
            if rule.orientation_is_implied:
                self.assertIn(rule.head_type, key)
                self.assertIn(rule.tail_type, key)
                self.assertNotEqual(rule.head_type, rule.tail_type)

    def test_unmapped_pairs_have_no_row(self):
        for pair in (("OBJECT", "STATE"), ("STATE", "STATE"), ("SKILL", "OBJECT"), ("SKILL", "STATE")):
            self.assertIsNone(table.lookup(*pair), f"{pair} should be unmapped")


class ReasonTaxonomyTests(unittest.TestCase):
    def test_every_code_has_a_group_and_a_priority(self):
        self.assertEqual(set(table.REASON_PRIORITY), set(table.REASON_GROUPS))

    def test_priority_runs_b_then_c_then_d_then_a(self):
        # Asserted as a pattern, not a fixed list, so later stations can
        # contribute codes without the invariant needing an edit.
        groups = [table.group_of(code) for code in table.REASON_PRIORITY]
        self.assertEqual(groups, sorted(groups, key="BCDA".index))
        self.assertEqual(set(groups), {"B", "C", "D", "A"})

    def test_suspect_type_outranks_ambiguous_direction(self):
        # The PRD's worked example: the fixable code wins over the blander one.
        self.assertLess(
            table.reason_rank(table.SUSPECT_TYPE), table.reason_rank(table.AMBIGUOUS_DIRECTION)
        )

    def test_unmapped_pair_is_the_last_resort(self):
        self.assertEqual(table.REASON_PRIORITY[-1], table.UNMAPPED_PAIR)

    def test_blocking_and_explanatory_partition_the_codes(self):
        groups = (
            table.BLOCKING_REASONS,
            table.EXPLANATORY_REASONS,
            table.STATION_3_REASONS,
            table.STATION_4_REASONS,
        )
        for first in range(len(groups)):
            for second in range(first + 1, len(groups)):
                self.assertEqual(groups[first] & groups[second], set())
        self.assertEqual(set().union(*groups), set(table.REASON_PRIORITY))

    def test_station_2_never_produces_a_later_station_code(self):
        from rule_preclassifier import classify
        from .station2_fixture import bundle

        codes = {
            item.reason_code
            for pair in (("OBJECT", "STATE"), ("WIDGET", "PRIMITIVE"), ("STATE", "STATE"))
            for item in classify([bundle("x", pair[0], "y", pair[1])]).parked
        }
        self.assertEqual(codes & (table.STATION_3_REASONS | table.STATION_4_REASONS), set())

    def test_every_group_has_a_meaning(self):
        self.assertEqual(set(table.GROUP_MEANING), set(table.REASON_GROUPS.values()))


class SynonymTests(unittest.TestCase):
    def test_synonyms_resolve_into_the_ontology(self):
        for source, target in table.TYPE_SYNONYMS.items():
            self.assertIn(target, table.ONTOLOGY)
            self.assertNotIn(source, table.ONTOLOGY, f"{source} is already an ontology type")

    def test_synonym_keys_are_upper_case(self):
        # canonical_type() upper-cases before looking up, so lower-case keys
        # would silently never match.
        for source in table.TYPE_SYNONYMS:
            self.assertEqual(source, source.upper())


if __name__ == "__main__":
    unittest.main()
