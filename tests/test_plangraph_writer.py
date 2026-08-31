"""Station 5: isolation, idempotent scoped rebuild, transactions, verification."""

from __future__ import annotations

import unittest

from direction_normalizer import normalize
from plangraph_writer import build, write_plangraph
from plangraph_writer.config import load_writer_config
from plangraph_writer.embed import (
    STATUS_NOT_CONFIGURED,
    STATUS_PENDING,
    STATUS_SKIPPED,
    build_payload,
    ensure_vector_index,
)
from plangraph_writer.guard import GuardedDatabase, IsolationViolation, forbidden_collections
from plangraph_writer.records import WriteReport
from plangraph_writer.schema import Schema
from plangraph_writer.writer import ensure_schema

from .fake_writable_arango import FOREIGN_COLLECTIONS, ForbiddenWrite, make_db
from .station4_fixture import chai_stamped

ENV = {
    "ARANGO_URL": "http://localhost:8529",
    "ARANGO_DB": "test_shlok",
    "ARANGO_USERNAME": "root",
    "ARANGO_PASSWORD": "",
    "PROJECT_NAME": "roboticsPlanner",
}
SCHEMA = Schema(prefix="roboticsPlanner")


def config(dry_run=False, **overrides):
    env = {**ENV, **overrides}
    return load_writer_config(env, dry_run=dry_run)


def chai_plan(scope=None):
    result = normalize(chai_stamped()[0])
    return build(result.finalized, result.derived, SCHEMA, skill_scope=scope)


class IsolationTests(unittest.TestCase):
    """FR-8 — the constraint that matters in a shared database."""

    def setUp(self):
        self.db = make_db()
        self.guard = GuardedDatabase(self.db, SCHEMA)

    def test_the_allowlist_is_exactly_the_plangraph(self):
        self.assertEqual(self.guard.allowed, frozenset(SCHEMA.all_collections))

    def test_writing_autographs_entities_is_refused(self):
        with self.assertRaises(IsolationViolation):
            self.guard.insert_many("roboticsPlanner_Entities", [{"_key": "x"}])

    def test_writing_autographs_relations_is_refused(self):
        with self.assertRaises(IsolationViolation):
            self.guard.insert_many("roboticsPlanner_Relations", [{"_key": "x"}])

    def test_another_projects_collection_is_refused(self):
        """A denylist of AutoGraph's names would have missed these."""
        for name in ("AIS-1847_test_Entities", "E2E_test_similarities", "api_test_project_sources"):
            with self.assertRaises(IsolationViolation):
                self.guard.insert_many(name, [{"_key": "x"}])

    def test_deleting_outside_the_allowlist_is_refused(self):
        with self.assertRaises(IsolationViolation):
            self.guard.delete_scope("roboticsPlanner_Entities", "make_masala_chai")

    def test_creating_a_foreign_collection_is_refused(self):
        with self.assertRaises(IsolationViolation):
            self.guard.create_collection("something_else", edge=False)

    def test_indexing_a_foreign_collection_is_refused(self):
        with self.assertRaises(IsolationViolation):
            self.guard.add_persistent_index("roboticsPlanner_Entities", ["type"])

    def test_creating_a_foreign_graph_is_refused(self):
        with self.assertRaises(IsolationViolation):
            self.guard.create_graph("roboticsPlanner_kg", [SCHEMA.edge_definition()])

    def test_a_write_query_naming_a_foreign_collection_is_refused(self):
        with self.assertRaises(IsolationViolation):
            self.guard.aql(
                "FOR d IN @@collection REMOVE d IN @@collection",
                {"@collection": "roboticsPlanner_Relations"},
            )

    def test_reading_a_foreign_collection_is_allowed(self):
        # Reading is always safe; only mutation is constrained.
        self.assertTrue(self.guard.has_collection("roboticsPlanner_Entities"))
        self.assertEqual(self.guard.count("roboticsPlanner_Entities"), 1)

    def test_a_full_run_never_touches_a_foreign_collection(self):
        write_plangraph(chai_plan(), self.db, config())
        for name in FOREIGN_COLLECTIONS:
            self.assertEqual(
                len(self.db.collection(name).documents), 1, f"{name} was modified"
            )

    def test_the_fake_database_would_notice_a_breach(self):
        """The double is only meaningful if it actually raises."""
        with self.assertRaises(ForbiddenWrite):
            self.db.collection("roboticsPlanner_Entities").insert_many([{"_key": "x"}])

    def test_forbidden_collections_lists_what_is_excluded(self):
        excluded = set(forbidden_collections(self.db, SCHEMA))
        self.assertIn("roboticsPlanner_Entities", excluded)
        self.assertEqual(excluded & set(SCHEMA.all_collections), set())


