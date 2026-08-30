"""Station 3: config, the prompt contract, the transport, and the CLI."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
import urllib.error

from kg_read_harness.errors import ConfigError
from llm_disambiguator.config import DEFAULT_BASE_URL, DEFAULT_MODEL, load_llm_config
from llm_disambiguator.prompt import (
    SYSTEM_PROMPT,
    MalformedResponse,
    PromptItem,
    build_user_message,
    parse_response,
)
from llm_disambiguator.provider import (
    Provider,
    ServiceError,
    build_payload,
    extract_message,
)
from llm_disambiguator.report import dump_json, print_report
from llm_disambiguator import disambiguate
from llm_disambiguator.cli import (
    build_parser,
    deferred_from_station2_json,
    main,
    run,
)
from rule_preclassifier import classify

from . import fake_llm
from .station2_fixture import chai_bundles
from .test_disambiguator import edge


class ConfigTests(unittest.TestCase):
    def test_requires_a_key(self):
        with self.assertRaises(ConfigError) as caught:
            load_llm_config({})
        self.assertIn("LLM_API_KEY", str(caught.exception))

    def test_accepts_any_of_the_key_variables(self):
        for name in ("LLM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
            self.assertEqual(load_llm_config({name: "k"}).api_key, "k")

    def test_first_key_variable_wins(self):
        config = load_llm_config({"LLM_API_KEY": "first", "OPENROUTER_API_KEY": "second"})
        self.assertEqual(config.api_key, "first")

    def test_defaults(self):
        config = load_llm_config({"LLM_API_KEY": "k"})
        self.assertEqual(config.base_url, DEFAULT_BASE_URL)
        self.assertEqual(config.model, DEFAULT_MODEL)
        self.assertEqual(config.temperature, 0.0)
        self.assertTrue(config.lexical_prepass)
        self.assertFalse(config.reasoning)

    def test_endpoint_is_derived_from_the_base_url(self):
        config = load_llm_config({"LLM_API_KEY": "k", "LLM_BASE_URL": "https://x.test/v1/"})
        self.assertEqual(config.endpoint, "https://x.test/v1/chat/completions")

    def test_never_describes_the_key(self):
        config = load_llm_config({"LLM_API_KEY": "super-secret-value"})
        rendered = " ".join(f"{a} {b}" for a, b in config.describe())
        self.assertNotIn("super-secret-value", rendered)

    def test_rejects_an_out_of_range_threshold(self):
        with self.assertRaises(ConfigError):
            load_llm_config({"LLM_API_KEY": "k", "LLM_CONFIDENCE_THRESHOLD": "1.5"})

    def test_rejects_a_non_numeric_batch_size(self):
        with self.assertRaises(ConfigError):
            load_llm_config({"LLM_API_KEY": "k", "LLM_BATCH_SIZE": "many"})

    def test_rejects_a_non_boolean_flag(self):
        with self.assertRaises(ConfigError):
            load_llm_config({"LLM_API_KEY": "k", "LLM_LEXICAL_PREPASS": "maybe"})

    def test_boolean_spellings(self):
        for raw, expected in (("1", True), ("on", True), ("TRUE", True), ("0", False), ("off", False)):
            config = load_llm_config({"LLM_API_KEY": "k", "LLM_LEXICAL_PREPASS": raw})
            self.assertEqual(config.lexical_prepass, expected, raw)


class PromptContractTests(unittest.TestCase):
    """Section 7: constrained output, closed vocabulary, no extra graph context."""

    def test_system_prompt_defines_both_labels_and_the_abstention(self):
        for token in ("requires", "produces", "unclear", "BEFORE", "AFTER"):
            self.assertIn(token, SYSTEM_PROMPT)

    def test_user_message_carries_only_the_three_fields(self):
        message = build_user_message([PromptItem("i1", "add_water", "pan_on_stove", "text")])
        self.assertIn("primitive: add_water", message)
        self.assertIn("state: pan_on_stove", message)
        self.assertIn("description: text", message)

    def test_no_relation_key_or_types_are_sent(self):
        bundle = edge(key="secret-relation-key")
        config = fake_llm.config()
        provider = fake_llm.FakeProvider(config, fake_llm.always("produces"))
        disambiguate([bundle], config, provider=provider)
        _system, user = provider.prompts[0]
        self.assertNotIn("secret-relation-key", user)
        self.assertNotIn("PRIMITIVE", user)

    def test_parses_the_documented_shape(self):
        verdicts = parse_response(
            '{"items":[{"id":"a","relation":"requires","confidence":0.9,"rationale":"r"}]}', "m"
        )
        self.assertEqual(verdicts["a"].label, "requires")
        self.assertEqual(verdicts["a"].confidence, 0.9)
        self.assertEqual(verdicts["a"].model, "m")

    def test_tolerates_code_fences(self):
        text = '```json\n{"items":[{"id":"a","relation":"produces","confidence":1}]}\n```'
        self.assertEqual(parse_response(text, "m")["a"].label, "produces")

    def test_tolerates_a_bare_array(self):
        text = '[{"id":"a","relation":"produces","confidence":0.9}]'
        self.assertEqual(parse_response(text, "m")["a"].label, "produces")

    def test_tolerates_an_alternative_wrapper_key(self):
        text = '{"results":[{"id":"a","relation":"requires","confidence":0.9}]}'
        self.assertEqual(parse_response(text, "m")["a"].label, "requires")

    def test_normalizes_label_casing(self):
        text = '{"items":[{"id":"a","relation":"Requires","confidence":0.9}]}'
        self.assertEqual(parse_response(text, "m")["a"].label, "requires")

    def test_rejects_a_label_outside_the_vocabulary(self):
        with self.assertRaises(MalformedResponse):
            parse_response('{"items":[{"id":"a","relation":"maybe","confidence":1}]}', "m")

    def test_rejects_prose(self):
        with self.assertRaises(MalformedResponse):
            parse_response("Sure, here you go!", "m")

    def test_rejects_an_empty_message(self):
        with self.assertRaises(MalformedResponse):
            parse_response("", "m")

    def test_rejects_an_item_with_no_id(self):
        with self.assertRaises(MalformedResponse):
            parse_response('{"items":[{"relation":"requires","confidence":1}]}', "m")

    def test_missing_confidence_becomes_zero_rather_than_failing(self):
        self.assertEqual(parse_response('{"items":[{"id":"a","relation":"requires"}]}', "m")["a"].confidence, 0.0)

    def test_confidence_is_clamped(self):
        text = '{"items":[{"id":"a","relation":"requires","confidence":7}]}'
        self.assertEqual(parse_response(text, "m")["a"].confidence, 1.0)

    def test_unclear_confidence_is_zeroed(self):
        text = '{"items":[{"id":"a","relation":"unclear","confidence":0.99}]}'
        self.assertEqual(parse_response(text, "m")["a"].confidence, 0.0)


class ProviderTests(unittest.TestCase):
    """Section 7 determinism settings and FR-7 resilience."""

    def test_payload_carries_temperature_and_json_mode(self):
        payload = build_payload(fake_llm.config(), "sys", "user")
        self.assertEqual(payload["temperature"], 0.0)
        self.assertEqual(payload["response_format"], {"type": "json_object"})
        self.assertEqual(payload["reasoning"], {"enabled": False})

    def test_reasoning_can_be_enabled(self):
        payload = build_payload(fake_llm.config(LLM_REASONING="1"), "s", "u")
        self.assertNotIn("reasoning", payload)

    def test_json_mode_can_be_disabled(self):
        payload = build_payload(fake_llm.config(LLM_JSON_MODE="0"), "s", "u")
        self.assertNotIn("response_format", payload)

    def test_extract_message_reads_the_content(self):
        self.assertEqual(
            extract_message({"choices": [{"message": {"content": "hi"}}]}), "hi"
        )

    def test_extract_message_explains_a_truncated_reply(self):
        with self.assertRaises(ServiceError) as caught:
            extract_message({"choices": [{"message": {"content": None}, "finish_reason": "length"}]})
        self.assertIn("max_tokens", str(caught.exception))

    def test_extract_message_surfaces_a_provider_error(self):
        with self.assertRaises(ServiceError):
            extract_message({"error": {"message": "nope"}})

    def test_retries_transient_failures_then_gives_up(self):
        attempts = {"n": 0}
        slept: list[float] = []

        class Flaky(Provider):
            def _post(self, payload):
                attempts["n"] += 1
                raise urllib.error.URLError("connection refused")

        config = fake_llm.config(LLM_MAX_RETRIES="2", LLM_BACKOFF_SECONDS="0.5")
        provider = Flaky(config, sleep=slept.append)
        with self.assertRaises(ServiceError):
            provider.complete("s", "u")
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(provider.retries_made, 2)
        self.assertEqual(slept, [0.5, 1.0])  # bounded, deterministic, no jitter

    def test_recovers_when_a_retry_succeeds(self):
        attempts = {"n": 0}

        class Flaky(Provider):
            def _post(self, payload):
                attempts["n"] += 1
                if attempts["n"] == 1:
                    raise urllib.error.URLError("temporary")
                return {"choices": [{"message": {"content": "ok"}}]}

        provider = Flaky(fake_llm.config(), sleep=lambda _s: None)
        self.assertEqual(provider.complete("s", "u"), "ok")

    def test_a_rejected_key_is_not_retried_and_is_not_printed(self):
        class Unauthorized(Provider):
            def _post(self, payload):
                raise urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)

        provider = Unauthorized(fake_llm.config(), sleep=lambda _s: None)
        with self.assertRaises(ServiceError) as caught:
            provider.complete("s", "u")
        self.assertEqual(provider.requests_made, 1)
        self.assertNotIn("test-key-not-real", str(caught.exception))


class CliTests(unittest.TestCase):
    def setUp(self):
        self.station2 = classify(chai_bundles()).to_dict()
        handle, self.path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            json.dump(self.station2, file)
        self.addCleanup(os.unlink, self.path)

    def test_extracts_the_deferred_bucket(self):
        deferred = deferred_from_station2_json(self.station2)
        self.assertEqual(len(deferred), 20)
        self.assertTrue(all(b.target.type == "STATE" for b in deferred))

    def test_rejects_a_station_1_listing_by_mistake(self):
        with self.assertRaises(ConfigError):
            deferred_from_station2_json([{"relation_key": "r1"}])

    def test_input_flags_are_mutually_exclusive(self):
        with self.assertRaises(SystemExit):
            build_parser().parse_args(["--from-json", "a", "--from-bundles", "b"])

    def test_table_run(self):
        out = io.StringIO()
        config = fake_llm.config()
        provider = fake_llm.FakeProvider(config, fake_llm.always("produces"))
        os.environ["LLM_API_KEY"] = "test-key-not-real"
        os.environ["LLM_CACHE"] = "0"
        self.addCleanup(os.environ.pop, "LLM_API_KEY", None)
        self.addCleanup(os.environ.pop, "LLM_CACHE", None)

        code = run(deferred=deferred_from_station2_json(self.station2), provider=provider, stdout=out)
        text = out.getvalue()
        self.assertEqual(code, 0)
        self.assertIn("Bridge Station 3", text)
        self.assertIn("conservation: 20", text)
        self.assertNotIn("test-key-not-real", text)

    def test_json_run_is_machine_clean(self):
        os.environ["LLM_API_KEY"] = "test-key-not-real"
        os.environ["LLM_CACHE"] = "0"
        self.addCleanup(os.environ.pop, "LLM_API_KEY", None)
        self.addCleanup(os.environ.pop, "LLM_CACHE", None)
        out = io.StringIO()
        config = fake_llm.config()
        provider = fake_llm.FakeProvider(config, fake_llm.always("requires"))
        run(
            deferred=deferred_from_station2_json(self.station2),
            provider=provider,
            output_format="json",
            stdout=out,
        )
        payload = json.loads(out.getvalue())
        self.assertEqual(set(payload), {"summary", "stamped", "parked"})
        self.assertEqual(payload["summary"]["total_input"], 20)

    def test_missing_key_exits_non_zero_with_a_config_error(self):
        for name in ("LLM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(name, None)
        self.assertEqual(main(["--from-json", self.path, "--dry-run"]), 2)

    def test_missing_file_exits_non_zero(self):
        os.environ["LLM_API_KEY"] = "k"
        self.addCleanup(os.environ.pop, "LLM_API_KEY", None)
        self.assertNotEqual(main(["--from-json", "/nonexistent.json"]), 0)

    def test_dry_run_makes_no_call(self):
        os.environ["LLM_API_KEY"] = "k"
        os.environ["LLM_CACHE"] = "0"
        self.addCleanup(os.environ.pop, "LLM_API_KEY", None)
        self.addCleanup(os.environ.pop, "LLM_CACHE", None)
        self.assertEqual(main(["--from-json", self.path, "--dry-run", "--no-listing"]), 0)


class ReportTests(unittest.TestCase):
    def setUp(self):
        config = fake_llm.config()
        provider = fake_llm.FakeProvider(config, fake_llm.always("produces", 0.88))
        bundles = [
            edge(description="requires the pan as a precondition", key="r1"),
            edge(state="b", description="silent", key="r2"),
            edge(state="c", description="", key="r3"),
        ]
        self.config = config
        self.result = disambiguate(bundles, config, provider=provider)

    def test_summary_reports_labels_methods_and_mean_confidence(self):
        out = io.StringIO()
        print_report(self.config, self.result, out, listing=False)
        text = out.getvalue()
        self.assertIn("requires", text)
        self.assertIn("produces", text)
        self.assertIn("lexical", text)
        self.assertIn("mean conf.", text)

    def test_summary_reports_reason_codes(self):
        out = io.StringIO()
        print_report(self.config, self.result, out, listing=False)
        self.assertIn("empty_description", out.getvalue())

    def test_summary_reports_call_counters(self):
        out = io.StringIO()
        print_report(self.config, self.result, out, listing=False)
        self.assertIn("cache hits", out.getvalue())

    def test_header_never_prints_the_key(self):
        out = io.StringIO()
        print_report(self.config, self.result, out, listing=False)
        self.assertNotIn("test-key-not-real", out.getvalue())

    def test_listing_marks_stamped_and_parked(self):
        out = io.StringIO()
        print_report(self.config, self.result, out)
        text = out.getvalue()
        self.assertIn("REQUIRES", text)
        self.assertIn("PARK", text)

    def test_ascii_fallback(self):
        class AsciiStream(io.StringIO):
            encoding = "ascii"

        out = AsciiStream()
        print_report(self.config, self.result, out)
        out.getvalue().encode("ascii")

    def test_json_dump_round_trips(self):
        out = io.StringIO()
        dump_json(self.result, out)
        self.assertEqual(json.loads(out.getvalue()), json.loads(json.dumps(self.result.to_dict())))


if __name__ == "__main__":
    unittest.main()
