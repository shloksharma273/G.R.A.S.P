"""Partitioning a multi-rulebook KG, and the interactive shell."""

from __future__ import annotations

import io
import unittest

from direction_normalizer import normalize
from layer2_planning import load_planner_config
from layer2_planning.shell import Shell, interact
from plangraph_writer import build, write_plangraph
from plangraph_writer.config import load_writer_config
from plangraph_writer.partition import orphan_skills, partition_by_skill, skills_in

from .corpus_fixture import ENV, corpus_db, stamped_edges
from .fake_writable_arango import FOREIGN_COLLECTIONS, make_db
from .rulebook_fixture import all_rulebooks


def mixed_station4():
    """Every rulebook's edges in one undifferentiated stream, as one KG gives them."""
    edges = []
    for book in all_rulebooks():
        edges.extend(stamped_edges(book)[0])
    return normalize(edges)


class PartitionTests(unittest.TestCase):
    """One AutoGraph project routinely holds several rulebooks."""

    def setUp(self):
        self.result = mixed_station4()
        self.partitions = partition_by_skill(self.result.finalized, self.result.derived)

    def test_one_partition_per_skill(self):
        self.assertEqual(len(self.partitions), len(all_rulebooks()))

    def test_each_partition_holds_only_its_own_primitives(self):
        by_skill = {p.scope: p for p in self.partitions}
        for book in all_rulebooks():
            with self.subTest(skill=book.skill):
                partition = by_skill[book.skill]
                decomposed = {
                    e.tail if hasattr(e, "tail") else e["to"]
                    for e in partition.finalized
                    if (e.edge_type if hasattr(e, "edge_type") else e["edge_type"])
                    == "decomposes_to"
                }
                self.assertEqual(decomposed, set(book.primitives))

    def test_shared_names_land_in_both_partitions(self):
        """chai and burger both use turn_on_stove; each must get its own copy."""
        by_skill = {p.scope: p for p in self.partitions}

        def primitives(scope):
            return {
                e.tail
                for e in by_skill[scope].finalized
                if e.edge_type == "decomposes_to"
            }

        self.assertIn("turn_on_stove", primitives("make_masala_chai"))
        self.assertIn("turn_on_stove", primitives("cook_burger"))

    def test_no_partition_is_empty(self):
        for partition in self.partitions:
            self.assertGreater(partition.edge_count, 0)

    def test_skills_in_finds_only_decomposing_skills(self):
        self.assertEqual(len(skills_in(self.result.finalized)), len(all_rulebooks()))

    def test_a_skill_with_no_decomposition_is_not_a_partition(self):
        edges = [
            {"edge_type": "uses", "from": "a", "to": "b", "method": "rule"},
        ]
        self.assertEqual(partition_by_skill(edges, []), [])

    def test_orphan_skills_are_reported(self):
        orphans = orphan_skills(self.result.finalized, ["make_bed", "never_extracted"])
        self.assertEqual(orphans, ["never_extracted"])

    def test_partitioning_is_deterministic(self):
        again = partition_by_skill(self.result.finalized, self.result.derived)
        self.assertEqual([p.scope for p in self.partitions], [p.scope for p in again])


class MultiSkillWriteTests(unittest.TestCase):
    """Writing every partition into one PlanGraph, each under its own scope."""

    def setUp(self):
        self.config = load_writer_config(ENV, dry_run=False)
        self.db = make_db()
        self.result = mixed_station4()
        for partition in partition_by_skill(self.result.finalized, self.result.derived):
            plan = build(
                partition.finalized,
                partition.derived,
                self.config.schema,
                skill_scope=partition.scope,
            )
            write_plangraph(plan, self.db, self.config)

    def test_every_skill_is_written(self):
        skills = self.db.collection(self.config.schema.skills_collection).documents
        self.assertEqual(len(skills), len(all_rulebooks()))

    def test_scopes_are_distinct(self):
        edges = self.db.collection(self.config.schema.edge_collection).documents.values()
        scopes = {e["skill_scope"] for e in edges}
        self.assertEqual(len(scopes), len(all_rulebooks()))

    def test_a_shared_name_has_one_vertex_per_scope(self):
        keys = set(self.db.collection(self.config.schema.vertex_collection("PRIMITIVE")).documents)
        self.assertIn("make_masala_chai__turn_on_stove", keys)
        self.assertIn("cook_burger__turn_on_stove", keys)

    def test_rewriting_one_skill_leaves_the_others_alone(self):
        before = len(self.db.collection(self.config.schema.edge_collection).documents)
        partition = partition_by_skill(self.result.finalized, self.result.derived)[0]
        plan = build(
            partition.finalized, partition.derived, self.config.schema, skill_scope=partition.scope
        )
        write_plangraph(plan, self.db, self.config)
        self.assertEqual(
            len(self.db.collection(self.config.schema.edge_collection).documents), before
        )

    def test_no_foreign_collection_is_touched(self):
        for name in FOREIGN_COLLECTIONS:
            self.assertEqual(len(self.db.collection(name).documents), 1)


