"""Station 3: the pipeline, the gate, conservation and reproducibility."""

from __future__ import annotations

import unittest

from kg_read_harness.bundle import Bundle, Entity
from llm_disambiguator import disambiguate
from rule_preclassifier.model import ConservationError

from . import fake_llm


def edge(primitive="add_water", state="pan_on_stove", description="a description", key="r1"):
    return Bundle(
        relation_key=key,
        source=Entity(primitive, "PRIMITIVE"),
        target=Entity(state, "STATE"),
        description=description,
    )


def run(bundles, responder=None, cache=None, **config_overrides):
    config = fake_llm.config(**config_overrides)
    provider = fake_llm.FakeProvider(config, responder or fake_llm.always("produces"))
    result = disambiguate(bundles, config, provider=provider, cache=cache)
    return result, provider


class EmptyDescriptionTests(unittest.TestCase):
    """FR-2: no usable text means no LLM call."""

    def test_empty_description_parks_without_a_call(self):
        result, provider = run([edge(description="")])
        self.assertEqual(result.parked[0].reason_code, "empty_description")
        self.assertEqual(provider.requests_made, 0)

    def test_whitespace_only_description_parks(self):
        result, provider = run([edge(description="   \n\t ")])
        self.assertEqual(result.parked[0].reason_code, "empty_description")
        self.assertEqual(provider.requests_made, 0)

    def test_parked_record_names_station_3(self):
        result, _ = run([edge(description="")])
        self.assertEqual(result.parked[0].station, "station3:llm_disambiguator")
        self.assertEqual(result.parked[0].to_dict()["station"], "station3:llm_disambiguator")


class LexicalPrepassTests(unittest.TestCase):
    """FR-3: short-circuit unambiguous wording; fall through otherwise."""

    def test_requires_wording_never_reaches_the_model(self):
        result, provider = run(
            [edge(description="Add Water requires that the pan is on the stove as a precondition.")]
        )
        self.assertEqual(result.stamped[0].edge_type, "requires")
        self.assertEqual(result.stamped[0].method, "lexical")
        self.assertEqual(provider.requests_made, 0)

    def test_produces_wording_never_reaches_the_model(self):
        result, provider = run([edge(description="Place Pan effect is that the pan is on the stove.")])
        self.assertEqual(result.stamped[0].edge_type, "produces")
        self.assertEqual(provider.requests_made, 0)

    def test_conflicting_cues_fall_through_to_the_model(self):
        result, provider = run(
            [edge(description="Serve requires tea in the cup, after which chai becomes served.")],
            responder=fake_llm.always("requires"),
        )
        self.assertEqual(provider.requests_made, 1)
        self.assertEqual(result.stamped[0].method, "llm")

    def test_silent_text_falls_through_to_the_model(self):
        result, provider = run(
            [edge(description="Add Milk relates to Tea Brewing.")],
            responder=fake_llm.always("produces"),
        )
        self.assertEqual(provider.requests_made, 1)

    def test_prepass_can_be_disabled(self):
        result, provider = run(
            [edge(description="Add Water requires the pan on the stove as a precondition.")],
            responder=fake_llm.always("requires"),
            LLM_LEXICAL_PREPASS="0",
        )
        self.assertEqual(provider.requests_made, 1)
        self.assertEqual(result.stamped[0].method, "llm")