class SchemaCreationTests(unittest.TestCase):
    """FR-1 — create only if absent, never error on re-run."""

    def setUp(self):
        self.db = make_db()

    def test_creates_all_five_collections_and_the_graph(self):
        report = WriteReport()
        ensure_schema(GuardedDatabase(self.db, SCHEMA), SCHEMA, report)
        self.assertEqual(len(report.schema_created), 5)
        self.assertTrue(report.graph_created)
        self.assertTrue(self.db.has_collection("roboticsPlanner_PlanEdges"))

    def test_the_edge_collection_is_an_edge_collection(self):
        ensure_schema(GuardedDatabase(self.db, SCHEMA), SCHEMA, WriteReport())
        self.assertTrue(self.db.collection("roboticsPlanner_PlanEdges").edge)
        self.assertFalse(self.db.collection("roboticsPlanner_Skills").edge)

    def test_a_second_run_creates_nothing(self):
        guard = GuardedDatabase(self.db, SCHEMA)
        ensure_schema(guard, SCHEMA, WriteReport())
        second = WriteReport()
        ensure_schema(guard, SCHEMA, second)
        self.assertEqual(second.schema_created, [])
        self.assertFalse(second.graph_created)
        self.assertEqual(second.indexes_created, [])

    def test_indexes_are_created_once(self):
        guard = GuardedDatabase(self.db, SCHEMA)
        first = WriteReport()
        ensure_schema(guard, SCHEMA, first)
        self.assertTrue(first.indexes_created)
        second = WriteReport()
        ensure_schema(guard, SCHEMA, second)
        self.assertEqual(second.indexes_created, [])

    def test_dry_run_creates_nothing(self):
        report = WriteReport(dry_run=True)
        ensure_schema(GuardedDatabase(self.db, SCHEMA), SCHEMA, report, dry_run=True)
        self.assertEqual(len(report.schema_created), 5)  # reported...
        self.assertFalse(self.db.has_collection("roboticsPlanner_Skills"))  # ...not created


