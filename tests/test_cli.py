"""End-to-end: FR-3, FR-8, Section 12 error matrix, Section 13 acceptance criteria."""

from __future__ import annotations

import io
import json
import unittest

from kg_read_harness.cli import run
from kg_read_harness.config import load_config
from kg_read_harness.errors import (
    EXIT_ATTRIBUTE_MISMATCH,
    EXIT_EMPTY_RESULT,
    EXIT_MISSING_COLLECTION,
    EXIT_OK,
    AttributeMismatchError,
    CollectionNotFoundError,
    EmptyResultError,
    HarnessError,
)
from tests import chai_fixture as chai
from tests.fake_arango import FakeWriteAttempt, make_db


def execute(env_overrides=None, db=None, relations_kwargs=None, entities=None):
    """Run the harness against a fake DB, returning (exit_code, stdout, stderr)."""
    config = load_config(chai.env(**(env_overrides or {})))
    if db is None:
        db = make_db(
            chai.entities() if entities is None else entities,
            chai.relations(**(relations_kwargs or {})),
        )
    out, err = io.StringIO(), io.StringIO()
    code = run(config, stdout=out, stderr=err, db=db)
    return code, out.getvalue(), err.getvalue(), db


class HappyPathTests(unittest.TestCase):
    def test_prints_every_bundle_and_a_correct_summary(self):
        code, out, _, _ = execute()
        self.assertEqual(code, EXIT_OK)
        expected = (
            len(chai.PRIMITIVES) + len(chai.EFFECTS) + len(chai.PRECONDITIONS) + len(chai.USES)
        )
        listing = [line for line in out.splitlines() if line.startswith("[")]
        self.assertEqual(len(listing), expected)
        self.assertIn("Summary", out)
        self.assertIn("TOTAL", out)

    def test_acceptance_chai_summary_has_the_pairs_the_bridge_needs(self):
        """Section 13: non-zero PRIMITIVE->STATE and SKILL->PRIMITIVE counts."""
        _, out, _, _ = execute()
        summary = out.split("Summary")[1]
        rows = {}
        for line in summary.splitlines():
            parts = line.replace("→", "->").split("->")
            if len(parts) == 2 and parts[1].strip():
                source = parts[0].strip()
                target, _, count = parts[1].strip().rpartition(" ")
                if count.strip().isdigit():
                    rows[(source, target.strip())] = int(count)
        self.assertGreater(rows.get(("PRIMITIVE", "STATE"), 0), 0)
        self.assertGreater(rows.get(("SKILL", "PRIMITIVE"), 0), 0)

    def test_header_reports_scale_without_secrets(self):
        code, out, _, _ = execute({"ARANGO_PASSWORD": "s3cr3t"})
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Bridge Station 1", out)
        self.assertIn("masala_chai_Relations", out)
        self.assertNotIn("s3cr3t", out)

    def test_dangling_endpoint_is_warned_about_and_counted(self):
        _, out, _, _ = execute()
        self.assertIn("dangling endpoint", out)
        self.assertIn("skipped (dangling endpoints)", out)

    def test_json_mode_puts_a_pipeable_array_on_stdout(self):
        code, out, err, _ = execute({"OUTPUT_FORMAT": "json"})
        self.assertEqual(code, EXIT_OK)
        payload = json.loads(out)
        self.assertTrue(payload)
        self.assertEqual(
            sorted(payload[0]), ["description", "relation_key", "source", "target"]
        )
        # Header, warnings and summary stay off stdout so the pipe is clean.
        self.assertIn("Summary", err)
        self.assertIn("Bridge Station 1", err)

    def test_limit_and_filter_are_honoured_together(self):
        code, out, _, _ = execute({"LIMIT": "4", "ENTITY_TYPE_FILTER": "PRIMITIVE,STATE"})
        self.assertEqual(code, EXIT_OK)
        listing = [line for line in out.splitlines() if line.startswith("[")]
        self.assertLessEqual(len(listing), 4)
        for line in listing:
            self.assertNotIn("(SKILL)", line)
            self.assertNotIn("(OBJECT)", line)

    def test_run_performs_zero_writes(self):
        """Section 13: verified by a database that refuses every mutation."""
        code, _, _, db = execute()
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(db.queries)  # it really did talk to the database

    def test_a_write_would_have_been_caught(self):
        """Sanity-check the tripwire the previous test relies on."""
        db = make_db(chai.entities(), chai.relations())
        with self.assertRaises(FakeWriteAttempt):
            db.aql.execute("INSERT {a: 1} INTO masala_chai_Entities")
        with self.assertRaises(FakeWriteAttempt):
            db.collection("masala_chai_Entities").insert({"a": 1})