class ConfidenceGateTests(unittest.TestCase):
    """FR-6: stamp at/above threshold, park below, park unclear always."""

    def test_stamps_at_the_threshold(self):
        result, _ = run([edge()], fake_llm.always("produces", 0.75), LLM_CONFIDENCE_THRESHOLD="0.75")
        self.assertEqual(len(result.stamped), 1)

    def test_parks_just_below_the_threshold(self):
        result, _ = run([edge()], fake_llm.always("produces", 0.74), LLM_CONFIDENCE_THRESHOLD="0.75")
        self.assertEqual(result.parked[0].reason_code, "low_confidence")
        self.assertEqual(result.stamped, [])

    def test_low_confidence_detail_names_the_numbers(self):
        result, _ = run([edge()], fake_llm.always("requires", 0.3), LLM_CONFIDENCE_THRESHOLD="0.9")
        self.assertIn("0.30", result.parked[0].detail)
        self.assertIn("0.90", result.parked[0].detail)

    def test_unclear_parks_regardless_of_confidence(self):
        result, _ = run([edge()], fake_llm.always("unclear", 1.0), LLM_CONFIDENCE_THRESHOLD="0.0")
        self.assertEqual(result.parked[0].reason_code, "unclear")
        self.assertEqual(result.stamped, [])

    def test_unclear_confidence_is_forced_to_zero(self):
        # An abstention carries no signal; nothing downstream should read one.
        result, _ = run([edge()], fake_llm.always("unclear", 0.99))
        self.assertIn("unclear", result.counts_by_reason_code())

    def test_low_confidence_and_unclear_are_never_stamped(self):
        bundles = [
            edge(description="silent one", key="r1"),
            edge(primitive="boil_water", state="stove_on", description="silent two", key="r2"),
        ]
        result, _ = run(bundles, fake_llm.always("produces", 0.1))
        self.assertEqual(result.stamped, [])
        self.assertEqual(len(result.parked), 2)


class OrientationTests(unittest.TestCase):
    """Section 5: the label fixes the meaning, so the arrow follows from it."""

    def test_arrow_runs_primitive_to_state(self):
        result, _ = run([edge()], fake_llm.always("produces"))
        orientation = result.stamped[0].orientation
        self.assertEqual((orientation.head, orientation.tail), ("add_water", "pan_on_stove"))
        self.assertEqual(orientation.decided_by, "implied_by_label")

    def test_reversed_input_is_recorded(self):
        reversed_bundle = Bundle(
            relation_key="r1",
            source=Entity("pan_on_stove", "STATE"),
            target=Entity("add_water", "PRIMITIVE"),
            description="silent",
        )
        result, _ = run([reversed_bundle], fake_llm.always("requires"))
        orientation = result.stamped[0].orientation
        self.assertEqual((orientation.head, orientation.tail), ("add_water", "pan_on_stove"))
        self.assertTrue(orientation.reversed_from_input)

    def test_original_bundle_is_never_mutated(self):
        original = edge()
        result, _ = run([original], fake_llm.always("produces"))
        self.assertIs(result.stamped[0].bundle, original)

    def test_lower_case_types_are_understood(self):
        bundle = Bundle("r1", Entity("ADD WATER", "tool"), Entity("PAN ON STOVE", "state"), "silent")
        result, _ = run([bundle], fake_llm.always("requires"))
        self.assertEqual(result.stamped[0].orientation.head, "ADD WATER")


class DeduplicationTests(unittest.TestCase):
    """Section 11: duplicates are classified once and the verdict applied to all."""

    def test_identical_triples_are_sent_once(self):
        bundles = [
            edge(description="silent", key="r1"),
            edge(description="silent", key="r2"),
            edge(description="silent", key="r3"),
        ]
        result, provider = run(bundles, fake_llm.always("produces"))
        self.assertEqual(len(result.stamped), 3)
        self.assertEqual(len(fake_llm.parse_items(provider.prompts[0][1])), 1)

    def test_whitespace_differences_do_not_defeat_dedup(self):
        bundles = [
            edge(description="the  same   text", key="r1"),
            edge(description="the same text", key="r2"),
        ]
        _, provider = run(bundles, fake_llm.always("produces"))
        self.assertEqual(len(fake_llm.parse_items(provider.prompts[0][1])), 1)

    def test_different_states_are_separate_items(self):
        bundles = [
            edge(state="pan_on_stove", description="silent", key="r1"),
            edge(state="stove_on", description="silent", key="r2"),
        ]
        _, provider = run(bundles, fake_llm.always("produces"))
        self.assertEqual(len(fake_llm.parse_items(provider.prompts[0][1])), 2)