class WriteTests(unittest.TestCase):
    """FR-3, FR-4, FR-7."""

    def setUp(self):
        self.db = make_db()
        self.plan = chai_plan()
        self.report = write_plangraph(self.plan, self.db, config())

    def test_all_documents_are_written(self):
        self.assertEqual(self.report.written_total, len(self.plan.vertices) + len(self.plan.edges))

    def test_verification_reads_back_the_expected_counts(self):
        """Acceptance criterion 1: exactly the expected vertices and typed edges."""
        self.assertEqual(self.report.verified["roboticsPlanner_Skills"], 1)
        self.assertEqual(self.report.verified["roboticsPlanner_Primitives"], 11)
        self.assertEqual(self.report.verified["roboticsPlanner_Objects"], 8)
        self.assertEqual(self.report.verified["roboticsPlanner_States"], 10)
        self.assertEqual(self.report.verified["roboticsPlanner_PlanEdges"], 49)

    def test_the_named_graph_exists(self):
        self.assertEqual(self.report.verified["_graph"], 1)

    def test_edges_reference_real_vertices(self):
        vertices = set()
        for name in SCHEMA.vertex_collections:
            vertices |= {f"{name}/{key}" for key in self.db.collection(name).documents}
        for edge in self.db.collection(SCHEMA.edge_collection).documents.values():
            self.assertIn(edge["_from"], vertices)
            self.assertIn(edge["_to"], vertices)

    def test_provenance_survives_into_the_database(self):
        edges = list(self.db.collection(SCHEMA.edge_collection).documents.values())
        self.assertTrue(all(e.get("skill_scope") == "make_masala_chai" for e in edges))
        self.assertTrue(all(e.get("build_id") for e in edges))
        self.assertEqual(
            {e["method"] for e in edges}, {"rule", "llm", "derived"}
        )

    def test_the_write_is_transactional(self):
        self.assertTrue(self.report.transactional)
        self.assertEqual(self.db.transactions_committed, 1)
        self.assertEqual(self.db.transactions_aborted, 0)

    def test_it_degrades_when_transactions_are_unsupported(self):
        db = make_db(supports_transactions=False)
        report = write_plangraph(chai_plan(), db, config())
        self.assertFalse(report.transactional)
        self.assertEqual(report.written_total, 79)


class IdempotencyTests(unittest.TestCase):
    """Acceptance criterion 2 — no duplicates, no stale edges."""

    def setUp(self):
        self.db = make_db()
        self.plan = chai_plan()
        self.first = write_plangraph(self.plan, self.db, config())

    def test_a_second_run_purges_then_rewrites(self):
        second = write_plangraph(self.plan, self.db, config())
        self.assertEqual(second.purged_total, self.first.written_total)
        self.assertEqual(second.written_total, self.first.written_total)

    def test_counts_do_not_accumulate(self):
        for _ in range(3):
            report = write_plangraph(self.plan, self.db, config())
        self.assertEqual(report.verified, self.first.verified)

    def test_stale_edges_do_not_survive_a_rulebook_change(self):
        """FR-5: the point of a scoped rebuild."""
        shrunk = build(
            [e for e in normalize(chai_stamped()[0]).finalized if e.edge_type != "uses"],
            [],
            SCHEMA,
            skill_scope="make_masala_chai",
        )
        report = write_plangraph(shrunk, self.db, config())
        edges = self.db.collection(SCHEMA.edge_collection).documents.values()
        self.assertEqual([e for e in edges if e["type"] == "uses"], [])
        self.assertEqual(report.verified["roboticsPlanner_PlanEdges"], len(shrunk.edges))

    def test_another_task_is_left_untouched(self):
        """Section 7: a scoped rebuild leaves other tasks alone."""
        other = chai_plan(scope="make_coffee")
        write_plangraph(other, self.db, config())
        write_plangraph(self.plan, self.db, config())
        remaining = [
            doc
            for doc in self.db.collection(SCHEMA.edge_collection).documents.values()
            if doc["skill_scope"] == "make_coffee"
        ]
        self.assertEqual(len(remaining), len(other.edges))

    def test_scoping_keeps_the_two_tasks_separate(self):
        """Acceptance criterion 1: scoped and isolated."""
        write_plangraph(chai_plan(scope="make_coffee"), self.db, config())
        keys = set(self.db.collection("roboticsPlanner_States").documents)
        self.assertIn("make_masala_chai__stove_on", keys)
        self.assertIn("make_coffee__stove_on", keys)


