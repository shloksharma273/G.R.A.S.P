"""The Rulebook Generator: transcript, extraction, rendering, and the gate."""

from __future__ import annotations

import io
import json
import os
import tempfile
import unittest

from rulebook_generator import (
    ACCEPT,
    FLAG,
    REJECT,
    ExtractionFailed,
    NotProcedural,
    Primitive,
    Rulebook,
    extract,
    generate,
    parse_file,
    parse_text,
    render,
    to_bundles,
    validate,
)
from rulebook_generator.cache import PayloadCache
from rulebook_generator.cli import EXIT_FLAGGED, EXIT_REJECTED, run
from rulebook_generator.config import load_generator_config
from rulebook_generator.extract import build_system_prompt, parse_reply, validate_shape
from rulebook_generator.ingest import ingest, write_rulebook
from rulebook_generator.report import dump_json, print_report
from rulebook_generator.transcript import (
    BadVideoUrl,
    NoCaptions,
    Transcript,
    clean,
    from_file,
    truncate,
    video_id,
)
from rulebook_generator.validate import check_grounding

from . import fake_llm

ENV = {"LLM_API_KEY": "test-key-not-real", "RULEBOOK_CACHE": "0", "LLM_MODEL": "test/model-1"}


def config(**overrides):
    return load_generator_config({**ENV, **overrides})


def transcript(text="the robot places the pan and adds water then boils it") -> Transcript:
    return Transcript(text=text, video_id="TESTVIDEO01", source="file", url="http://x")


CHAI = {
    "skill": "make_tea",
    "title": "Tea",
    "overview": "How to make tea.",
    "objects": ["kettle", "water", "cup"],
    "states": ["water_boiling", "tea_in_cup"],
    "primitives": [
        {"name": "boil_water", "narration": "The robot boils the water.",
         "requires": [], "produces": ["water_boiling"], "uses": ["kettle", "water"]},
        {"name": "pour_tea", "narration": "The robot pours the tea into the cup.",
         "requires": ["water_boiling"], "produces": ["tea_in_cup"], "uses": ["cup"]},
    ],
    "ordering": ["The water must boil before the tea is poured."],
}


def responder(payload):
    def respond(_system, _user):
        return json.dumps(payload)

    return respond


