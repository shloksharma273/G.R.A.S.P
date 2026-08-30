"""End-to-end tests for Station 2's entry point and its summary (FR-7)."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest

from rule_preclassifier import classify
from rule_preclassifier.cli import bundles_from_json, main, run
from rule_preclassifier.report import dump_json, print_report

from .station2_fixture import bundle, chai_bundles


def station1_json(bundles):
    return json.dumps([b.to_dict() for b in bundles])


class JsonInputTests(unittest.TestCase):
    def test_round_trips_station_1_output(self):
        original = chai_bundles()
        restored = bundles_from_json(json.loads(station1_json(original)))
        self.assertEqual(restored, original)

    def test_rejects_a_non_array(self):
        from kg_read_harness.errors import ConfigError

        with self.assertRaises(ConfigError):
            bundles_from_json({"relation_key": "r1"})

    def test_rejects_a_malformed_bundle(self):
        from kg_read_harness.errors import ConfigError

        with self.assertRaises(ConfigError):
            bundles_from_json([{"relation_key": "r1", "source": {"name": "a"}}])

    def test_tolerates_a_missing_description(self):
        restored = bundles_from_json(
            [{"relation_key": "r1", "source": {"name": "a", "type": "SKILL"},
              "target": {"name": "b", "type": "PRIMITIVE"}}]
        )
        self.assertEqual(restored[0].description, "")


class RunTests(unittest.TestCase):
    def test_table_run_prints_listing_and_summary(self):
        out = io.StringIO()
        code = run(bundles=chai_bundles(), stdout=out)
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Bridge Station 2", text)
        self.assertIn("decomposes_to", text)
        self.assertIn("conservation: 39", text)

    def test_no_listing_suppresses_the_per_bundle_lines(self):
        out = io.StringIO()
        run(bundles=chai_bundles(), stdout=out, listing=False)
        text = out.getvalue()
        self.assertNotIn("STAMP", text)
        self.assertIn("buckets", text)

    def test_json_run_emits_one_machine_readable_object(self):
        out = io.StringIO()
        run(bundles=chai_bundles(), output_format="json", stdout=out)
        payload = json.loads(out.getvalue())
        self.assertEqual(set(payload), {"summary", "stamped", "deferred", "parked"})
        self.assertEqual(payload["summary"]["total_input"], 39)
        self.assertEqual(len(payload["stamped"]), 19)
        self.assertEqual(len(payload["deferred"]), 20)

    def test_json_stdout_carries_nothing_but_the_object(self):
        out = io.StringIO()
        run(bundles=chai_bundles(), output_format="json", stdout=out)
        json.loads(out.getvalue())  # would raise if a header leaked in


class MainTests(unittest.TestCase):
    def setUp(self):
        handle, self.path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write(station1_json(chai_bundles()))
        self.addCleanup(os.unlink, self.path)

    def test_reads_a_station_1_file(self):
        self.assertEqual(main(["--from-json", self.path, "--format", "json", "--no-listing"]), 0)

    def test_missing_file_exits_non_zero(self):
        self.assertNotEqual(main(["--from-json", "/nonexistent/bundles.json"]), 0)

    def test_invalid_json_exits_non_zero(self):
        handle, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write("{not json")
        self.addCleanup(os.unlink, path)
        self.assertNotEqual(main(["--from-json", path]), 0)

    def test_touches_no_database_when_given_a_file(self):
        # A bogus environment would break a live read; --from-json must not do one.
        for name in ("ARANGO_URL", "ARANGO_DB", "PROJECT_NAME"):
            os.environ.pop(name, None)
        self.assertEqual(main(["--from-json", self.path, "--no-listing"]), 0)


class Utf8Stream(io.StringIO):
    """A stream that can encode box glyphs, unlike a bare StringIO."""

    encoding = "utf-8"


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.result = classify(chai_bundles())

    def test_summary_reports_counts_by_bucket(self):
        out = io.StringIO()
        print_report(self.result, out, listing=False)
        text = out.getvalue()
        self.assertIn("stamped       19", text)
        self.assertIn("deferred      20", text)
        self.assertIn("parked         0", text)

    def test_summary_reports_counts_by_edge_type(self):
        out = io.StringIO()
        print_report(self.result, out, listing=False)
        self.assertIn("decomposes_to", out.getvalue())
        self.assertIn("uses", out.getvalue())

    def test_summary_reports_reason_codes_and_group_guidance(self):
        result = classify(
            [
                bundle("stove", "OBJECT", "stove_on", "STATE", relation_key="r1"),
                bundle("a", "WIDGET", "b", "PRIMITIVE", relation_key="r2"),
            ]
        )
        out = io.StringIO()
        print_report(result, out, listing=False)
        text = out.getvalue()
        self.assertIn("unmapped_pair", text)
        self.assertIn("missing_type", text)
        self.assertIn("reading the pile", text)

    def test_header_reports_type_normalization(self):
        result = classify([bundle("make_chai", "skill", "PLACE PAN", "tool")])
        out = io.StringIO()
        print_report(result, out, listing=False)
        self.assertIn("tool -> PRIMITIVE", out.getvalue())

    def test_json_dump_matches_the_result(self):
        out = io.StringIO()
        dump_json(self.result, out)
        self.assertEqual(json.loads(out.getvalue()), json.loads(json.dumps(self.result.to_dict())))

    def test_listing_marks_each_bucket(self):
        result = classify(
            [
                bundle("make_chai", "SKILL", "place_pan", "PRIMITIVE", relation_key="r1"),
                bundle("place_pan", "PRIMITIVE", "pan_on_stove", "STATE", relation_key="r2"),
                bundle("stove", "OBJECT", "stove_on", "STATE", relation_key="r3"),
            ]
        )
        out = io.StringIO()
        print_report(result, out)
        text = out.getvalue()
        self.assertIn("STAMP", text)
        self.assertIn("DEFER", text)
        self.assertIn("PARK", text)

    def test_report_falls_back_to_ascii_on_a_limited_stream(self):
        class AsciiStream(io.StringIO):
            encoding = "ascii"

        out = AsciiStream()
        print_report(self.result, out)
        out.getvalue().encode("ascii")  # would raise if a box glyph leaked in

    def test_report_uses_box_glyphs_when_the_stream_can_encode_them(self):
        out = Utf8Stream()
        print_report(self.result, out)
        text = out.getvalue()
        self.assertIn("Summary — buckets", text)
        self.assertIn("39 in → 39 out", text)


if __name__ == "__main__":
    unittest.main()
