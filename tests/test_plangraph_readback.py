"""Acceptance criterion 4 — the read contract Layer 2 depends on (PRD Section 8)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest

from direction_normalizer import normalize
from plangraph_writer import build, write_plangraph
from plangraph_writer.cli import build_parser, run, station4_streams
from plangraph_writer.config import load_writer_config
from plangraph_writer.readback import read_subgraph
from plangraph_writer.report import dump_json, print_report
from plangraph_writer.schema import Schema

from .fake_writable_arango import FOREIGN_COLLECTIONS, make_db
from .station4_fixture import chai_stamped, violated_constraints

ENV = {
    "ARANGO_URL": "http://localhost:8529",
    "ARANGO_DB": "test_shlok",
    "ARANGO_USERNAME": "root",
    "ARANGO_PASSWORD": "",
    "PROJECT_NAME": "roboticsPlanner",
}
SCHEMA = Schema(prefix="roboticsPlanner")


def written_db(scope=None):
    config = load_writer_config(ENV, dry_run=False)
    result = normalize(chai_stamped()[0])
    plan = build(result.finalized, result.derived, SCHEMA, skill_scope=scope)
    db = make_db()
    write_plangraph(plan, db, config)
    return db, plan, config


class ReadContractTests(unittest.TestCase):
    def setUp(self):
        self.db, self.plan, self.config = written_db()
        self.subgraph = read_subgraph(self.db, SCHEMA, "make_masala_chai")

    def test_the_traversal_finds_the_skill(self):
        self.assertEqual(self.subgraph.skill, "make_masala_chai")

    def test_it_returns_every_vertex(self):
        self.assertEqual(len(self.subgraph.primitives), 11)
        self.assertEqual(len(self.subgraph.states), 10)
        self.assertEqual(len(self.subgraph.objects), 8)

    def test_it_returns_every_edge_type(self):
        self.assertEqual(len(self.subgraph.precedes), 10)
        self.assertEqual(sum(len(v) for v in self.subgraph.requires.values()), 10)
        self.assertEqual(sum(len(v) for v in self.subgraph.produces.values()), 10)
        self.assertEqual(sum(len(v) for v in self.subgraph.uses.values()), 8)

    def test_the_subgraph_is_precondition_closed(self):
        """Every state a primitive needs is produced within the task."""
        self.assertEqual(self.subgraph.unmet_preconditions(), [])
        self.assertTrue(self.subgraph.is_precondition_closed())

    def test_it_is_ready_for_topological_sort(self):
        order = self.subgraph.topological_order()
        self.assertIsNotNone(order)
        self.assertEqual(violated_constraints(order), [])

    def test_the_recovered_plan_is_the_recipe(self):
        order = self.subgraph.topological_order()
        self.assertEqual(order[0], "place_pan")
        self.assertIn("serve", order)
        self.assertEqual(len(order), 11)

    def test_a_missing_scope_reads_back_empty(self):
        empty = read_subgraph(self.db, SCHEMA, "no_such_task")
        self.assertIsNone(empty.skill)
        self.assertEqual(empty.primitives, [])

    def test_scopes_never_bleed_into_one_another(self):
        """Section 5's hazard, checked through the read path."""
        config = load_writer_config(ENV, dry_run=False)
        result = normalize(chai_stamped()[0])
        write_plangraph(
            build(result.finalized, result.derived, SCHEMA, skill_scope="make_coffee"),
            self.db,
            config,
        )
        chai = read_subgraph(self.db, SCHEMA, "make_masala_chai")
        coffee = read_subgraph(self.db, SCHEMA, "make_coffee")
        self.assertEqual(len(chai.precedes), 10)
        self.assertEqual(len(coffee.precedes), 10)
        self.assertEqual(len(chai.primitives), 11)

    def test_reading_never_writes(self):
        before = {
            name: len(self.db.collection(name).documents) for name in SCHEMA.all_collections
        }
        read_subgraph(self.db, SCHEMA, "make_masala_chai")
        after = {
            name: len(self.db.collection(name).documents) for name in SCHEMA.all_collections
        }
        self.assertEqual(before, after)