class VideoIdTests(unittest.TestCase):
    def test_watch_url(self):
        self.assertEqual(video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_short_url(self):
        self.assertEqual(video_id("https://youtu.be/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_shorts_url(self):
        self.assertEqual(video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_bare_id(self):
        self.assertEqual(video_id("dQw4w9WgXcQ"), "dQw4w9WgXcQ")

    def test_url_with_extra_params(self):
        self.assertEqual(
            video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=42s&list=X"), "dQw4w9WgXcQ"
        )

    def test_nonsense_is_an_actionable_error(self):
        with self.assertRaises(BadVideoUrl) as caught:
            video_id("https://example.com/whatever")
        self.assertIn("--transcript", caught.exception.render())


class CleanTests(unittest.TestCase):
    def test_music_and_sound_marks_are_dropped(self):
        self.assertEqual(clean("[Music] hello [applause] world"), "hello world")

    def test_speaker_marks_are_dropped(self):
        self.assertEqual(clean(">> hello there"), "hello there")

    def test_overlapping_cues_are_deduped(self):
        """Auto-captions repeat the tail of the previous cue; joining naively triples it."""
        raw = "put the pan\nput the pan on the stove\non the stove and add water"
        self.assertEqual(clean(raw), "put the pan on the stove and add water")

    def test_whitespace_is_normalized(self):
        self.assertEqual(clean("a\n\n  b   c\n"), "a b c")

    def test_empty_captions_stay_empty(self):
        self.assertEqual(clean("[Music]\n[Applause]"), "")


class TranscriptTests(unittest.TestCase):
    def test_from_file(self):
        handle, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write("boil the water\nadd the tea")
        self.addCleanup(os.unlink, path)
        result = from_file(path)
        self.assertEqual(result.text, "boil the water add the tea")
        self.assertEqual(result.source, "file")

    def test_an_empty_file_fails_clearly(self):
        handle, path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write("[Music]\n")
        self.addCleanup(os.unlink, path)
        with self.assertRaises(NoCaptions):
            from_file(path)

    def test_the_digest_is_content_derived(self):
        self.assertEqual(transcript("a").digest, transcript("a").digest)
        self.assertNotEqual(transcript("a").digest, transcript("b").digest)

    def test_truncation_is_reported(self):
        long_one = transcript(" ".join(["word"] * 100))
        bounded, was_truncated = truncate(long_one, max_words=10)
        self.assertTrue(was_truncated)
        self.assertEqual(bounded.words, 10)

    def test_short_transcripts_are_untouched(self):
        _bounded, was_truncated = truncate(transcript("short"), max_words=10)
        self.assertFalse(was_truncated)


class ExtractionTests(unittest.TestCase):
    """FR-3 — the schema-constrained pass."""

    def _provider(self, respond):
        return fake_llm.FakeProvider(fake_llm.config(), respond)

    def test_the_prompt_states_the_inference_job(self):
        prompt = build_system_prompt()
        self.assertIn("RECONSTRUCTION", prompt)
        self.assertIn("ALMOST NEVER STATED", prompt)
        self.assertIn("GROUNDED", prompt)

    def test_the_prompt_carries_a_worked_example(self):
        prompt = build_system_prompt()
        self.assertIn("make_masala_chai", prompt)
        self.assertIn("pan_on_stove", prompt)

    def test_a_well_formed_reply_becomes_an_intermediate(self):
        result = extract(transcript(), self._provider(responder(CHAI)), "m")
        self.assertEqual(result.rulebook.skill, "make_tea")
        self.assertEqual(len(result.rulebook.primitives), 2)
        self.assertEqual(result.rulebook.primitives[1].requires, ["water_boiling"])

    def test_names_are_normalized(self):
        payload = {**CHAI, "skill": "Make Tea", "objects": ["The Kettle"]}
        result = extract(transcript(), self._provider(responder(payload)), "m")
        self.assertEqual(result.rulebook.skill, "make_tea")
        self.assertEqual(result.rulebook.objects, ["kettle"])

    def test_a_non_procedural_reply_is_recognized(self):
        payload = {"not_procedural": True, "reason": "this is a music video"}
        result = extract(transcript(), self._provider(responder(payload)), "m")
        self.assertTrue(result.not_procedural)
        self.assertIn("music video", result.reason)

    def test_prose_is_reprompted_once_then_fails(self):
        provider = self._provider(fake_llm.malformed("Sure! Here is your rulebook."))
        with self.assertRaises(ExtractionFailed):
            extract(transcript(), provider, "m")
        self.assertEqual(provider.requests_made, 2)

    def test_a_successful_reprompt_is_used(self):
        provider = self._provider(fake_llm.malformed_once(responder(CHAI)))
        result = extract(transcript(), provider, "m")
        self.assertTrue(result.reprompted)
        self.assertEqual(result.rulebook.skill, "make_tea")

    def test_a_reply_with_no_primitives_is_rejected(self):
        with self.assertRaises(ExtractionFailed):
            validate_shape({"skill": "x", "primitives": []})

    def test_a_reply_with_no_skill_is_rejected(self):
        with self.assertRaises(ExtractionFailed):
            validate_shape({"primitives": [{"name": "a"}]})

    def test_code_fences_are_tolerated(self):
        self.assertEqual(parse_reply('```json\n{"a": 1}\n```'), {"a": 1})

    def test_not_procedural_raises_from_validate_shape(self):
        with self.assertRaises(NotProcedural):
            validate_shape({"not_procedural": True, "reason": "a vlog"})


class CacheTests(unittest.TestCase):
    """FR-6 — the same video yields the same rulebook, with no second call."""

    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.path = os.path.join(self.directory, "cache.json")
        self.addCleanup(self._clean)

    def _clean(self):
        for name in os.listdir(self.directory):
            os.unlink(os.path.join(self.directory, name))
        os.rmdir(self.directory)

    def test_a_warm_run_makes_no_call(self):
        cache = PayloadCache(self.path)
        provider = fake_llm.FakeProvider(fake_llm.config(), responder(CHAI))
        extract(transcript(), provider, "m", cache=cache)
        cache.save()
        self.assertEqual(provider.requests_made, 1)

        warm = PayloadCache(self.path)
        cold_provider = fake_llm.FakeProvider(fake_llm.config(), fake_llm.failing("must not call"))
        result = extract(transcript(), cold_provider, "m", cache=warm)
        self.assertEqual(cold_provider.requests_made, 0)
        self.assertTrue(result.from_cache)
        self.assertEqual(result.rulebook.skill, "make_tea")

    def test_a_different_transcript_misses(self):
        cache = PayloadCache(self.path)
        provider = fake_llm.FakeProvider(fake_llm.config(), responder(CHAI))
        extract(transcript("one"), provider, "m", cache=cache)
        extract(transcript("two"), provider, "m", cache=cache)
        self.assertEqual(provider.requests_made, 2)

    def test_a_different_model_misses(self):
        cache = PayloadCache(self.path)
        provider = fake_llm.FakeProvider(fake_llm.config(), responder(CHAI))
        extract(transcript(), provider, "m1", cache=cache)
        extract(transcript(), provider, "m2", cache=cache)
        self.assertEqual(provider.requests_made, 2)

    def test_a_corrupt_cache_degrades_to_empty(self):
        with open(self.path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual(len(PayloadCache(self.path)), 0)


class RenderTests(unittest.TestCase):
    """FR-4 — deterministic, and in the corpus's own phrasing."""

    def setUp(self):
        self.rulebook = Rulebook.from_dict(CHAI)
        self.markdown = render(self.rulebook)

    def test_every_canonical_section_is_present(self):
        for heading in ("## Overview", "## Skill", "## Objects", "## States",
                        "## Primitive actions and their rules", "## Ordering rules"):
            self.assertIn(heading, self.markdown)

    def test_rendering_is_deterministic(self):
        self.assertEqual(render(self.rulebook), self.markdown)

    def test_preconditions_use_the_corpus_phrasing(self):
        """Station 3's lexical pre-pass keys on this wording."""
        self.assertIn("requires that", self.markdown)
        self.assertIn("as a precondition", self.markdown)

    def test_effects_use_the_corpus_phrasing(self):
        self.assertIn("After this action", self.markdown)

    def test_the_skill_is_bold_like_the_corpus(self):
        self.assertIn("**make_tea**", self.markdown)

    def test_ordering_is_derived_when_the_model_states_none(self):
        book = Rulebook.from_dict({**CHAI, "ordering": []})
        self.assertIn("must happen before", render(book))

    def test_the_source_url_is_recorded(self):
        book = Rulebook.from_dict({**CHAI, "source_url": "http://example.com/v"})
        self.assertIn("http://example.com/v", render(book))


class RoundTripTests(unittest.TestCase):
    """parse(render(x)) must recover x — the markdown is the deliverable."""

    def test_the_generated_intermediate_survives(self):
        book = Rulebook.from_dict(CHAI)
        back = parse_text(render(book))
        self.assertEqual(back.skill, book.skill)
        self.assertEqual(back.primitive_names, book.primitive_names)
        for original in book.primitives:
            recovered = back.by_name(original.name)
            self.assertEqual(sorted(recovered.requires), sorted(original.requires))
            self.assertEqual(sorted(recovered.produces), sorted(original.produces))

    def test_the_hand_written_corpus_survives(self):
        """The reader must handle rulebooks it did not write."""
        for path in ("masala_chai_rulebook.md", "dataset/rulebook_cook_burger.md"):
            with self.subTest(path=path):
                book = parse_file(path)
                back = parse_text(render(book))
                self.assertEqual(back.primitive_names, book.primitive_names)

    def test_word_order_is_respected(self):
        """'pan on stove' is not 'water in the pan and the stove is on'."""
        book = Rulebook(
            skill="s",
            states=["pan_on_stove", "stove_on", "water_in_pan"],
            primitives=[
                Primitive(
                    name="boil",
                    narration="The robot boils it.",
                    requires=["water_in_pan", "stove_on"],
                    produces=["water_boiling"],
                )
            ],
        )
        back = parse_text(render(book))
        self.assertEqual(sorted(back.by_name("boil").requires), ["stove_on", "water_in_pan"])


class ValidationGateTests(unittest.TestCase):
    """FR-5 — the gate that stands between inference and a plan."""

    def test_a_clean_rulebook_is_accepted(self):
        report = validate(Rulebook.from_dict(CHAI))
        self.assertEqual(report.verdict, ACCEPT)
        self.assertEqual(report.issues, [])

    def test_every_hand_written_rulebook_is_accepted(self):
        """Calibration: the gate must not reject the corpus it was built from."""
        import glob

        for path in ["masala_chai_rulebook.md"] + sorted(glob.glob("dataset/*.md")):
            with self.subTest(path=path):
                self.assertEqual(validate(parse_file(path)).verdict, ACCEPT)

    def test_the_plan_is_reported(self):
        report = validate(Rulebook.from_dict(CHAI))
        self.assertEqual(report.plan, ["boil_water", "pour_tea"])

    def test_no_primitives_is_rejected(self):
        report = validate(Rulebook(skill="empty"))
        self.assertEqual(report.verdict, REJECT)
        self.assertEqual(report.issues[0].code, "no_primitives")

    def test_an_orphan_precondition_is_flagged_not_rejected(self):
        payload = {**CHAI}
        payload["primitives"] = [
            {"name": "pour_tea", "narration": "Pour it.", "requires": ["water_boiling"],
             "produces": ["tea_in_cup"], "uses": []}
        ]
        report = validate(Rulebook.from_dict(payload))
        self.assertEqual(report.verdict, FLAG)
        self.assertIn("orphan_precondition", [i.code for i in report.issues])

    def test_a_cycle_is_rejected(self):
        payload = {
            "skill": "loop", "objects": [], "states": ["a", "b"],
            "primitives": [
                {"name": "one", "narration": "x", "requires": ["b"], "produces": ["a"], "uses": []},
                {"name": "two", "narration": "y", "requires": ["a"], "produces": ["b"], "uses": []},
            ],
        }
        report = validate(Rulebook.from_dict(payload))
        self.assertEqual(report.verdict, REJECT)
        self.assertIn("cycle", [i.code for i in report.issues])

    def test_a_rulebook_that_achieves_nothing_is_rejected(self):
        payload = {
            "skill": "nothing", "objects": [], "states": [],
            "primitives": [{"name": "wave", "narration": "Wave.", "requires": [],
                            "produces": [], "uses": []}],
        }
        report = validate(Rulebook.from_dict(payload))
        self.assertEqual(report.verdict, REJECT)
        self.assertIn("no_goal_state", [i.code for i in report.issues])

    def test_an_undeclared_state_is_flagged(self):
        payload = {**CHAI, "states": ["water_boiling"]}  # tea_in_cup missing
        report = validate(Rulebook.from_dict(payload))
        self.assertEqual(report.verdict, FLAG)
        self.assertIn("undeclared_state", [i.code for i in report.issues])

    def test_the_gate_uses_the_real_bridge(self):
        bundles = to_bundles(Rulebook.from_dict(CHAI))
        self.assertTrue(any(b.source.type == "SKILL" for b in bundles))
        self.assertTrue(any(b.target.type == "STATE" for b in bundles))

    def test_grounding_flags_an_invented_action(self):
        book = Rulebook.from_dict(CHAI)
        issues = check_grounding(book, "there is no mention of anything here")
        self.assertEqual(issues[0].code, "ungrounded_primitive")

    def test_grounding_accepts_actions_the_narration_mentions(self):
        book = Rulebook.from_dict(CHAI)
        self.assertEqual(check_grounding(book, "first boil the water then pour the tea"), [])

    def test_preconditions_may_be_inferred_without_being_grounded(self):
        """The asymmetry: actions must be stated, preconditions need not be."""
        book = Rulebook.from_dict(CHAI)
        self.assertEqual(check_grounding(book, "boil and pour"), [])


class PipelineTests(unittest.TestCase):
    def _generate(self, respond, **overrides):
        return generate(
            transcript(),
            config(**overrides),
            provider=fake_llm.FakeProvider(fake_llm.config(), respond),
            cache=PayloadCache(None, False),
        )

    def test_a_good_transcript_is_accepted(self):
        result = self._generate(responder(CHAI), RULEBOOK_CHECK_GROUNDING="0")
        self.assertEqual(result.verdict, ACCEPT)
        self.assertTrue(result.markdown)
        self.assertIn("**make_tea**", result.markdown)

    def test_a_non_procedural_video_is_rejected(self):
        result = self._generate(responder({"not_procedural": True, "reason": "a music video"}))
        self.assertEqual(result.verdict, REJECT)
        self.assertIn("music video", result.reason)
        self.assertFalse(result.markdown)

    def test_a_service_error_is_a_rejection_not_a_crash(self):
        result = self._generate(fake_llm.failing("endpoint down"))
        self.assertEqual(result.verdict, REJECT)
        self.assertIn("unavailable", result.reason)

    def test_malformed_output_is_a_rejection(self):
        result = self._generate(fake_llm.malformed("nope"))
        self.assertEqual(result.verdict, REJECT)
        self.assertIn("extraction failed", result.reason)

    def test_the_source_url_is_carried_onto_the_rulebook(self):
        result = self._generate(responder(CHAI), RULEBOOK_CHECK_GROUNDING="0")
        self.assertEqual(result.rulebook.source_url, "http://x")

    def test_grounding_flags_the_run(self):
        result = self._generate(responder(CHAI))
        self.assertEqual(result.verdict, FLAG)
        self.assertIn("ungrounded_primitive", [i.code for i in result.report.issues])


class NeverSilentlyAcceptTests(unittest.TestCase):
    """FR-7 — a broken rulebook is never auto-ingested."""

    def test_strictness_controls_what_is_written(self):
        self.assertEqual(config(RULEBOOK_STRICTNESS="normal").accepts, (ACCEPT,))
        self.assertEqual(config(RULEBOOK_STRICTNESS="lenient").accepts, (ACCEPT, FLAG))

    def test_a_flagged_verdict_never_ingests(self):
        for strictness in ("strict", "normal", "lenient"):
            with self.subTest(strictness=strictness):
                settings = config(RULEBOOK_STRICTNESS=strictness, RULEBOOK_AUTO_INGEST="1")
                self.assertFalse(settings.may_ingest(FLAG))
                self.assertFalse(settings.may_ingest(REJECT))

    def test_only_accept_may_ingest(self):
        self.assertTrue(config(RULEBOOK_AUTO_INGEST="1").may_ingest(ACCEPT))

    def test_ingest_is_off_by_default(self):
        self.assertFalse(config().auto_ingest)

    def test_ingest_without_a_url_says_so_rather_than_failing(self):
        os.environ.pop("AUTOGRAPH_URL", None)
        ingested, detail = ingest("# x", "make_tea")
        self.assertFalse(ingested)
        self.assertIn("AUTOGRAPH_URL is not set", detail)

    def test_write_rulebook_uses_the_canonical_name(self):
        directory = tempfile.mkdtemp()
        path = write_rulebook("# hi", "make_tea", directory)
        self.addCleanup(lambda: (os.unlink(path), os.rmdir(directory)))
        self.assertTrue(path.endswith("rulebook_make_tea.md"))


class CliTests(unittest.TestCase):
    def setUp(self):
        for key, value in ENV.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        handle, self.path = tempfile.mkstemp(suffix=".txt")
        with os.fdopen(handle, "w", encoding="utf-8") as file:
            file.write("first boil the water then pour the tea into the cup")
        self.addCleanup(os.unlink, self.path)

    def _run(self, respond, **kwargs):
        out = io.StringIO()
        code = run(
            transcript_path=self.path,
            provider=fake_llm.FakeProvider(fake_llm.config(), respond),
            no_cache=True,
            stdout=out,
            **kwargs,
        )
        return code, out.getvalue()

    def test_an_accepted_run_exits_zero(self):
        code, text = self._run(responder(CHAI))
        self.assertEqual(code, 0)
        self.assertIn("ACCEPT", text)

    def test_a_rejected_run_exits_three(self):
        code, text = self._run(responder({"not_procedural": True, "reason": "a vlog"}))
        self.assertEqual(code, EXIT_REJECTED)
        self.assertIn("REJECT", text)

    def test_json_output(self):
        _code, text = self._run(responder(CHAI), output_format="json")
        payload = json.loads(text)
        self.assertEqual(payload["verdict"], ACCEPT)
        self.assertEqual(payload["validation"]["plan"], ["boil_water", "pour_tea"])

    def test_show_prints_the_markdown(self):
        _code, text = self._run(responder(CHAI), show=True)
        self.assertIn("## Primitive actions and their rules", text)

    def test_the_report_explains_the_inference(self):
        _code, text = self._run(responder(CHAI))
        self.assertIn("mostly inferred", text)

    def test_ascii_fallback(self):
        class AsciiStream(io.StringIO):
            encoding = "ascii"

        out = AsciiStream()
        run(transcript_path=self.path, no_cache=True, stdout=out,
            provider=fake_llm.FakeProvider(fake_llm.config(), responder(CHAI)))
        out.getvalue().encode("ascii")

    def test_json_dump_round_trips(self):
        result = generate(
            transcript(), config(RULEBOOK_CHECK_GROUNDING="0"),
            provider=fake_llm.FakeProvider(fake_llm.config(), responder(CHAI)),
            cache=PayloadCache(None, False),
        )
        out = io.StringIO()
        dump_json(result, out)
        self.assertEqual(json.loads(out.getvalue())["verdict"], ACCEPT)


class ManualSourceTests(unittest.TestCase):
    """Written documentation is not speech, and must not be cleaned like speech."""

    MANUAL = (
        "Arming the Vehicle\n"
        "Before arming, confirm the following:\n"
        "- GPS lock is acquired (HDOP below 2.0)\n"
        "- Battery voltage above the minimum [see COM_ARM_BAT_MIN]\n"
        "To arm, hold the throttle down and rudder right for one second.\n"
    )

    def _write(self, text):
        handle = tempfile.NamedTemporaryFile(
            "w", suffix=".md", delete=False, encoding="utf-8"
        )
        handle.write(text)
        handle.close()
        self.addCleanup(os.unlink, handle.name)
        return handle.name

    def test_manual_cleaning_keeps_parenthesised_thresholds(self):
        """The parenthesis holds the precondition; caption cleaning deletes it."""
        cleaned = clean(self.MANUAL, caption_artifacts=False)
        self.assertIn("(HDOP below 2.0)", cleaned)
        self.assertIn("[see COM_ARM_BAT_MIN]", cleaned)

    def test_caption_cleaning_still_strips_that_noise(self):
        cleaned = clean(self.MANUAL)
        self.assertNotIn("HDOP", cleaned)
        self.assertNotIn("COM_ARM_BAT_MIN", cleaned)

    def test_manual_cleaning_keeps_the_step_boundaries(self):
        """A manual's bullets are its steps; flattening them loses the structure."""
        self.assertIn("\n", clean(self.MANUAL, caption_artifacts=False))

    def test_manual_cleaning_does_not_de_overlap(self):
        """A repeated line in a procedure is a repeated step, not a duplicated cue."""
        repeated = "lower the landing gear\nlower the landing gear\n"
        self.assertEqual(
            clean(repeated, caption_artifacts=False).count("lower the landing gear"), 2
        )
        self.assertEqual(clean(repeated).count("lower the landing gear"), 1)

    def test_from_file_marks_the_source_as_manual(self):
        path = self._write(self.MANUAL)
        self.assertEqual(from_file(path, manual=True).source, "manual")
        self.assertEqual(from_file(path).source, "file")

    def test_an_empty_manual_is_still_an_error(self):
        with self.assertRaises(NoCaptions):
            from_file(self._write("   \n\n  "), manual=True)

    def test_the_manual_prompt_inverts_the_inference_instruction(self):
        video, manual = build_system_prompt(), build_system_prompt(manual=True)
        self.assertIn("ALMOST NEVER STATED", video)
        self.assertIn("MOSTLY STATED", manual)
        self.assertIn("Do NOT invent preconditions", manual)

    def test_the_manual_prompt_still_demands_grounded_primitives(self):
        """The asymmetry holds in both registers: no invented actions."""
        self.assertIn("GROUNDED", build_system_prompt(manual=True))

    def test_the_manual_prompt_carries_its_own_worked_example(self):
        manual = build_system_prompt(manual=True)
        self.assertIn("run_centrifuge_cycle", manual)
        # `make_masala_chai` also appears in the shared schema, so key on a name
        # unique to the chai worked example itself.
        self.assertNotIn("place_pan", manual)
        self.assertIn("place_pan", build_system_prompt())

    def test_the_two_modes_do_not_share_a_cache_entry(self):
        """Identical text, two prompts, two rulebooks - one key would serve the wrong one."""
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "cache.json")
        cache = PayloadCache(path)
        text = Transcript(text="the robot boils water", video_id="v", source="manual")

        provider = fake_llm.FakeProvider(fake_llm.config(), responder(CHAI))
        extract(text, provider, "m", cache=cache, manual=True)
        extract(text, provider, "m", cache=cache, manual=False)

        self.assertEqual(len(cache), 2)
        self.assertEqual(provider.requests_made, 2)

    def test_the_mode_follows_the_transcript_when_unspecified(self):
        provider = fake_llm.FakeProvider(fake_llm.config(), responder(CHAI))
        extract(
            Transcript(text="power up the vehicle", video_id="v", source="manual"),
            provider, "m",
        )
        system, user = provider.prompts[0]
        self.assertIn("MOSTLY STATED", system)
        self.assertIn("DOCUMENTATION", user)


class GeneratedCorpusTests(unittest.TestCase):
    """Anything the generator has actually written must still pass its own gate."""

    def test_generated_rulebooks_on_disk_reparse_and_validate(self):
        import glob

        paths = sorted(glob.glob("generated/rulebook_*.md"))
        if not paths:
            self.skipTest("no generated rulebooks on disk")
        for path in paths:
            with self.subTest(path=path):
                book = parse_file(path)
                self.assertTrue(book.primitives, f"{path} re-parsed to nothing")
                report = validate(book)
                self.assertNotEqual(report.verdict, REJECT, [i.code for i in report.issues])


class EndToEndTests(unittest.TestCase):
    """Criterion 2: a generated rulebook runs through the full pipeline."""

    def test_a_generated_rulebook_yields_a_plan(self):
        from direction_normalizer import normalize as normalize_edges
        from layer2_planning import load_planner_config, plan_command
        from plangraph_writer import build, write_plangraph
        from plangraph_writer.config import load_writer_config
        from rulebook_generator.validate import resolved_edge
        from rule_preclassifier import classify

        from .corpus_fixture import ENV as WRITER_ENV
        from .fake_writable_arango import make_db

        book = Rulebook.from_dict(CHAI)
        self.assertEqual(validate(book).verdict, ACCEPT)

        classified = classify(to_bundles(book))
        edges = list(classified.stamped)
        for item in classified.deferred:
            bundle = item.bundle
            label = "requires" if "requires" in bundle.description else "produces"
            edges.append(resolved_edge(bundle, label))

        writer_config = load_writer_config(WRITER_ENV, dry_run=False)
        db = make_db()
        result = normalize_edges(edges)
        plan = build(result.finalized, result.derived, writer_config.schema, skill_scope=book.skill)
        write_plangraph(plan, db, writer_config)

        planner = load_planner_config(WRITER_ENV, use_llm=False)
        answer = plan_command("make me tea", db, planner)
        self.assertEqual(answer.goal, "make_tea")
        self.assertEqual(answer.actions, ["boil_water", "pour_tea"])


if __name__ == "__main__":
    unittest.main()