class DryRunTests(unittest.TestCase):
    """The default mode: plan everything, touch nothing."""

    def setUp(self):
        self.db = make_db()
        self.plan = chai_plan()

    def test_nothing_is_created(self):
        write_plangraph(self.plan, self.db, config(dry_run=True))
        for name in SCHEMA.all_collections:
            self.assertFalse(self.db.has_collection(name), name)

    def test_no_graph_is_created(self):
        write_plangraph(self.plan, self.db, config(dry_run=True))
        self.assertNotIn("roboticsPlanner_PlanGraph", [g["name"] for g in self.db.graphs()])

    def test_it_reports_what_would_be_written(self):
        report = write_plangraph(self.plan, self.db, config(dry_run=True))
        self.assertEqual(report.written_total, 79)
        self.assertTrue(report.dry_run)

    def test_it_reports_what_would_be_purged(self):
        write_plangraph(self.plan, self.db, config())
        report = write_plangraph(self.plan, self.db, config(dry_run=True))
        self.assertEqual(report.purged_total, 79)

    def test_no_transaction_is_opened(self):
        write_plangraph(self.plan, self.db, config(dry_run=True))
        self.assertEqual(self.db.transactions_committed, 0)

    def test_the_vector_index_is_not_called(self):
        report = write_plangraph(self.plan, self.db, config(dry_run=True))
        self.assertEqual(report.vector_index, STATUS_SKIPPED)


class VectorIndexTests(unittest.TestCase):
    """FR-6 and Section 11 — never fail the write because the index cannot be built."""

    def test_payload_names_the_skills_description_field(self):
        payload = build_payload(config())
        self.assertEqual(payload["collection"], "roboticsPlanner_Skills")
        self.assertEqual(payload["field"], "description")
        self.assertEqual(payload["database"], "test_shlok")

    def test_unconfigured_endpoint_reports_not_configured(self):
        report = WriteReport()
        ensure_vector_index(config(), report)
        self.assertEqual(report.vector_index, STATUS_NOT_CONFIGURED)
        self.assertIn("goal resolution", report.vector_index_detail)

    def test_an_unreachable_endpoint_marks_it_pending_and_does_not_raise(self):
        report = WriteReport()
        ensure_vector_index(config(AUTOGRAPH_URL="http://127.0.0.1:1/nope"), report)
        self.assertEqual(report.vector_index, STATUS_PENDING)
        self.assertIn("re-run", report.vector_index_detail)

    def test_the_graph_is_still_written_when_the_endpoint_fails(self):
        db = make_db()
        report = write_plangraph(
            chai_plan(), db, config(AUTOGRAPH_URL="http://127.0.0.1:1/nope")
        )
        self.assertEqual(report.vector_index, STATUS_PENDING)
        self.assertEqual(report.verified["roboticsPlanner_PlanEdges"], 49)

    def test_the_embedding_field_is_configurable(self):
        payload = build_payload(config(PLANGRAPH_EMBEDDING_FIELD="summary"))
        self.assertEqual(payload["field"], "summary")


class ConfigTests(unittest.TestCase):
    def test_prefix_defaults_to_the_project_name(self):
        self.assertEqual(config().prefix, "roboticsPlanner")

    def test_prefix_is_configurable(self):
        self.assertEqual(config(PLANGRAPH_PREFIX="grasp").schema.graph_name, "grasp_PlanGraph")

    def test_a_prefix_colliding_with_autograph_is_refused(self):
        from kg_read_harness.errors import ConfigError

        with self.assertRaises(ConfigError):
            config(PLANGRAPH_PREFIX="roboticsPlanner_Entities")

    def test_write_credentials_can_differ_from_the_reading_user(self):
        written = config(ARANGO_WRITE_USERNAME="writer", ARANGO_WRITE_PASSWORD="pw")
        self.assertEqual(written.arango.username, "writer")
        self.assertEqual(written.writes_as, "writer")

    def test_a_write_username_without_a_password_is_refused(self):
        from kg_read_harness.errors import ConfigError

        with self.assertRaises(ConfigError):
            config(ARANGO_WRITE_USERNAME="writer")

    def test_describe_never_prints_a_credential(self):
        rendered = " ".join(
            f"{a} {b}" for a, b in config(ARANGO_PASSWORD="hunter2").describe()
        )
        self.assertNotIn("hunter2", rendered)


if __name__ == "__main__":
    unittest.main()
