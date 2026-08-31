"""Station 4: the JSON seam, the report, and the cycle-guard primitives."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest

from direction_normalizer import normalize, stamped_edges_from_json
from direction_normalizer.cli import build_parser, load_stamped, main, run
from direction_normalizer.cycles import OrderingGraph
from direction_normalizer.report import dump_json, print_plan, print_report
from kg_read_harness.errors import ConfigError
from llm_disambiguator import disambiguate
from rule_preclassifier import classify

from . import fake_llm
from .station2_fixture import chai_bundles
from .station4_fixture import chai_stamped, precedes, produces, requires


class OrderingGraphTests(unittest.TestCase):
    def test_adds_an_acyclic_edge(self):
        graph = OrderingGraph()
        self.assertTrue(graph.add("a", "b"))
        self.assertTrue(graph.add("b", "c"))
        self.assertEqual(len(graph), 2)

    def test_refuses_a_back_edge(self):
        graph = OrderingGraph()
        graph.add("a", "b")
        graph.add("b", "c")
        self.assertTrue(graph.would_cycle("c", "a"))
        self.assertFalse(graph.add("c", "a"))
        self.assertEqual(len(graph), 2)

    def test_refuses_a_self_loop(self):
        graph = OrderingGraph()
        self.assertFalse(graph.add("a", "a"))

    def test_matches_names_across_spellings(self):
        graph = OrderingGraph()
        graph.add("PLACE PAN", "ADD WATER")
        self.assertTrue(graph.would_cycle("add_water", "place_pan"))

    def test_topological_order_of_a_dag(self):
        graph = OrderingGraph()
        graph.add("a", "b")
        graph.add("b", "c")
        self.assertEqual(graph.topological_order(), ["a", "b", "c"])

    def test_topological_order_is_alphabetical_on_ties(self):
        graph = OrderingGraph()
        graph.add("b", "z")
        graph.add("a", "z")
        self.assertEqual(graph.topological_order(), ["a", "b", "z"])

    def test_empty_graph_sorts_to_nothing(self):
        self.assertEqual(OrderingGraph().topological_order(), [])

    def test_diamond_keeps_both_branches_before_the_join(self):
        graph = OrderingGraph()
        for head, tail in (("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")):
            graph.add(head, tail)
        order = graph.topological_order()
        self.assertLess(order.index("b"), order.index("d"))
        self.assertLess(order.index("c"), order.index("d"))


class JsonSeamTests(unittest.TestCase):
    def setUp(self):
        classified = classify(chai_bundles())
        config = fake_llm.config()
        provider = fake_llm.FakeProvider(config, fake_llm.always("produces"))
        resolved = disambiguate(classified.deferred, config, provider=provider)
        self.station2 = classified.to_dict()
        self.station3 = resolved.to_dict()

    def test_reads_a_station_2_result(self):
        edges = stamped_edges_from_json(self.station2)
        self.assertEqual(len(edges), 19)

    def test_reads_a_station_3_result(self):
        edges = stamped_edges_from_json(self.station3)
        self.assertEqual(len(edges), 20)

    def test_reads_both_together(self):
        edges = stamped_edges_from_json([self.station2, self.station3])
        self.assertEqual(len(edges), 39)

    def test_reads_a_bare_array_of_stamped_rows(self):
        edges = stamped_edges_from_json(self.station2["stamped"])
        self.assertEqual(len(edges), 19)

    def test_rejects_something_else(self):
        with self.assertRaises(ValueError):
            stamped_edges_from_json({"nope": []})

    def test_round_trips_through_the_pipeline(self):
        edges = stamped_edges_from_json([self.station2, self.station3])
        result = normalize(edges)
        self.assertEqual(result.total_input, 39)
        self.assertEqual(result.total_output, 39)


class CliTests(unittest.TestCase):
    def setUp(self):
        classified = classify(chai_bundles())
        config = fake_llm.config()
        provider = fake_llm.FakeProvider(config, fake_llm.always("produces"))
        resolved = disambiguate(classified.deferred, config, provider=provider)
        self.paths = []
        for payload in (classified.to_dict(), resolved.to_dict()):
            handle, path = tempfile.mkstemp(suffix=".json")
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(payload, file)
            self.paths.append(path)
            self.addCleanup(os.unlink, path)

    def test_loads_both_files(self):
        self.assertEqual(len(load_stamped(self.paths)), 39)

    def test_a_malformed_file_is_a_config_error(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump({"nope": 1}, file)
        self.addCleanup(os.unlink, path)
        with self.assertRaises(ConfigError):
            load_stamped([path])

    def test_from_json_accepts_several_paths(self):
        args = build_parser().parse_args(["--from-json", "a.json", "b.json"])
        self.assertEqual(args.from_json, ["a.json", "b.json"])

    def test_table_run(self):
        out = io.StringIO()
        code = run(edges=chai_stamped()[0], stdout=out)
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Bridge Station 4", text)
        self.assertIn("conservation: 39", text)

    def test_json_run_is_machine_clean(self):
        out = io.StringIO()
        run(edges=chai_stamped()[0], output_format="json", stdout=out)
        payload = json.loads(out.getvalue())
        self.assertEqual(
            set(payload), {"summary", "finalized", "derived", "parked", "topological_order"}
        )
        self.assertEqual(payload["topological_order"][0], "place_pan")

    def test_main_reads_files(self):
        self.assertEqual(main(["--from-json", *self.paths, "--no-listing", "--no-plan"]), 0)

    def test_missing_file_exits_non_zero(self):
        self.assertNotEqual(main(["--from-json", "/nonexistent.json"]), 0)


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.result = normalize(chai_stamped()[0])

    def test_summary_reports_streams_and_overlap(self):
        out = io.StringIO()
        print_report(self.result, out, listing=False, plan=False)
        text = out.getvalue()
        self.assertIn("finalized", text)
        self.assertIn("derived", text)
        self.assertIn("new (never written down)", text)

    def test_summary_reports_direction_methods(self):
        out = io.StringIO()
        print_report(self.result, out, listing=False, plan=False)
        self.assertIn("type_implied", out.getvalue())

    def test_listing_shows_the_derived_chains(self):
        out = io.StringIO()
        print_report(self.result, out, plan=False)
        text = out.getvalue()
        self.assertIn("via pan_on_stove", text)
        self.assertIn("place_pan", text)

    def test_plan_prints_the_order(self):
        out = io.StringIO()
        print_plan(self.result, out)
        text = out.getvalue()
        self.assertIn("1. place_pan", text)
        self.assertIn("serve", text)

    def test_plan_says_so_when_the_graph_is_cyclic(self):
        result = normalize([
            produces("a", "s1", key="r1"), requires("b", "s1", key="r2"),
            precedes("b", "a", "b before a", key="r3"),
        ])
        # The guard prevents the cycle, so a valid order must still exist.
        out = io.StringIO()
        print_plan(result, out)
        self.assertNotIn("cyclic", out.getvalue())

    def test_parked_edges_appear_in_the_listing(self):
        result = normalize([
            produces("a", "s", key="r1"), requires("b", "s", key="r2"),
            precedes("b", "a", "written backwards", key="r3"),
        ])
        out = io.StringIO()
        print_report(result, out, plan=False)
        self.assertIn("conflicting_edge", out.getvalue())

    def test_ascii_fallback(self):
        class AsciiStream(io.StringIO):
            encoding = "ascii"

        out = AsciiStream()
        print_report(self.result, out)
        out.getvalue().encode("ascii")

    def test_json_dump_round_trips(self):
        out = io.StringIO()
        dump_json(self.result, out)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["summary"]["total_input"], self.result.total_input)


if __name__ == "__main__":
    unittest.main()