class CliTests(unittest.TestCase):
    def setUp(self):
        result = normalize(chai_stamped()[0])
        self.station4 = {
            "finalized": [e.to_dict() for e in result.finalized],
            "derived": [e.to_dict() for e in result.derived],
        }
        handle, self.path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(self.station4, file)
        self.addCleanup(os.unlink, self.path)
        for key, value in ENV.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)

    def test_reads_a_station_4_result(self):
        finalized, derived = station4_streams(self.station4)
        self.assertEqual(len(finalized), 39)
        self.assertEqual(len(derived), 10)

    def test_rejects_something_that_is_not_station_4_output(self):
        from kg_read_harness.errors import ConfigError

        with self.assertRaises(ConfigError):
            station4_streams({"stamped": []})

    def test_the_default_is_a_dry_run(self):
        self.assertFalse(build_parser().parse_args([]).write)

    def test_dry_run_writes_nothing(self):
        db = make_db()
        out = io.StringIO()
        code = run(from_json=self.path, write=False, stdout=out, db=db)
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out.getvalue())
        for name in SCHEMA.all_collections:
            self.assertFalse(db.has_collection(name))

    def test_write_applies(self):
        db = make_db()
        out = io.StringIO()
        run(from_json=self.path, write=True, stdout=out, db=db)
        self.assertTrue(db.has_collection("roboticsPlanner_PlanEdges"))
        self.assertIn("write complete", out.getvalue())

    def test_scope_override(self):
        db = make_db()
        run(from_json=self.path, write=True, scope="custom_task", stdout=io.StringIO(), db=db)
        subgraph = read_subgraph(db, SCHEMA, "custom_task")
        self.assertEqual(len(subgraph.primitives), 11)

    def test_json_output(self):
        db = make_db()
        out = io.StringIO()
        run(from_json=self.path, write=True, output_format="json", stdout=out, db=db)
        payload = json.loads(out.getvalue())
        self.assertEqual(set(payload), {"plan", "write"})
        self.assertEqual(payload["plan"]["skill_scope"], "make_masala_chai")

    def test_a_full_cli_run_touches_no_foreign_collection(self):
        db = make_db()
        run(from_json=self.path, write=True, stdout=io.StringIO(), db=db)
        for name in FOREIGN_COLLECTIONS:
            self.assertEqual(len(db.collection(name).documents), 1)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.db, self.plan, self.config = written_db()
        from plangraph_writer.writer import write_plangraph as write

        self.report = write(self.plan, self.db, self.config)

    def test_report_shows_plan_and_write(self):
        out = io.StringIO()
        print_report(self.config, self.plan, self.report, out)
        text = out.getvalue()
        self.assertIn("Bridge Station 5", text)
        self.assertIn("decomposes_to", text)
        self.assertIn("Verified", text)

    def test_report_names_the_scope_and_build(self):
        out = io.StringIO()
        print_report(self.config, self.plan, self.report, out, listing=False)
        text = out.getvalue()
        self.assertIn("make_masala_chai", text)
        self.assertIn(self.plan.build_id, text)

    def test_report_never_prints_a_credential(self):
        config = load_writer_config({**ENV, "ARANGO_PASSWORD": "hunter2"}, dry_run=True)
        out = io.StringIO()
        print_report(config, self.plan, self.report, out, listing=False)
        self.assertNotIn("hunter2", out.getvalue())

    def test_ascii_fallback(self):
        class AsciiStream(io.StringIO):
            encoding = "ascii"

        out = AsciiStream()
        print_report(self.config, self.plan, self.report, out)
        out.getvalue().encode("ascii")

    def test_json_dump_round_trips(self):
        out = io.StringIO()
        dump_json(self.plan, self.report, out)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["plan"]["edges"], len(self.plan.edges))


if __name__ == "__main__":
    unittest.main()
