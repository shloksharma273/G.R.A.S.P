"""One source, one rulebook per task: the reference, the task choice, the slice.

The reference here is the real one - `generated/rulebook_operate_openamrobot.md`,
the whole-robot rulebook the fourteen OpenAMRobot task rulebooks were written
from - so what the slicer is checked against is those hand-made rulebooks.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from rulebook_generator.config import load_generator_config
from rulebook_generator.parse import parse_file
from rulebook_generator.schema import ACCEPT
from rulebook_generator.split import (
    METHOD_LLM,
    METHOD_RULES,
    STAGE_CHOOSING,
    STAGE_SLICING,
    Task,
    TaskChoiceError,
    check_tasks,
    choose_tasks,
    generate_split,
    grade,
    slice_rulebook,
    tasks_by_rule,
)
from rulebook_generator.transcript import Transcript
from llm_disambiguator.provider import ServiceError

from . import fake_llm

ROOT = Path(__file__).resolve().parent.parent
REFERENCE = parse_file(ROOT / "generated" / "rulebook_operate_openamrobot.md")

GEN_ENV = {
    "LLM_API_KEY": "test-key-not-real",
    "LLM_MODEL": "test/model-1",
    "RULEBOOK_CACHE": "0",
    "RULEBOOK_CHECK_GROUNDING": "0",
}

TASKS = [
    {"skill": "go_to_pose", "title": "OpenAMRobot Go To Pose", "overview": "Go to x, y.",
     "goals": ["go_to_location"], "assumes": []},
    {"skill": "dock_at_charger", "title": "OpenAMRobot Dock", "overview": "Dock.",
     "goals": ["dock_robot"], "assumes": []},
    {"skill": "undock_from_charger", "title": "OpenAMRobot Undock", "overview": "Undock.",
     "goals": ["undock_robot"], "assumes": ["robot_docked"]},
]


def steps(rulebook):
    return [p.name for p in rulebook.primitives]


def hand_made(name):
    return parse_file(ROOT / "generated" / f"rulebook_{name}.md")


def responder(tasks=TASKS, reference=REFERENCE):
    """Answer the extraction prompt with the reference, the task prompt with `tasks`."""

    def respond(system, user):
        if "cut into one" in system and "rulebook per TASK" in system:
            return json.dumps({"tasks": tasks}) if not callable(tasks) else tasks()
        return json.dumps(reference.to_dict())

    return respond


def provider(respond):
    return fake_llm.FakeProvider(fake_llm.config(), respond)


class SliceTest(unittest.TestCase):
    """The slice reproduces the hand-made task rulebooks from the reference."""

    CASES = {
        "dock_at_charger": ["dock_robot"],
        "patrol_waypoints": ["follow_waypoints"],
        "rotate_in_place": ["spin_in_place"],
        "back_up_robot": ["back_up"],
        "drive_straight_distance": ["drive_on_heading"],
        "cancel_navigation_goal": ["cancel_navigation"],
        "plan_path_to_location": ["compute_path_to_pose"],
        "drive_planned_path": ["follow_path"],
        "drive_with_cmd_vel": ["drive_with_velocity"],
        "test_motors": ["run_open_loop_motor_test"],
        "clear_costmaps": ["clear_local_costmap", "clear_global_costmap"],
    }

    def test_each_slice_holds_exactly_the_hand_made_rulebooks_steps(self):
        for name, goals in self.CASES.items():
            with self.subTest(name):
                sliced = slice_rulebook(REFERENCE, Task(f"{name}_x", name, "", goals))
                self.assertEqual(steps(sliced), steps(hand_made(name)))

    def test_go_to_location_differs_only_by_the_goal_steps_name(self):
        sliced = slice_rulebook(REFERENCE, Task("go_to_pose", "t", "", ["go_to_location"]))
        self.assertEqual(steps(sliced)[:-1], steps(hand_made("go_to_location"))[:-1])

    def test_every_slice_passes_the_gate(self):
        for name, goals in self.CASES.items():
            with self.subTest(name):
                graded = grade(slice_rulebook(REFERENCE, Task(f"{name}_x", name, "", goals)), None, False)
                self.assertEqual(graded.verdict, ACCEPT, graded.reason)

    def test_steps_keep_their_execution_handles_and_reference_order(self):
        sliced = slice_rulebook(REFERENCE, Task("dock_x", "t", "", ["dock_robot"]))
        for primitive in sliced.primitives:
            self.assertEqual(primitive.interface, REFERENCE.by_name(primitive.name).interface)
        order = steps(REFERENCE)
        self.assertEqual(steps(sliced), sorted(steps(sliced), key=order.index))

    def test_an_assumed_state_is_not_chased_and_is_written_into_the_overview(self):
        sliced = slice_rulebook(
            REFERENCE, Task("undock_x", "t", "Undock.", ["undock_robot"], ["robot_docked"])
        )
        self.assertEqual(steps(sliced), ["undock_robot"])
        self.assertNotIn("robot_docked", sliced.primitives[0].requires)
        self.assertIn("Assumes: robot docked.", sliced.overview)

    def test_without_the_assumption_the_whole_docking_sequence_is_pulled_in(self):
        sliced = slice_rulebook(REFERENCE, Task("undock_x", "t", "", ["undock_robot"]))
        self.assertIn("dock_robot", steps(sliced))

    def test_a_one_step_task_is_not_flagged_for_having_nothing_to_order(self):
        graded = grade(
            slice_rulebook(REFERENCE, Task("undock_x", "t", "", ["undock_robot"], ["robot_docked"])),
            None, False,
        )
        self.assertEqual(graded.verdict, ACCEPT, graded.reason)

    def test_the_slice_declares_only_the_objects_and_states_it_uses(self):
        sliced = slice_rulebook(REFERENCE, Task("spin_x", "t", "", ["spin_in_place"]))
        used = {s for p in sliced.primitives for s in (*p.requires, *p.produces)}
        self.assertEqual(set(sliced.states), used)
        self.assertEqual(set(sliced.objects), {o for p in sliced.primitives for o in p.uses})


class GuardTest(unittest.TestCase):
    def test_a_good_task_list_passes(self):
        tasks = check_tasks(TASKS, REFERENCE)
        self.assertEqual([t.skill for t in tasks], ["go_to_pose", "dock_at_charger", "undock_from_charger"])
        self.assertEqual(tasks[2].assumes, ["robot_docked"])

    def refused(self, items, needle):
        with self.assertRaises(TaskChoiceError) as caught:
            check_tasks(items, REFERENCE)
        self.assertIn(needle, str(caught.exception))

    def test_a_skill_named_after_a_step_is_refused(self):
        self.refused([{"skill": "go_to_location", "goals": ["go_to_location"]}], "name of a step")

    def test_a_goal_that_is_not_a_step_is_refused(self):
        self.refused([{"skill": "fly", "goals": ["take_off"]}], "take_off")

    def test_two_tasks_with_one_name_are_refused(self):
        self.refused([{"skill": "a_task", "goals": ["back_up"]}] * 2, "two tasks")

    def test_an_assumption_the_reference_never_mentions_is_refused(self):
        self.refused([{"skill": "a_task", "goals": ["back_up"], "assumes": ["robot_flying"]}], "robot_flying")

    def test_an_empty_list_is_refused(self):
        self.refused([], "no tasks")


class ChoiceTest(unittest.TestCase):
    def test_the_models_list_is_used_when_it_passes_the_guard(self):
        choice = choose_tasks(REFERENCE, provider(responder()), "m")
        self.assertEqual(choice.method, METHOD_LLM)
        self.assertFalse(choice.reprompted)
        self.assertEqual(len(choice.tasks), 3)

    def test_a_rejected_list_is_reprompted_once_with_the_reason(self):
        replies = iter([
            json.dumps({"tasks": [{"skill": "back_up", "goals": ["back_up"]}]}),
            json.dumps({"tasks": TASKS}),
        ])
        fake = provider(responder(tasks=lambda: next(replies)))
        choice = choose_tasks(REFERENCE, fake, "m")
        self.assertEqual(choice.method, METHOD_LLM)
        self.assertTrue(choice.reprompted)
        self.assertIn("name of a step", fake.prompts[1][0])

    def test_two_rejections_fall_back_to_one_task_per_final_step(self):
        fake = provider(responder(tasks=lambda: "not json"))
        choice = choose_tasks(REFERENCE, fake, "m")
        self.assertEqual(choice.method, METHOD_RULES)
        self.assertIn("rejected the task list twice", choice.fallback_reason)
        self.assertEqual(choice.tasks, tasks_by_rule(REFERENCE))

    def test_an_unavailable_model_falls_back(self):
        def down(system, user):
            raise ServiceError("503")

        choice = choose_tasks(REFERENCE, provider(down), "m")
        self.assertEqual(choice.method, METHOD_RULES)
        self.assertIn("unavailable", choice.fallback_reason)

    def test_no_model_falls_back(self):
        self.assertEqual(choose_tasks(REFERENCE, None).method, METHOD_RULES)

    def test_the_rule_fallback_never_names_a_skill_after_a_step(self):
        names = set(steps(REFERENCE))
        for task in tasks_by_rule(REFERENCE):
            self.assertNotIn(task.skill, names)
            self.assertEqual(len(task.goals), 1)

    def test_a_cached_list_makes_no_request(self):
        class Cache(dict):
            def put(self, key, value):
                self[key] = value

        cache = Cache()
        first = provider(responder())
        choose_tasks(REFERENCE, first, "m", cache)
        second = provider(responder())
        choice = choose_tasks(REFERENCE, second, "m", cache)
        self.assertTrue(choice.from_cache)
        self.assertEqual(second.requests_made, 0)


class GenerateSplitTest(unittest.TestCase):
    def run_split(self, respond=None, **env):
        config = load_generator_config({**GEN_ENV, **env})
        transcript = Transcript(text="openamrobot docs " * 50, video_id="docs", source="code", url="x")
        stages = []
        split = generate_split(
            transcript, config, provider=provider(respond or responder()), on_stage=stages.append
        )
        return split, stages

    def test_a_reference_and_one_graded_rulebook_per_task(self):
        split, stages = self.run_split()
        self.assertEqual(split.reference.rulebook.skill, "operate_openamrobot")
        self.assertEqual([t.rulebook.skill for t in split.tasks],
                         ["go_to_pose", "dock_at_charger", "undock_from_charger"])
        self.assertTrue(all(t.verdict == ACCEPT for t in split.tasks), [t.reason for t in split.tasks])
        self.assertIn(STAGE_CHOOSING, stages)
        self.assertIn(STAGE_SLICING, stages)

    def test_the_reference_is_asked_for_every_operation(self):
        fake = provider(responder())
        config = load_generator_config(GEN_ENV)
        transcript = Transcript(text="docs " * 50, video_id="d", source="manual", url="x")
        generate_split(transcript, config, provider=fake)
        self.assertIn("REFERENCE rulebook for the WHOLE system", fake.prompts[0][0])

    def test_the_references_size_is_not_held_against_it(self):
        split, _ = self.run_split()
        codes = {i.code for i in split.reference.report.issues}
        self.assertNotIn("suspicious_size", codes)
        self.assertNotIn("multiple_tasks", codes)

    def test_each_task_rulebook_renders_its_own_skill(self):
        split, _ = self.run_split()
        for book in split.tasks:
            self.assertIn(f"**{book.rulebook.skill}** is a high-level skill", book.markdown)

    def test_a_non_procedural_source_cuts_nothing(self):
        split, _ = self.run_split(lambda s, u: json.dumps({"not_procedural": True, "reason": "a song"}))
        self.assertEqual(split.tasks, [])
        self.assertIn("a song", split.reason)

    def test_serialization_carries_every_rulebook(self):
        split, _ = self.run_split()
        payload = split.to_dict()
        self.assertEqual(len(payload["tasks"]), 3)
        self.assertEqual(payload["method"], METHOD_LLM)


if __name__ == "__main__":
    unittest.main()


class BudgetTest(unittest.TestCase):
    def test_the_reference_gets_a_larger_output_budget_than_one_task(self):
        config = load_generator_config(GEN_ENV)
        self.assertEqual(config.reference_max_tokens, 32000)
        self.assertGreater(config.reference_max_tokens, config.llm.max_tokens)

    def test_the_budget_is_configurable_and_bounded(self):
        from kg_read_harness.errors import ConfigError

        self.assertEqual(
            load_generator_config({**GEN_ENV, "RULEBOOK_REFERENCE_MAX_TOKENS": "48000"}).reference_max_tokens,
            48000,
        )
        with self.assertRaises(ConfigError):
            load_generator_config({**GEN_ENV, "RULEBOOK_REFERENCE_MAX_TOKENS": "10"})

    def test_a_reply_cut_off_mid_json_says_so(self):
        import importlib

        extract = importlib.import_module("rulebook_generator.extract")
        for cut in ('{"skill": "x", "primitives": [{"name": "a"', '{"skill": "x", "narr'):
            with self.subTest(cut):
                with self.assertRaises(extract.ExtractionFailed) as caught:
                    extract.parse_reply(cut)
                self.assertIn("cut off at the model's output limit", str(caught.exception))
        with self.assertRaises(extract.ExtractionFailed) as caught:
            extract.parse_reply('{"skill": x}')
        self.assertIn("not valid JSON", str(caught.exception))


class FaultyReferenceTest(unittest.TestCase):
    def test_a_shell_command_is_not_kept_as_an_execution_handle(self):
        from rulebook_generator.schema import Interface

        self.assertIsNone(Interface.from_dict(
            {"kind": "api", "name": "source /opt/ros/jazzy/setup.bash && export X=1", "type": "shell"}
        ))
        self.assertEqual(
            Interface.from_dict({"kind": "service", "name": "/dock_trigger", "type": "std_srvs/srv/Trigger"}).name,
            "/dock_trigger",
        )

    def test_a_rejected_reference_still_yields_the_tasks_that_avoid_its_fault(self):
        import copy

        faulty = copy.deepcopy(REFERENCE)
        # Two steps that each need what the other produces: a cycle only a task
        # reaching them can inherit.
        save, load = faulty.by_name("save_map"), faulty.by_name("load_map")
        save.requires.append("map_loaded_again")
        load.produces.append("map_loaded_again")
        load.requires.append("map_saved")
        tasks = [
            {"skill": "dock_at_charger", "title": "t", "overview": "", "goals": ["dock_robot"]},
            {"skill": "switch_map", "title": "t", "overview": "", "goals": ["load_map"]},
        ]
        config = load_generator_config(GEN_ENV)
        transcript = Transcript(text="docs " * 50, video_id="d", source="code", url="x")
        split = generate_split(transcript, config, provider=provider(responder(tasks, faulty)))
        self.assertEqual(split.reference.verdict, "reject")
        self.assertIn("graded on its own", split.reason)
        verdicts = {t.rulebook.skill: t.verdict for t in split.tasks}
        self.assertEqual(verdicts["dock_at_charger"], ACCEPT)
        self.assertEqual(verdicts["switch_map"], "reject")


class VariantTest(unittest.TestCase):
    """Two bring-ups that reach the same states: the slice must run one, not both."""

    def reference(self):
        from rulebook_generator.schema import Primitive, Rulebook

        return Rulebook(
            skill="operate_bot", title="Bot",
            primitives=[
                Primitive("launch_real_bringup", "Real robot.", [], ["localization_active", "navigation_active"]),
                Primitive("launch_sim_bringup", "Simulation.", [], ["localization_active", "navigation_active"]),
                Primitive("launch_localization_only", "Just AMCL.", [], ["localization_active"]),
                Primitive("send_goal", "Go.", ["localization_active", "navigation_active"], ["robot_at_goal"]),
            ],
        )

    def test_one_bring_up_covers_every_state_it_can(self):
        sliced = slice_rulebook(self.reference(), Task("go_x", "t", "", ["send_goal"]))
        self.assertEqual(steps(sliced), ["launch_real_bringup", "send_goal"])

    def test_via_picks_the_variant(self):
        sliced = slice_rulebook(self.reference(), Task("go_x", "t", "", ["send_goal"], via=["launch_sim_bringup"]))
        self.assertEqual(steps(sliced), ["launch_sim_bringup", "send_goal"])

    def test_via_must_name_a_step(self):
        with self.assertRaises(TaskChoiceError):
            check_tasks([{"skill": "go_x", "goals": ["send_goal"], "via": ["teleport"]}], self.reference())


class PromptKeyedCacheTest(unittest.TestCase):
    def test_a_changed_prompt_is_not_served_an_old_reply(self):
        import importlib

        extract = importlib.import_module("rulebook_generator.extract")

        class Cache(dict):
            def put(self, key, value):
                self[key] = value

        cache = Cache()
        transcript = Transcript(text="docs " * 50, video_id="d", source="code", url="x")
        first = provider(responder())
        extract.extract(transcript, first, "m", cache=cache, catalog=True)
        second = provider(responder())
        extract.extract(transcript, second, "m", cache=cache, catalog=True)
        self.assertEqual(second.requests_made, 0)  # same prompt: served from cache

        original = extract.CATALOG_SUFFIX
        extract.CATALOG_SUFFIX = original + "\n- one more rule"
        try:
            third = provider(responder())
            extract.extract(transcript, third, "m", cache=cache, catalog=True)
            self.assertEqual(third.requests_made, 1)
        finally:
            extract.CATALOG_SUFFIX = original
