"""Station 5: the schema, identity, and the pure build step."""

from __future__ import annotations

import unittest

from direction_normalizer import normalize
from kg_read_harness.errors import ConfigError
from plangraph_writer import build, infer_scope
from plangraph_writer.identity import (
    MAX_KEY_LENGTH,
    build_id,
    edge_key,
    scope_key,
    vertex_key,
)
from plangraph_writer.schema import EDGE_ENDPOINTS, VERTEX_COLLECTIONS, Schema, endpoint_types

from .station4_fixture import chai_stamped

SCHEMA = Schema(prefix="roboticsPlanner")


def chai_plan(**kwargs):
    result = normalize(chai_stamped()[0])
    return build(result.finalized, result.derived, SCHEMA, **kwargs)


class SchemaTests(unittest.TestCase):
    """PRD Section 4."""

    def test_four_vertex_collections(self):
        self.assertEqual(set(VERTEX_COLLECTIONS), {"SKILL", "PRIMITIVE", "OBJECT", "STATE"})

    def test_collection_names_are_prefixed(self):
        self.assertEqual(SCHEMA.vertex_collection("SKILL"), "roboticsPlanner_Skills")
        self.assertEqual(SCHEMA.edge_collection, "roboticsPlanner_PlanEdges")
        self.assertEqual(SCHEMA.graph_name, "roboticsPlanner_PlanGraph")

    def test_never_collides_with_autographs_collections(self):
        """Section 2: the PlanGraph must survive an AutoGraph rebuild."""
        autograph = {"roboticsPlanner_Entities", "roboticsPlanner_Relations"}
        self.assertEqual(set(SCHEMA.all_collections) & autograph, set())

    def test_all_five_edge_types(self):
        self.assertEqual(
            set(EDGE_ENDPOINTS),
            {"decomposes_to", "requires", "produces", "precedes", "uses"},
        )

    def test_endpoint_types_match_the_prd_table(self):
        self.assertEqual(endpoint_types("decomposes_to"), ("SKILL", "PRIMITIVE"))
        self.assertEqual(endpoint_types("requires"), ("PRIMITIVE", "STATE"))
        self.assertEqual(endpoint_types("produces"), ("PRIMITIVE", "STATE"))
        self.assertEqual(endpoint_types("precedes"), ("PRIMITIVE", "PRIMITIVE"))
        self.assertEqual(endpoint_types("uses"), ("PRIMITIVE", "OBJECT"))

    def test_unknown_edge_type_raises(self):
        with self.assertRaises(ValueError):
            endpoint_types("mentions")

    def test_edge_definition_binds_the_named_graph(self):
        definition = SCHEMA.edge_definition()
        self.assertEqual(definition["edge_collection"], SCHEMA.edge_collection)
        self.assertIn("roboticsPlanner_Skills", definition["from_vertex_collections"])
        self.assertIn("roboticsPlanner_States", definition["to_vertex_collections"])

    def test_all_collections_is_the_allowlist(self):
        self.assertEqual(len(SCHEMA.all_collections), 5)


