"""FR-6, FR-7, Section 9: the listing and the summary."""

from __future__ import annotations

import io
import json
import unittest

from kg_read_harness.bundle import Bundle, Entity
from kg_read_harness.config import load_config
from kg_read_harness.output import (
    JsonWriter,
    Summary,
    TableWriter,
    make_writer,
    print_summary,
)
from tests.chai_fixture import env


def bundle(source_type="SKILL", target_type="PRIMITIVE", description="is composed of"):
    return Bundle(
        relation_key="r001",
        source=Entity("make_masala_chai", source_type),
        target=Entity("place_pan", target_type),
        description=description,
    )


class TableListingTests(unittest.TestCase):
    def test_short_description_rides_on_the_arrow_line(self):
        stream = io.StringIO()
        TableWriter(stream).write(bundle())
        line = stream.getvalue().strip()
        self.assertIn("make_masala_chai (SKILL)", line)
        self.assertIn("[ is composed of ]", line)
        self.assertIn("place_pan (PRIMITIVE)", line)
        self.assertEqual(len(stream.getvalue().strip().splitlines()), 1)

    def test_long_description_becomes_an_indented_block(self):
        stream = io.StringIO()
        TableWriter(stream).write(bundle(description="word " * 60))
        lines = stream.getvalue().strip().splitlines()
        self.assertGreater(len(lines), 1)
        self.assertIn("make_masala_chai (SKILL)", lines[0])
        self.assertIn("place_pan (PRIMITIVE)", lines[0])
        self.assertTrue(lines[1].startswith(" "))

    def test_missing_description_is_stated(self):
        stream = io.StringIO()
        TableWriter(stream).write(bundle(description=""))
        self.assertIn("(no description)", stream.getvalue())

    def test_rows_are_numbered(self):
        stream = io.StringIO()
        writer = TableWriter(stream)
        writer.write(bundle())
        writer.write(bundle())
        text = stream.getvalue()
        self.assertIn("[   1]", text)
        self.assertIn("[   2]", text)


class JsonListingTests(unittest.TestCase):
    def test_emits_a_valid_array_of_contract_shaped_objects(self):
        stream = io.StringIO()
        writer = JsonWriter(stream)
        writer.write(bundle())
        writer.write(bundle(target_type="STATE"))
        writer.close()
        payload = json.loads(stream.getvalue())
        self.assertEqual(len(payload), 2)
        self.assertEqual(
            sorted(payload[0]), ["description", "relation_key", "source", "target"]
        )
        self.assertEqual(sorted(payload[0]["source"]), ["name", "type"])

    def test_empty_listing_is_still_valid_json(self):
        stream = io.StringIO()
        writer = JsonWriter(stream)
        writer.close()
        self.assertEqual(json.loads(stream.getvalue()), [])

    def test_writer_selection_follows_output_format(self):
        self.assertIsInstance(make_writer(load_config(env()), io.StringIO()), TableWriter)
        self.assertIsInstance(
            make_writer(load_config(env(OUTPUT_FORMAT="json")), io.StringIO()), JsonWriter
        )


class SummaryTests(unittest.TestCase):
    def test_counts_per_type_pair_and_total(self):
        summary = Summary()
        for _ in range(3):
            summary.add(bundle("PRIMITIVE", "STATE"))
        summary.add(bundle("SKILL", "PRIMITIVE"))
        self.assertEqual(summary.total, 4)
        self.assertEqual(
            summary.rows(), [("PRIMITIVE", "STATE", 3), ("SKILL", "PRIMITIVE", 1)]
        )

    def test_ties_break_alphabetically_so_output_is_stable(self):
        summary = Summary()
        summary.add(bundle("SKILL", "PRIMITIVE"))
        summary.add(bundle("PRIMITIVE", "OBJECT"))
        self.assertEqual([r[0] for r in summary.rows()], ["PRIMITIVE", "SKILL"])

    def test_printed_table_shows_pairs_total_and_skipped(self):
        summary = Summary()
        summary.add(bundle("PRIMITIVE", "STATE"))
        stream = io.StringIO()
        print_summary(summary, stream, skipped=2)
        text = stream.getvalue()
        self.assertIn("PRIMITIVE", text)
        self.assertIn("STATE", text)
        self.assertIn("TOTAL", text)
        self.assertIn("skipped (dangling endpoints)", text)

    def test_summary_survives_an_empty_run(self):
        stream = io.StringIO()
        print_summary(Summary(), stream)
        self.assertIn("TOTAL", stream.getvalue())


if __name__ == "__main__":
    unittest.main()