class ShellTests(unittest.TestCase):
    """The interactive session."""

    def setUp(self):
        self.db, _writer, _books = corpus_db()
        self.config = load_planner_config(ENV, use_llm=False)

    def _run(self, script: str) -> str:
        out = io.StringIO()
        interact(Shell(self.db, self.config, stdout=out), stdin=io.StringIO(script))
        return out.getvalue()

    def test_the_banner_reports_the_graph_and_retrieval_method(self):
        text = self._run(":q\n")
        self.assertIn("skills", text)
        self.assertIn("lexical", text)
        self.assertIn("graph-derived", text)

    def test_a_command_produces_a_plan(self):
        text = self._run("make me a masala chai\n:q\n")
        self.assertIn("place_pan", text)
        self.assertIn("Plan", text)

    def test_an_unresolvable_command_asks(self):
        text = self._run("reticulate the splines\n:q\n")
        self.assertIn("Clarification needed", text)

    def test_skills_lists_every_skill_with_step_counts(self):
        text = self._run(":skills\n:q\n")
        for skill in ("make_masala_chai", "cook_burger", "fold_tshirt"):
            self.assertIn(skill, text)
        self.assertIn("steps", text)

    def test_show_prints_one_skills_subgraph(self):
        text = self._run(":show burger\n:q\n")
        self.assertIn("assemble_burger", text)
        self.assertIn("requires", text)

    def test_show_reports_an_unknown_skill(self):
        self.assertIn("no skill matching", self._run(":show nonsense\n:q\n"))

    def test_json_prints_the_last_plan(self):
        text = self._run("make the bed\n:json\n:q\n")
        self.assertIn('"goal"', text)
        self.assertIn('"steps"', text)

    def test_json_before_planning_says_so(self):
        self.assertIn("nothing planned yet", self._run(":json\n:q\n"))

    def test_why_explains_a_resolution(self):
        text = self._run("cook a burger\n:why\n:q\n")
        self.assertIn("lexical retrieval", text)
        self.assertIn("came from the graph", text)

    def test_why_explains_a_clarification(self):
        text = self._run("xyzzy\n:why\n:q\n")
        self.assertIn("below the threshold", text)

    def test_threshold_can_be_changed_mid_session(self):
        text = self._run(":threshold 0.99\nmake me a masala chai\n:q\n")
        self.assertIn("match threshold: 0.99", text)
        self.assertIn("Clarification needed", text)

    def test_a_bad_threshold_is_rejected(self):
        self.assertIn("between 0 and 1", self._run(":threshold 5\n:q\n"))
        self.assertIn("needs a number", self._run(":threshold high\n:q\n"))

    def test_llm_toggle_without_a_key_explains_itself(self):
        text = self._run(":llm on\n:q\n")
        self.assertIn("no LLM key configured", text)

    def test_unknown_colon_command(self):
        self.assertIn("unknown command", self._run(":nonsense\n:q\n"))

    def test_help_lists_the_commands(self):
        text = self._run(":help\n:q\n")
        for command in (":skills", ":show", ":json", ":why"):
            self.assertIn(command, text)

    def test_blank_lines_are_ignored(self):
        self.assertNotIn("unknown", self._run("\n\n:q\n"))

    def test_end_of_input_ends_the_session(self):
        self._run("make the bed\n")  # no :q — must return rather than hang

    def test_the_shell_never_writes(self):
        before = {n: len(c.documents) for n, c in self.db._collections.items()}
        self._run("make me a masala chai\n:skills\n:show burger\n:q\n")
        after = {n: len(c.documents) for n, c in self.db._collections.items()}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