class IdentityTests(unittest.TestCase):
    """PRD Section 5 — the key decision."""

    def test_vertex_key_is_scope_plus_normalized_name(self):
        self.assertEqual(vertex_key("make_chai", "PAN ON STOVE"), "make_chai__pan_on_stove")

    def test_same_name_in_different_tasks_never_merges(self):
        self.assertNotEqual(vertex_key("make_chai", "serve"), vertex_key("make_coffee", "serve"))

    def test_shared_state_name_does_not_chain_across_tasks(self):
        """The exact hazard Section 5 names: stove_on in two unrelated recipes."""
        self.assertNotEqual(
            vertex_key("make_chai", "stove_on"), vertex_key("fry_egg", "stove_on")
        )

    def test_repeated_mentions_dedupe_within_a_task(self):
        self.assertEqual(vertex_key("s", "Pan On Stove"), vertex_key("s", "pan_on_stove"))

    def test_key_is_deterministic(self):
        self.assertEqual(vertex_key("s", "a"), vertex_key("s", "a"))

    def test_long_names_fall_back_to_a_hash_and_stay_within_the_limit(self):
        key = vertex_key("scope", "x" * 400)
        self.assertLessEqual(len(key.encode("utf-8")), 254)
        self.assertNotEqual(key, vertex_key("scope", "y" * 400))

    def test_illegal_characters_are_sanitized(self):
        key = vertex_key("scope", "a/b c\\d")
        self.assertNotIn("/", key)
        self.assertNotIn("\\", key)

    def test_edge_key_covers_scope_from_to_and_type(self):
        base = edge_key("s", "a", "b", "requires")
        self.assertNotEqual(base, edge_key("s2", "a", "b", "requires"))
        self.assertNotEqual(base, edge_key("s", "b", "a", "requires"))
        self.assertNotEqual(base, edge_key("s", "a", "b", "produces"))

    def test_edge_key_is_stable(self):
        self.assertEqual(edge_key("s", "a", "b", "uses"), edge_key("s", "a", "b", "uses"))

    def test_build_id_is_content_derived_not_a_clock(self):
        """Section 10: identical input must yield an identical PlanGraph."""
        self.assertEqual(build_id("same"), build_id("same"))
        self.assertNotEqual(build_id("same"), build_id("different"))

    def test_scope_key_normalizes(self):
        self.assertEqual(scope_key("Make Masala Chai"), "make_masala_chai")


class ScopeInferenceTests(unittest.TestCase):
    def test_infers_the_single_skill(self):
        self.assertEqual(chai_plan().skill_scope, "make_masala_chai")

    def test_explicit_scope_wins(self):
        self.assertEqual(chai_plan(skill_scope="custom").skill_scope, "custom")

    def test_no_skill_is_an_actionable_error(self):
        with self.assertRaises(ConfigError) as caught:
            infer_scope([{"edge_type": "requires", "from": "a", "to": "b"}])
        self.assertIn("SKILL_SCOPE", caught.exception.render())

    def test_several_skills_is_an_actionable_error(self):
        rows = [
            {"edge_type": "decomposes_to", "from": "make_chai", "to": "p"},
            {"edge_type": "decomposes_to", "from": "make_coffee", "to": "q"},
        ]
        with self.assertRaises(ConfigError) as caught:
            infer_scope(rows)
        self.assertIn("ambiguous", caught.exception.render())