class ErrorMatrixTests(unittest.TestCase):
    """One test per row of PRD Section 12 (missing env vars live in test_config)."""

    def test_collection_not_found(self):
        with self.assertRaises(CollectionNotFoundError) as caught:
            execute({"RELATION_COLLECTION": "nope_Relations"})
        self.assertIn("nope_Relations", caught.exception.message)
        self.assertIn("Graph Explorer", caught.exception.hint)
        self.assertEqual(caught.exception.exit_code, EXIT_MISSING_COLLECTION)

    def test_entity_type_attribute_absent(self):
        with self.assertRaises(AttributeMismatchError) as caught:
            execute({"ENTITY_TYPE_FIELD": "kind"})
        self.assertIn("kind", caught.exception.message)
        self.assertIn("ENTITY_TYPE_FIELD", caught.exception.hint)
        self.assertEqual(caught.exception.exit_code, EXIT_ATTRIBUTE_MISMATCH)

    def test_description_attribute_absent(self):
        with self.assertRaises(AttributeMismatchError) as caught:
            execute({"DESCRIPTION_FIELD": "note"})
        self.assertIn("note", caught.exception.message)
        self.assertIn("DESCRIPTION_FIELD", caught.exception.hint)

    def test_vector_rag_only_build_has_no_entities(self):
        with self.assertRaises(EmptyResultError) as caught:
            execute(entities=[])
        self.assertIn("no entities", caught.exception.message)
        self.assertIn("VectorRAG-only", caught.exception.hint)
        self.assertEqual(caught.exception.exit_code, EXIT_EMPTY_RESULT)

    def test_relation_collection_empty(self):
        db = make_db(chai.entities(), [])
        with self.assertRaises(EmptyResultError) as caught:
            execute(db=db)
        self.assertIn("no relationships", caught.exception.message)

    def test_no_relationship_of_the_requested_type(self):
        with self.assertRaises(EmptyResultError) as caught:
            execute({"RELATION_TYPE": "DEPENDS_ON"})
        self.assertIn("DEPENDS_ON", caught.exception.message)
        # The hint names what the KG actually contains.
        self.assertIn("RELATED_TO", caught.exception.hint)

    def test_absent_type_is_not_misreported_as_an_attribute_mismatch(self):
        """Other relation types in the same collection need not share a schema.

        A live KG holds RELATED_TO next to IN_COMMUNITY edges that carry no
        description; asking for a type that does not exist must report exactly
        that, not an attribute problem found on an unrelated document.
        """
        relations = chai.relations()
        relations.append(
            {
                "_key": "z999",
                "_id": "masala_chai_Relations/z999",
                "_from": "masala_chai_Entities/serve",
                "_to": "masala_chai_Entities/cup",
                "type": "IN_COMMUNITY",
            }
        )
        db = make_db(chai.entities(), relations)
        with self.assertRaises(EmptyResultError) as caught:
            execute({"RELATION_TYPE": "DEPENDS_ON"}, db=db)
        self.assertIn("DEPENDS_ON", caught.exception.message)

    def test_schemaless_sibling_types_do_not_break_the_happy_path(self):
        relations = chai.relations()
        relations.append(
            {
                "_key": "z999",
                "_id": "masala_chai_Relations/z999",
                "_from": "masala_chai_Entities/serve",
                "_to": "masala_chai_Entities/cup",
                "type": "IN_COMMUNITY",
            }
        )
        code, out, _, _ = execute(db=make_db(chai.entities(), relations))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("TOTAL", out)

    def test_filter_that_matches_nothing_is_reported(self):
        with self.assertRaises(EmptyResultError) as caught:
            execute({"ENTITY_TYPE_FILTER": "OBJECT"})
        self.assertIn("OBJECT", caught.exception.message)

    def test_non_edge_relation_collection_warns(self):
        db = make_db(chai.entities(), chai.relations(), relation_is_edge=False)
        code, out, _, _ = execute(db=db)
        self.assertEqual(code, EXIT_OK)
        self.assertIn("not an edge collection", out)

    def test_every_failure_mode_exits_non_zero(self):
        """Section 13: each Section 12 condition yields a clear message and code != 0."""
        for exception in (
            CollectionNotFoundError("m", "h"),
            AttributeMismatchError("m", "h"),
            EmptyResultError("m", "h"),
        ):
            self.assertNotEqual(exception.exit_code, EXIT_OK)
            self.assertIn("m", exception.render())
            self.assertIn("h", exception.render())
            self.assertIsInstance(exception, HarnessError)


class BridgeReuseTests(unittest.TestCase):
    def test_station_2_can_route_bundles_by_type_pair_unchanged(self):
        """Section 14: the read function is the bridge's input stage as-is."""
        from kg_read_harness import load_config as public_load_config
        from kg_read_harness import read_relationship_bundles

        config = public_load_config(chai.env())
        db = make_db(chai.entities(), chai.relations())
        routed: dict[tuple[str, str], list] = {}
        for bundle in read_relationship_bundles(db, config):
            routed.setdefault(bundle.type_pair, []).append(bundle)
        self.assertIn(("PRIMITIVE", "STATE"), routed)
        self.assertIn(("SKILL", "PRIMITIVE"), routed)
        self.assertIn(("PRIMITIVE", "OBJECT"), routed)


if __name__ == "__main__":
    unittest.main()
