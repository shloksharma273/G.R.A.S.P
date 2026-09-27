"""The direct route: a rulebook file into the PlanGraph, without AutoGraph."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path

from layer2_planning import load_planner_config, plan_command
from layer2_planning.retrieve import load_skills
from plangraph_writer.config import load_writer_config
from plangraph_writer.schema import Schema
from rulebook_generator import parse_file, render
from rulebook_generator.direct import find_rulebooks, ingest_all, ingest_rulebook
from rulebook_generator.direct_cli import EXIT_NOTHING_WRITTEN, run
from rulebook_generator.schema import Interface, Primitive, Rulebook
from rulebook_generator.validate import to_stamped_edges

from .corpus_fixture import ENV
from .fake_writable_arango import FOREIGN_COLLECTIONS, make_db

SCHEMA = Schema(prefix="roboticsPlanner")


def writer(**overrides):
    return load_writer_config({**ENV, **overrides}, dry_run=False)


def write_rulebook(rulebook: Rulebook) -> str:
    handle, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(handle, "w", encoding="utf-8") as file:
        file.write(render(rulebook))
    return path


TEA = Rulebook(
    skill="make_tea",
    title="Tea",
    objects=["kettle", "cup"],
    states=["water_boiling", "tea_in_cup"],
    primitives=[
        Primitive("boil_water", "The robot boils the water.", [], ["water_boiling"], ["kettle"]),
        Primitive("pour_tea", "The robot pours the tea.", ["water_boiling"], ["tea_in_cup"], ["cup"]),
    ],
)


class StampedEdgeTests(unittest.TestCase):
    """The step the gate and the ingest share, so the two cannot drift."""

    def test_the_rulebook_supplies_the_labels_not_a_model(self):
        edges = to_stamped_edges(TEA)
        labelled = {
            (e.bundle.source.name, e.bundle.target.name): e.edge_type
            for e in edges
            if e.edge_type in ("requires", "produces")
        }
        self.assertEqual(labelled[("boil_water", "water_boiling")], "produces")
        self.assertEqual(labelled[("pour_tea", "water_boiling")], "requires")

    def test_every_edge_type_is_stamped(self):
        types = {e.edge_type for e in to_stamped_edges(TEA)}
        self.assertEqual(types, {"decomposes_to", "uses", "requires", "produces"})


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.path = write_rulebook(TEA)
        self.addCleanup(os.unlink, self.path)
        self.db = make_db()

    def test_a_rulebook_becomes_a_scoped_subgraph(self):
        result = ingest_rulebook(self.path, self.db, writer())
        self.assertTrue(result.ok)
        self.assertEqual(result.scope, "make_tea")
        self.assertEqual(self.db.collection(SCHEMA.skills_collection).count(), 1)

    def test_the_ordering_is_derived(self):
        result = ingest_rulebook(self.path, self.db, writer())
        self.assertEqual(result.derived, 1)  # boil_water -> pour_tea

    def test_the_written_graph_plans(self):
        """The point of the whole exercise."""
        ingest_rulebook(self.path, self.db, writer())
        answer = plan_command("make me tea", self.db, load_planner_config(ENV, use_llm=False))
        self.assertEqual(answer.goal, "make_tea")
        self.assertEqual(answer.actions, ["boil_water", "pour_tea"])

    def test_a_dry_run_writes_nothing(self):
        ingest_rulebook(self.path, self.db, writer(), require_verdict=True)
        fresh = make_db()
        ingest_rulebook(self.path, fresh, load_writer_config(ENV, dry_run=True))
        self.assertFalse(fresh.has_collection(SCHEMA.skills_collection))

    def test_it_is_idempotent(self):
        first = ingest_rulebook(self.path, self.db, writer())
        second = ingest_rulebook(self.path, self.db, writer())
        self.assertEqual(first.write.verified, second.write.verified)

    def test_a_rejected_rulebook_is_skipped_by_default(self):
        """A cycle cannot be planned, so writing it would poison the graph."""
        cyclic = Rulebook(
            skill="loop",
            states=["pan_hot", "pan_full"],
            primitives=[
                Primitive("one", "Do the first thing.", ["pan_full"], ["pan_hot"], []),
                Primitive("two", "Do the second thing.", ["pan_hot"], ["pan_full"], []),
            ],
        )
        path = write_rulebook(cyclic)
        self.addCleanup(os.unlink, path)
        result = ingest_rulebook(path, self.db, writer())
        self.assertFalse(result.ok)
        self.assertIn("rejected", result.skipped)
        self.assertFalse(self.db.has_collection(SCHEMA.skills_collection))

    def test_force_writes_a_rejected_rulebook(self):
        cyclic = Rulebook(
            skill="loop",
            states=["pan_hot", "pan_full"],
            primitives=[
                Primitive("one", "Do the first thing.", ["pan_full"], ["pan_hot"], []),
                Primitive("two", "Do the second thing.", ["pan_hot"], ["pan_full"], []),
            ],
        )
        path = write_rulebook(cyclic)
        self.addCleanup(os.unlink, path)
        result = ingest_rulebook(path, self.db, writer(), require_verdict=False)
        self.assertTrue(result.ok)

    def test_a_flagged_rulebook_is_still_written(self):
        """A flag wants a human eye; it does not make the graph unplannable."""
        flagged = Rulebook(
            skill="thin",
            states=["done"],
            primitives=[Primitive("act", "Do it.", ["missing"], ["done"], [])],
        )
        path = write_rulebook(flagged)
        self.addCleanup(os.unlink, path)
        result = ingest_rulebook(path, self.db, writer())
        self.assertTrue(result.ok)
        self.assertEqual(result.report.verdict, "flag_for_review")

    def test_a_file_that_is_not_a_rulebook_is_skipped(self):
        handle, path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write("# Just a document\n\nNothing structured here.\n")
        self.addCleanup(os.unlink, path)
        result = ingest_rulebook(path, self.db, writer())
        self.assertIn("no primitives", result.skipped)

    def test_no_foreign_collection_is_touched(self):
        ingest_rulebook(self.path, self.db, writer())
        for name in FOREIGN_COLLECTIONS:
            self.assertEqual(len(self.db.collection(name).documents), 1)


GOTO = Rulebook(
    skill="go_to_location",
    title="Go To Location",
    objects=["robot"],
    states=["robot_localized", "robot_at_goal"],
    primitives=[
        Primitive(
            "set_initial_pose", "The robot tells AMCL where it is.", [], ["robot_localized"], ["robot"],
            interface=Interface("topic", "/initialpose", "geometry_msgs/msg/PoseWithCovarianceStamped"),
        ),
        Primitive(
            "navigate_to_goal", "The robot drives to the goal.", ["robot_localized"], ["robot_at_goal"],
            ["robot"], interface=Interface("action", "/navigate_to_pose", "nav2_msgs/action/NavigateToPose"),
        ),
    ],
)


class InterfaceTests(unittest.TestCase):
    """A step's execution handle survives the ingest and reaches the plan."""

    def setUp(self):
        self.db = make_db()

    def ingest(self, rulebook):
        path = write_rulebook(rulebook)
        self.addCleanup(os.unlink, path)
        ingest_rulebook(path, self.db, writer())

    def test_each_step_says_how_to_run_it(self):
        self.ingest(GOTO)
        answer = plan_command("go to location", self.db, load_planner_config(ENV, use_llm=False))
        self.assertEqual(
            [step.interface for step in answer.steps],
            [
                {"kind": "topic", "name": "/initialpose",
                 "type": "geometry_msgs/msg/PoseWithCovarianceStamped"},
                {"kind": "action", "name": "/navigate_to_pose",
                 "type": "nav2_msgs/action/NavigateToPose"},
            ],
        )

    def test_a_rulebook_without_handles_plans_without_them(self):
        self.ingest(TEA)
        answer = plan_command("make me tea", self.db, load_planner_config(ENV, use_llm=False))
        self.assertEqual([step.interface for step in answer.steps], [None, None])

    def test_the_handle_stays_out_of_the_skill_match_text(self):
        """It says how a step runs, not what the task is - it would only dilute the match."""
        self.ingest(GOTO)
        (skill,) = load_skills(self.db, SCHEMA)
        self.assertIn("navigate_to_goal", skill["description"])
        self.assertNotIn("/navigate_to_pose", skill["description"])
        self.assertNotIn("executed by", skill["description"])