class BuildTests(unittest.TestCase):
    """PRD Sections 6, 7 — the write plan, before any I/O."""

    def setUp(self):
        self.plan = chai_plan()

    def test_every_vertex_type_is_present(self):
        counts = self.plan.counts_by_vertex_type()
        self.assertEqual(counts["SKILL"], 1)
        self.assertEqual(counts["PRIMITIVE"], 11)
        self.assertEqual(counts["OBJECT"], 8)
        self.assertEqual(counts["STATE"], 10)

    def test_every_edge_type_is_present(self):
        counts = self.plan.counts_by_edge_type()
        self.assertEqual(counts["decomposes_to"], 11)
        self.assertEqual(counts["uses"], 8)
        self.assertEqual(counts["requires"], 10)
        self.assertEqual(counts["produces"], 10)
        self.assertEqual(counts["precedes"], 10)

    def test_nothing_is_rejected(self):
        self.assertEqual(self.plan.rejected, [])

    def test_an_unknown_edge_type_is_rejected_not_written(self):
        plan = build(
            [{"edge_type": "mentions", "from": "a", "to": "b", "method": "rule"}],
            [],
            SCHEMA,
            skill_scope="s",
        )
        self.assertEqual(plan.edges, [])
        self.assertEqual(len(plan.rejected), 1)

    def test_every_vertex_carries_scope_and_build_id(self):
        for vertex in self.plan.vertices:
            document = vertex.to_document()
            self.assertEqual(document["skill_scope"], "make_masala_chai")
            self.assertEqual(document["build_id"], self.plan.build_id)

    def test_every_edge_carries_full_provenance(self):
        """Section 7."""
        for edge in self.plan.edges:
            document = edge.to_document()
            for field in ("type", "method", "confidence", "evidence", "skill_scope", "build_id"):
                self.assertIn(field, document)

    def test_derived_edges_record_their_sources(self):
        derived = [e for e in self.plan.edges if e.method == "derived"]
        self.assertTrue(derived)
        for edge in derived:
            document = edge.to_document()
            self.assertIn("via_state", document)
            self.assertEqual(len(document["source_relation_keys"]), 2)

    def test_rule_and_llm_provenance_survive(self):
        methods = set(self.plan.counts_by_method())
        self.assertEqual(methods, {"rule", "llm", "derived"})

    def test_evidence_is_the_original_description(self):
        decomposes = [e for e in self.plan.edges if e.edge_type == "decomposes_to"][0]
        self.assertIn("composed of", decomposes.evidence)

    def test_edges_point_at_the_right_collections(self):
        by_type = {e.edge_type: e for e in self.plan.edges}
        self.assertTrue(by_type["decomposes_to"].from_id.startswith("roboticsPlanner_Skills/"))
        self.assertTrue(by_type["uses"].to_id.startswith("roboticsPlanner_Objects/"))
        self.assertTrue(by_type["requires"].to_id.startswith("roboticsPlanner_States/"))
        self.assertTrue(by_type["precedes"].to_id.startswith("roboticsPlanner_Primitives/"))

    def test_duplicate_edges_are_deduped(self):
        row = {
            "edge_type": "requires",
            "from": "a",
            "to": "s",
            "method": "llm",
            "source": {"name": "a", "type": "PRIMITIVE"},
            "target": {"name": "s", "type": "STATE"},
        }
        plan = build([row, dict(row)], [], SCHEMA, skill_scope="scope")
        self.assertEqual(len(plan.edges), 1)

    def test_the_build_is_deterministic(self):
        self.assertEqual(chai_plan().to_dict(), chai_plan().to_dict())

    def test_the_build_id_is_stable_across_runs(self):
        self.assertEqual(chai_plan().build_id, chai_plan().build_id)

    def test_different_input_gives_a_different_build_id(self):
        other = build(
            [{"edge_type": "uses", "from": "a", "to": "b", "method": "rule"}],
            [],
            SCHEMA,
            skill_scope="make_masala_chai",
        )
        self.assertNotEqual(other.build_id, self.plan.build_id)

    def test_vertices_and_edges_are_ordered_stably(self):
        first, second = chai_plan(), chai_plan()
        self.assertEqual([v.key for v in first.vertices], [v.key for v in second.vertices])
        self.assertEqual([e.key for e in first.edges], [e.key for e in second.edges])

    def test_the_build_touches_no_database(self):
        import ast
        import pathlib

        import plangraph_writer

        source = (pathlib.Path(plangraph_writer.__file__).parent / "build.py").read_text()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], {"arango", "urllib", "os", "time"})


class SkillDescriptionTests(unittest.TestCase):
    """FR-6: the text the vector index has to resolve a command against."""

    def setUp(self):
        self.skill = [v for v in chai_plan().vertices if v.entity_type == "SKILL"][0]

    def test_the_description_is_not_empty(self):
        self.assertTrue(self.skill.description.strip())

    def test_the_skill_name_is_in_the_embedded_text(self):
        # A command names the task, so the name must be embedded with the prose.
        self.assertIn("make masala chai", self.skill.description.lower())

    def test_the_decomposition_evidence_is_included(self):
        self.assertIn("composed of", self.skill.description)

    def test_the_description_is_deterministic(self):
        again = [v for v in chai_plan().vertices if v.entity_type == "SKILL"][0]
        self.assertEqual(self.skill.description, again.description)

    def test_only_skills_carry_a_description(self):
        others = [v for v in chai_plan().vertices if v.entity_type != "SKILL"]
        self.assertTrue(all(v.description == "" for v in others))


if __name__ == "__main__":
    unittest.main()
