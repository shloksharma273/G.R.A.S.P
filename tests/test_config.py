"""FR-1 and Section 8: config comes from the environment, fails fast, hides secrets."""

from __future__ import annotations

import unittest

from kg_read_harness.config import load_config, unknown_filter_types
from kg_read_harness.errors import EXIT_CONFIG, ConfigError
from tests.chai_fixture import env


class LoadConfigTests(unittest.TestCase):
    def test_defaults_are_derived_from_project_name(self):
        config = load_config(env())
        self.assertEqual(config.entity_collection, "masala_chai_Entities")
        self.assertEqual(config.relation_collection, "masala_chai_Relations")
        self.assertEqual(config.entity_type_field, "entity_type")
        self.assertEqual(config.description_field, "description")
        self.assertEqual(config.relation_type, "RELATED_TO")
        self.assertEqual(config.output_format, "table")
        self.assertEqual(config.limit, 0)
        self.assertEqual(config.entity_type_filter, ())

    def test_overrides_win(self):
        config = load_config(
            env(
                ENTITY_COLLECTION="Entities",
                RELATION_COLLECTION="Relations",
                ENTITY_TYPE_FIELD="type",
                DESCRIPTION_FIELD="note",
                RELATION_TYPE="MENTIONS",
                OUTPUT_FORMAT="JSON",
                LIMIT="25",
            )
        )
        self.assertEqual(config.entity_collection, "Entities")
        self.assertEqual(config.relation_collection, "Relations")
        self.assertEqual(config.entity_type_field, "type")
        self.assertEqual(config.description_field, "note")
        self.assertEqual(config.relation_type, "MENTIONS")
        self.assertEqual(config.output_format, "json")
        self.assertEqual(config.limit, 25)

    def test_missing_required_variable_is_named(self):
        broken = env()
        del broken["ARANGO_DB"]
        with self.assertRaises(ConfigError) as caught:
            load_config(broken)
        self.assertIn("ARANGO_DB", caught.exception.message)
        self.assertEqual(caught.exception.exit_code, EXIT_CONFIG)

    def test_whitespace_only_counts_as_missing(self):
        with self.assertRaises(ConfigError) as caught:
            load_config(env(ARANGO_URL="   "))
        self.assertIn("ARANGO_URL", caught.exception.message)

    def test_token_replaces_username_and_password(self):
        config = load_config(
            {
                "ARANGO_URL": "http://localhost:8529",
                "ARANGO_DB": "chai_db",
                "PROJECT_NAME": "masala_chai",
                "ARANGO_AUTH_TOKEN": "jwt-value",
            }
        )
        self.assertTrue(config.uses_token_auth)
        self.assertIsNone(config.username)

    def test_missing_credentials_are_reported(self):
        bare = {
            "ARANGO_URL": "http://localhost:8529",
            "ARANGO_DB": "chai_db",
            "PROJECT_NAME": "masala_chai",
        }
        with self.assertRaises(ConfigError) as caught:
            load_config(bare)
        self.assertIn("ARANGO_USERNAME", caught.exception.message)
        self.assertIn("ARANGO_PASSWORD", caught.exception.message)
        self.assertIn("ARANGO_AUTH_TOKEN", caught.exception.hint)

    def test_empty_password_is_accepted(self):
        config = load_config(env(ARANGO_PASSWORD=""))
        self.assertEqual(config.password, "")

    def test_bad_limit_and_format_fail_fast(self):
        with self.assertRaises(ConfigError):
            load_config(env(LIMIT="ten"))
        with self.assertRaises(ConfigError):
            load_config(env(LIMIT="-1"))
        with self.assertRaises(ConfigError):
            load_config(env(OUTPUT_FORMAT="yaml"))

    def test_entity_type_filter_is_trimmed_and_deduplicated(self):
        config = load_config(env(ENTITY_TYPE_FILTER=" primitive , STATE ,primitive"))
        # Case is preserved (live KGs use lowercase type values) and ignored when
        # matching, so the ontology check must not be case-sensitive either.
        self.assertEqual(config.entity_type_filter, ("primitive", "STATE"))
        self.assertEqual(unknown_filter_types(config), ())

    def test_unknown_filter_types_are_flagged(self):
        config = load_config(env(ENTITY_TYPE_FILTER="SKILL,GADGET"))
        self.assertEqual(unknown_filter_types(config), ("GADGET",))


class SecretHygieneTests(unittest.TestCase):
    def test_describe_never_contains_a_secret(self):
        config = load_config(env(ARANGO_PASSWORD="s3cr3t"))
        rendered = " ".join(f"{label} {value}" for label, value in config.describe())
        self.assertNotIn("s3cr3t", rendered)

    def test_describe_never_contains_a_token(self):
        config = load_config(
            {
                "ARANGO_URL": "http://localhost:8529",
                "ARANGO_DB": "chai_db",
                "PROJECT_NAME": "masala_chai",
                "ARANGO_AUTH_TOKEN": "jwt-abc123",
            }
        )
        rendered = " ".join(f"{label} {value}" for label, value in config.describe())
        self.assertNotIn("jwt-abc123", rendered)
        self.assertIn("bearer token", rendered)


if __name__ == "__main__":
    unittest.main()