class MultipleRulebookTests(unittest.TestCase):
    def test_each_lands_in_its_own_scope(self):
        coffee = Rulebook(
            skill="make_coffee",
            states=["water_boiling", "coffee_poured"],
            primitives=[
                Primitive("boil_water", "Boil it.", [], ["water_boiling"], []),
                Primitive("pour_coffee", "Pour it.", ["water_boiling"], ["coffee_poured"], []),
            ],
        )
        paths = [write_rulebook(TEA), write_rulebook(coffee)]
        for path in paths:
            self.addCleanup(os.unlink, path)

        db = make_db()
        run_result = ingest_all(paths, db, writer())
        self.assertEqual(len(run_result.written), 2)

        keys = set(db.collection(SCHEMA.vertex_collection("PRIMITIVE")).documents)
        # A shared primitive name gets one vertex per scope - the point of scoping.
        self.assertIn("make_tea__boil_water", keys)
        self.assertIn("make_coffee__boil_water", keys)

    def test_a_directory_is_expanded(self):
        directory = tempfile.mkdtemp()
        (Path(directory) / "rulebook_tea.md").write_text(render(TEA), encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(directory))
        self.assertEqual(len(find_rulebooks(directory)), 1)

    def test_the_real_corpus_ingests(self):
        """Every hand-written rulebook must survive the direct route."""
        db = make_db()
        paths = find_rulebooks("dataset") + [Path("masala_chai_rulebook.md")]
        result = ingest_all(paths, db, writer())
        self.assertEqual(len(result.skipped), 0, [r.skipped for r in result.skipped])
        self.assertEqual(len(result.written), 6)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.path = write_rulebook(TEA)
        self.addCleanup(os.unlink, self.path)
        for key, value in ENV.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)

    def test_a_dry_run_says_so(self):
        out = io.StringIO()
        code = run([self.path], write=False, stdout=out, db=make_db())
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out.getvalue())
        self.assertIn("make_tea", out.getvalue())

    def test_write_applies(self):
        db = make_db()
        out = io.StringIO()
        run([self.path], write=True, stdout=out, db=db)
        self.assertIn("WROTE", out.getvalue())
        self.assertEqual(db.collection(SCHEMA.skills_collection).count(), 1)

    def test_json_output(self):
        out = io.StringIO()
        run([self.path], write=True, output_format="json", stdout=out, db=make_db())
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["rulebooks"][0]["skill"], "make_tea")
        self.assertEqual(payload["rulebooks"][0]["derived_precedes"], 1)

    def test_nothing_written_exits_three(self):
        handle, path = tempfile.mkstemp(suffix=".md")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write("# not a rulebook\n")
        self.addCleanup(os.unlink, path)
        out = io.StringIO()
        self.assertEqual(run([path], write=True, stdout=out, db=make_db()), EXIT_NOTHING_WRITTEN)

    def test_a_missing_target_is_an_actionable_error(self):
        from kg_read_harness.errors import ConfigError

        with self.assertRaises(ConfigError):
            run(["no_such_directory_at_all"], stdout=io.StringIO(), db=make_db())

    def test_ascii_fallback(self):
        class AsciiStream(io.StringIO):
            encoding = "ascii"

        out = AsciiStream()
        run([self.path], write=True, stdout=out, db=make_db())
        out.getvalue().encode("ascii")


if __name__ == "__main__":
    unittest.main()
