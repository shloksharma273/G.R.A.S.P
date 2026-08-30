"""FR-4, FR-5 and the read-only constraint (Section 10)."""

from __future__ import annotations

import unittest

from kg_read_harness.client import assert_read_only
from kg_read_harness.config import load_config
from kg_read_harness.errors import ReadOnlyViolation
from kg_read_harness.read import (
    UNKNOWN_TYPE,
    ReadStats,
    build_query,
    read_relationship_bundles,
    row_to_bundle,
)
from tests import chai_fixture as chai
from tests.fake_arango import make_db


def build_fake_db(**relation_kwargs):
    return make_db(chai.entities(), chai.relations(**relation_kwargs))


class BundleShapeTests(unittest.TestCase):
    def test_row_maps_onto_the_section_7_contract(self):
        bundle = row_to_bundle(
            {
                "relation_key": "r001",
                "from_id": "E/make_masala_chai",
                "to_id": "E/place_pan",
                "source_name": "make_masala_chai",
                "source_type": "SKILL",
                "target_name": "place_pan",
                "target_type": "PRIMITIVE",
                "description": "composed of",
            }
        )
        self.assertEqual(
            bundle.to_dict(),
            {
                "relation_key": "r001",
                "source": {"name": "make_masala_chai", "type": "SKILL"},
                "target": {"name": "place_pan", "type": "PRIMITIVE"},
                "description": "composed of",
            },
        )
        self.assertEqual(bundle.type_pair, ("SKILL", "PRIMITIVE"))

    def test_missing_values_degrade_instead_of_crashing(self):
        bundle = row_to_bundle(
            {
                "relation_key": "r999",
                "from_id": "E/x",
                "to_id": "E/y",
                "source_name": None,
                "source_type": None,
                "target_name": "y",
                "target_type": "  ",
                "description": None,
            }
        )
        self.assertEqual(bundle.source.name, "E/x")
        self.assertEqual(bundle.source.type, UNKNOWN_TYPE)
        self.assertEqual(bundle.target.type, UNKNOWN_TYPE)
        self.assertEqual(bundle.description, "")


class ReadTests(unittest.TestCase):
    def setUp(self):
        self.config = load_config(chai.env())
        self.db = build_fake_db()

    def test_reads_every_related_to_edge(self):
        stats = ReadStats()
        bundles = list(read_relationship_bundles(self.db, self.config, stats))
        expected = len(chai.PRIMITIVES) + len(chai.EFFECTS) + len(chai.PRECONDITIONS) + len(chai.USES)
        self.assertEqual(len(bundles), expected)
        self.assertEqual(stats.emitted, expected)

    def test_other_relation_types_are_excluded(self):
        pairs = {b.type_pair for b in read_relationship_bundles(self.db, self.config)}
        self.assertIn(("SKILL", "PRIMITIVE"), pairs)
        self.assertIn(("PRIMITIVE", "STATE"), pairs)
        descriptions = [b.description for b in read_relationship_bundles(self.db, self.config)]
        self.assertNotIn("Mentioned together in the overview.", descriptions)

    def test_dangling_endpoint_is_skipped_warned_and_counted(self):
        stats = ReadStats()
        warnings: list[str] = []
        bundles = list(
            read_relationship_bundles(self.db, self.config, stats, on_warning=warnings.append)
        )
        self.assertEqual(stats.skipped_dangling, 1)
        self.assertEqual(len(warnings), 1)
        self.assertIn("dangling endpoint", warnings[0])
        self.assertNotIn("deleted_entity", [b.target.name for b in bundles])
        # The read continues past the skip.
        self.assertEqual(stats.rows_scanned, stats.emitted + 1)

    def test_limit_bounds_the_rows_read(self):
        config = load_config(chai.env(LIMIT="5"))
        self.assertLessEqual(len(list(read_relationship_bundles(self.db, config))), 5)

    def test_entity_type_filter_restricts_both_endpoints(self):
        config = load_config(chai.env(ENTITY_TYPE_FILTER="PRIMITIVE,STATE"))
        pairs = {b.type_pair for b in read_relationship_bundles(self.db, config)}
        self.assertEqual(pairs, {("PRIMITIVE", "STATE")})

    def test_entity_type_filter_ignores_case(self):
        """Live AutoGraph builds emit lowercase type values such as 'tool'."""
        config = load_config(chai.env(ENTITY_TYPE_FILTER="primitive,state"))
        pairs = {b.type_pair for b in read_relationship_bundles(self.db, config)}
        self.assertEqual(pairs, {("PRIMITIVE", "STATE")})

    def test_entity_type_filter_matches_lowercase_type_values(self):
        entities = [
            {**doc, "entity_type": doc["entity_type"].lower()} for doc in chai.entities()
        ]
        db = make_db(entities, chai.relations())
        config = load_config(chai.env(ENTITY_TYPE_FILTER="PRIMITIVE,STATE"))
        pairs = {b.type_pair for b in read_relationship_bundles(db, config)}
        self.assertEqual(pairs, {("primitive", "state")})

    def test_attribute_names_are_configurable(self):
        db = make_db(
            chai.entities(type_field="type", name_field="label"),
            chai.relations(type_field="rel_type", description_field="note"),
        )
        config = load_config(
            chai.env(
                ENTITY_TYPE_FIELD="type",
                ENTITY_NAME_FIELD="label",
                RELATION_TYPE_FIELD="rel_type",
                DESCRIPTION_FIELD="note",
            )
        )
        bundles = list(read_relationship_bundles(db, config))
        self.assertTrue(bundles)
        self.assertTrue(all(b.source.type != UNKNOWN_TYPE for b in bundles))
        self.assertTrue(all(b.description for b in bundles))

    def test_output_is_repeatable(self):
        first = [b.to_dict() for b in read_relationship_bundles(self.db, self.config)]
        second = [b.to_dict() for b in read_relationship_bundles(self.db, self.config)]
        self.assertEqual(first, second)


class ReadOnlyTests(unittest.TestCase):
    def test_the_read_query_contains_no_write_operation(self):
        config = load_config(chai.env(ENTITY_TYPE_FILTER="SKILL,PRIMITIVE", LIMIT="3"))
        assert_read_only(build_query(config))  # must not raise

    def test_guard_rejects_mutating_queries(self):
        for query in (
            "INSERT {a: 1} INTO things",
            "FOR d IN things REMOVE d IN things",
            "FOR d IN things UPDATE d WITH {x: 1} IN things",
            "UPSERT {a: 1} INSERT {a: 1} UPDATE {a: 2} IN things",
        ):
            with self.assertRaises(ReadOnlyViolation):
                assert_read_only(query)

    def test_guard_allows_ordinary_reads(self):
        assert_read_only("FOR d IN things FILTER d.type == 'RELATED_TO' RETURN d")

    def test_a_full_read_issues_no_write(self):
        db = build_fake_db()
        list(read_relationship_bundles(db, load_config(chai.env())))
        self.assertTrue(db.queries)
        for query in db.queries:
            assert_read_only(query)


if __name__ == "__main__":
    unittest.main()