class BatchingTests(unittest.TestCase):
    def test_items_are_split_into_batches(self):
        bundles = [edge(state=f"state_{n}", description="silent", key=f"r{n}") for n in range(25)]
        _, provider = run(bundles, fake_llm.always("produces"), LLM_BATCH_SIZE="10")
        self.assertEqual(provider.requests_made, 3)
        self.assertEqual([len(fake_llm.parse_items(p[1])) for p in provider.prompts], [10, 10, 5])

    def test_one_batch_failure_does_not_abort_the_rest(self):
        calls = {"n": 0}

        def flaky(system, user):
            calls["n"] += 1
            if calls["n"] == 1:
                raise fake_llm.ServiceError("first batch died")
            return fake_llm.always("produces")(system, user)

        bundles = [edge(state=f"state_{n}", description="silent", key=f"r{n}") for n in range(4)]
        result, _ = run(bundles, flaky, LLM_BATCH_SIZE="2")
        self.assertEqual(len(result.stamped), 2)
        self.assertEqual({p.reason_code for p in result.parked}, {"service_error"})


class ResilienceTests(unittest.TestCase):
    """FR-7 and Section 11: failures become parked entries, never an abort."""

    def test_persistent_failure_parks_service_error(self):
        result, _ = run([edge(description="silent")], fake_llm.failing())
        self.assertEqual(result.parked[0].reason_code, "service_error")
        self.assertIn("endpoint unavailable", result.parked[0].detail)

    def test_malformed_output_is_reprompted_once_then_parked(self):
        result, provider = run([edge(description="silent")], fake_llm.malformed())
        self.assertEqual(provider.requests_made, 2)
        self.assertEqual(result.parked[0].reason_code, "service_error")
        self.assertEqual(result.calls.reprompts, 1)

    def test_a_successful_reprompt_is_stamped(self):
        result, provider = run(
            [edge(description="silent")],
            fake_llm.malformed_once(fake_llm.always("requires")),
        )
        self.assertEqual(provider.requests_made, 2)
        self.assertEqual(result.stamped[0].edge_type, "requires")

    def test_a_label_outside_the_vocabulary_is_malformed(self):
        def rogue(_system, user):
            item = fake_llm.parse_items(user)[0]
            return f'{{"items": [{{"id": "{item["id"]}", "relation": "maybe", "confidence": 1.0}}]}}'

        result, _ = run([edge(description="silent")], rogue)
        self.assertEqual(result.parked[0].reason_code, "service_error")

    def test_a_missing_item_in_the_reply_parks_that_item(self):
        def partial(_system, user):
            first = fake_llm.parse_items(user)[0]
            return (
                f'{{"items": [{{"id": "{first["id"]}", "relation": "produces", '
                f'"confidence": 0.9, "rationale": "ok"}}]}}'
            )

        bundles = [
            edge(state="a", description="silent", key="r1"),
            edge(state="b", description="silent", key="r2"),
        ]
        result, _ = run(bundles, partial)
        self.assertEqual(len(result.stamped), 1)
        self.assertEqual(result.parked[0].reason_code, "service_error")

    def test_request_cap_stops_calling_and_parks_the_rest(self):
        bundles = [edge(state=f"s{n}", description="silent", key=f"r{n}") for n in range(10)]
        result, provider = run(
            bundles, fake_llm.always("produces"), LLM_BATCH_SIZE="2", LLM_MAX_REQUESTS="2"
        )
        self.assertEqual(provider.requests_made, 2)
        self.assertEqual(len(result.stamped), 4)
        self.assertEqual(len(result.parked), 6)
        self.assertEqual({p.reason_code for p in result.parked}, {"service_error"})

    def test_a_zero_cap_makes_no_call_at_all(self):
        result, provider = run([edge(description="silent")], LLM_MAX_REQUESTS="0")
        self.assertEqual(provider.requests_made, 0)
        self.assertEqual(result.parked[0].reason_code, "service_error")


class ConservationTests(unittest.TestCase):
    """FR-8: every input exits as exactly one of stamped or parked."""

    def test_holds_on_a_mixed_set(self):
        bundles = [
            edge(description="requires the pan as a precondition", key="r1"),
            edge(state="s2", description="", key="r2"),
            edge(state="s3", description="silent", key="r3"),
            edge(state="s4", description="silent", key="r4"),
        ]
        result, _ = run(bundles, fake_llm.always("unclear"))
        self.assertEqual(result.total_input, 4)
        self.assertEqual(result.total_output, 4)

    def test_holds_when_everything_fails(self):
        bundles = [edge(state=f"s{n}", description="silent", key=f"r{n}") for n in range(5)]
        result, _ = run(bundles, fake_llm.failing())
        self.assertEqual(result.total_output, 5)

    def test_empty_input_is_conserved(self):
        result, _ = run([])
        self.assertEqual(result.counts_by_bucket(), {"stamped": 0, "parked": 0})

    def test_duplicates_all_survive(self):
        bundles = [edge(description="silent", key=f"r{n}") for n in range(4)]
        result, _ = run(bundles, fake_llm.always("produces"))
        self.assertEqual(result.total_output, 4)

    def test_violation_raises(self):
        result, _ = run([edge(description="")])
        result.total_input = 99
        with self.assertRaises(ConservationError):
            result.assert_conservation()

    def test_no_bundle_appears_twice(self):
        bundles = [edge(state=f"s{n}", description="silent", key=f"r{n}") for n in range(6)]
        result, _ = run(bundles, fake_llm.always("produces", 0.4))
        keys = [record.bundle.relation_key for record in result.all_records()]
        self.assertEqual(len(keys), len(set(keys)))


class SummaryTests(unittest.TestCase):
    """FR-8 / Section 13: label counts, method split, reason codes, mean confidence."""

    def test_mean_confidence_is_reported(self):
        bundles = [edge(state="a", description="silent", key="r1"), edge(state="b", description="silent", key="r2")]
        result, _ = run(bundles, fake_llm.always("produces", 0.8))
        self.assertAlmostEqual(result.mean_confidence(), 0.8)

    def test_mean_confidence_is_none_when_nothing_stamped(self):
        result, _ = run([edge(description="")])
        self.assertIsNone(result.mean_confidence())

    def test_method_split_counts_both_paths(self):
        bundles = [
            edge(description="requires the pan as a precondition", key="r1"),
            edge(state="b", description="silent", key="r2"),
        ]
        result, _ = run(bundles, fake_llm.always("produces"))
        self.assertEqual(dict(result.counts_by_method()), {"lexical": 1, "llm": 1})

    def test_label_counts(self):
        bundles = [
            edge(description="the effect is that the pan is on the stove", key="r1"),
            edge(state="b", description="requires it as a precondition", key="r2"),
        ]
        result, _ = run(bundles)
        self.assertEqual(dict(result.counts_by_label()), {"produces": 1, "requires": 1})

    def test_model_is_recorded_on_llm_verdicts_only(self):
        bundles = [
            edge(description="requires the pan as a precondition", key="r1"),
            edge(state="b", description="silent", key="r2"),
        ]
        result, _ = run(bundles, fake_llm.always("produces"))
        by_method = {e.method: e for e in result.stamped}
        self.assertEqual(by_method["llm"].model, "test/model-1")
        self.assertIsNone(by_method["lexical"].model)

    def test_run_timestamp_is_recorded(self):
        result, _ = run([edge(description="silent")], fake_llm.always("produces"))
        self.assertTrue(result.stamped[0].run_timestamp)


if __name__ == "__main__":
    unittest.main()
