"""Task decomposition: a compound command into ordered Layer 2 plans."""

from __future__ import annotations

import dataclasses
import io
import json
import os
import tempfile
import unittest

from grasp_web.api import PlannerService
from layer2_planning import load_planner_config, plan_command
from layer2_planning.plan import Clarification, Plan
from layer2_planning.retrieve import load_skills
from plangraph_writer.config import load_writer_config
from plangraph_writer.schema import Schema
from rulebook_generator import render
from rulebook_generator.direct import ingest_rulebook
from rulebook_generator.schema import Interface, Primitive, Rulebook
from task_decomposition import CompoundPlan, decompose, plan_compound, split_by_rules
from task_decomposition.cli import run
from task_decomposition.decompose import METHOD_LLM, METHOD_RULES

from .corpus_fixture import ENV
from .fake_llm import FakeProvider, config as llm_config
from .fake_writable_arango import make_db

SCHEMA = Schema(prefix="roboticsPlanner")


def _bringup():
    return [
        Primitive("start_localization", "The robot brings up localization.", [], ["localization_active"],
                  ["robot"], interface=Interface("service", "/lifecycle_manager_localization/is_active",
                                                 "std_srvs/srv/Trigger")),
        Primitive("set_initial_pose", "The robot sets its pose.", ["localization_active"], ["robot_localized"],
                  ["robot"], interface=Interface("topic", "/initialpose",
                                                 "geometry_msgs/msg/PoseWithCovarianceStamped")),
    ]


GOTO = Rulebook(
    skill="go_to_location",
    objects=["robot"],
    states=["localization_active", "robot_localized", "robot_at_goal"],
    primitives=[
        *_bringup(),
        Primitive("navigate_to_goal", "The robot drives to the goal.", ["robot_localized"], ["robot_at_goal"],
                  ["robot"], interface=Interface("topic", "/goal_pose", "geometry_msgs/msg/PoseStamped")),
    ],
)

DOCK = Rulebook(
    skill="dock_at_charger",
    objects=["robot", "dock"],
    states=["localization_active", "robot_localized", "robot_docked"],
    primitives=[
        *_bringup(),
        Primitive("dock_robot", "The robot docks.", ["robot_localized"], ["robot_docked"], ["dock"],
                  interface=Interface("topic", "/dock_trigger", "std_msgs/msg/Bool")),
    ],
)


def reply(*subtasks):
    return json.dumps({"subtasks": [dict(zip(("command", "skill", "parameters"), s)) for s in subtasks]})


GO_THEN_CHARGE = reply(
    ("go to 2,1", "go_to_location", {"x": 2, "y": 1}),
    ("charge the robot", "dock_at_charger", {}),
)


def scripted(*replies):
    """A provider that answers the decomposer from `replies`, in turn, and gives the
    composer nothing usable so its wording falls back to templates."""
    queue = list(replies)

    def respond(_system, user):
        if "Skill catalog" in user:
            return queue.pop(0) if len(queue) > 1 else queue[0]
        return "not json"

    return FakeProvider(llm_config(), respond)


def llm_planner():
    return dataclasses.replace(load_planner_config(ENV, use_llm=False), use_llm=True, llm=llm_config())


class GraphCase(unittest.TestCase):
    def setUp(self):
        self.db = make_db()
        for rulebook in (GOTO, DOCK):
            handle, path = tempfile.mkstemp(suffix=".md")
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                file.write(render(rulebook))
            self.addCleanup(os.unlink, path)
            ingest_rulebook(path, self.db, load_writer_config(ENV, dry_run=False))
        self.skills = load_skills(self.db, SCHEMA)


class RuleSplitTests(unittest.TestCase):
    def test_it_cuts_on_connectors(self):
        pieces = [s.command for s in split_by_rules("go to 2,1 and then dock; back up, then rotate")]
        self.assertEqual(pieces, ["go to 2,1", "dock", "back up", "rotate"])

    def test_a_bare_and_between_values_is_not_a_connector(self):
        pieces = [s.command for s in split_by_rules("patrol waypoints 1,0 and 2,1")]
        self.assertEqual(pieces, ["patrol waypoints 1,0 and 2,1"])

    def test_a_bare_and_before_a_verb_is(self):
        pieces = [s.command for s in split_by_rules("go to 2,1 and charge the robot")]
        self.assertEqual(pieces, ["go to 2,1", "charge the robot"])

    def test_decimals_are_not_sentence_ends(self):
        pieces = [s.command for s in split_by_rules("back up 0.3 m then rotate")]
        self.assertEqual(pieces, ["back up 0.3 m", "rotate"])


class GuardTests(GraphCase):
    def test_a_valid_reply_is_used(self):
        result = decompose("go to 2,1 and then charge the robot", self.skills, scripted(GO_THEN_CHARGE))
        self.assertEqual(result.method, METHOD_LLM)
        self.assertEqual([s.skill for s in result.subtasks], ["go_to_location", "dock_at_charger"])
        self.assertEqual(result.subtasks[0].parameters, {"x": 2, "y": 1})

    def test_the_catalog_reaches_the_model(self):
        provider = scripted(GO_THEN_CHARGE)
        decompose("go to 2,1 and then charge the robot", self.skills, provider)
        user = provider.prompts[0][1]
        self.assertIn("- dock_at_charger: start_localization, set_initial_pose, dock_robot", user)
        self.assertIn("Command: go to 2,1 and then charge the robot", user)

    def test_an_invented_skill_is_reprompted(self):
        bad = reply(("go to 2,1", "teleport", {}))
        result = decompose("go to 2,1 and then charge the robot", self.skills, scripted(bad, GO_THEN_CHARGE))
        self.assertEqual(result.method, METHOD_LLM)
        self.assertTrue(result.reprompted)

    def test_an_invented_value_is_rejected(self):
        """A coordinate the user never said is a plan that drives somewhere else."""
        bad = reply(("go to 2,1", "go_to_location", {"x": 5, "y": 1}))
        result = decompose("go to 2,1 and then charge the robot", self.skills, scripted(bad))
        self.assertEqual(result.method, METHOD_RULES)
        self.assertIn("x=5", result.fallback_reason)
        self.assertEqual([s.command for s in result.subtasks], ["go to 2,1", "charge the robot"])

    def test_a_named_place_must_be_in_the_command(self):
        bad = reply(("go to the kitchen", "go_to_location", {"location": "garage"}))
        result = decompose("go to the kitchen", self.skills, scripted(bad))
        self.assertEqual(result.method, METHOD_RULES)

    def test_garbage_falls_back_to_rules(self):
        result = decompose("go to 2,1 then dock", self.skills, scripted("I think you mean..."))
        self.assertEqual(result.method, METHOD_RULES)
        self.assertEqual(len(result.subtasks), 2)

    def test_no_llm_means_rules(self):
        result = decompose("go to 2,1 then dock", self.skills, None)
        self.assertEqual((result.method, result.fallback_reason), (METHOD_RULES, "no LLM configured"))


class CompoundTests(GraphCase):
    def test_go_then_charge_is_one_run(self):
        """The point of the whole layer."""
        result = plan_compound(
            "go to 2,1 and then charge the robot", self.db, llm_planner(),
            provider=scripted(GO_THEN_CHARGE),
        )
        self.assertIsInstance(result, CompoundPlan)
        self.assertTrue(result.executable)
        self.assertEqual(
            [s["action"] for s in result.steps],
            ["start_localization", "set_initial_pose", "navigate_to_goal", "dock_robot"],
        )
        self.assertEqual([s["order"] for s in result.steps], [1, 2, 3, 4])
        self.assertEqual([s["subtask"] for s in result.steps], [1, 1, 1, 2])

    def test_repeated_bring_up_is_skipped_and_reported(self):
        result = plan_compound(
            "go to 2,1 and then charge the robot", self.db, llm_planner(),
            provider=scripted(GO_THEN_CHARGE),
        )
        self.assertEqual(
            [(s["action"], s["satisfied_by"]) for s in result.skipped],
            [("start_localization", 1), ("set_initial_pose", 2)],
        )

    def test_values_land_on_the_goal_step_with_its_handle(self):
        result = plan_compound(
            "go to 2,1 and then charge the robot", self.db, llm_planner(),
            provider=scripted(GO_THEN_CHARGE),
        )
        navigate = next(s for s in result.steps if s["action"] == "navigate_to_goal")
        self.assertEqual(navigate["parameters"], {"x": 2, "y": 1})
        self.assertEqual(navigate["interface"]["name"], "/goal_pose")
        self.assertEqual(result.steps[0]["parameters"], {})

    def test_a_repeated_goal_is_not_deduplicated(self):
        """"go to 1,0 then go to 2,1" navigates twice - the goal is what was asked for."""
        twice = reply(
            ("go to 1,0", "go_to_location", {"x": 1, "y": 0}),
            ("go to 2,1", "go_to_location", {"x": 2, "y": 1}),
        )
        result = plan_compound("go to 1,0 then go to 2,1", self.db, llm_planner(), provider=scripted(twice))
        navigations = [s for s in result.steps if s["action"] == "navigate_to_goal"]
        self.assertEqual([n["parameters"] for n in navigations], [{"x": 1, "y": 0}, {"x": 2, "y": 1}])

    def test_the_model_resolves_what_words_cannot(self):
        """"charge" shares no word with dock_at_charger's steps; the model maps it anyway."""
        result = plan_compound(
            "go to 2,1 and then charge the robot", self.db, llm_planner(),
            provider=scripted(GO_THEN_CHARGE),
        )
        self.assertEqual(result.subtasks[1].result.goal, "dock_at_charger")
        self.assertEqual(result.subtasks[1].result.meta["match_method"], "decomposer")

    def test_rules_without_an_llm(self):
        result = plan_compound(
            "go to location then dock at charger", self.db, load_planner_config(ENV, use_llm=False)
        )
        self.assertIsInstance(result, CompoundPlan)
        self.assertEqual([s.result.goal for s in result.subtasks], ["go_to_location", "dock_at_charger"])
        self.assertEqual(result.decomposition.method, METHOD_RULES)

    def test_an_unmatched_task_blocks_execution(self):
        result = plan_compound(
            "go to location then reticulate the splines", self.db, load_planner_config(ENV, use_llm=False)
        )
        self.assertFalse(result.executable)
        self.assertIsInstance(result.subtasks[1].result, Clarification)
        self.assertEqual({s["subtask"] for s in result.steps}, {1})

    def test_a_single_task_is_layer_2s_own_answer(self):
        config = load_planner_config(ENV, use_llm=False)
        single = plan_compound("go to location", self.db, config, now="t")
        direct = plan_command("go to location", self.db, config, now="t")
        self.assertIsInstance(single, Plan)
        self.assertEqual(single.actions, direct.actions)
        self.assertEqual(single.meta["decomposition"]["method"], METHOD_RULES)

    def test_a_single_task_carries_its_values(self):
        one = reply(("go to 2,1", "go_to_location", {"x": 2, "y": 1}))
        result = plan_compound("go to 2,1", self.db, llm_planner(), provider=scripted(one))
        self.assertIsInstance(result, Plan)
        self.assertEqual(result.meta["parameters"], {"x": 2, "y": 1})


class SurfaceTests(GraphCase):
    def setUp(self):
        super().setUp()
        for key, value in ENV.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        for key in ("LLM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(key, None)

    def test_the_web_api_answers_compound(self):
        service = PlannerService(self.db, llm_planner(), provider=scripted(GO_THEN_CHARGE))
        payload = service.plan("go to 2,1 and then charge the robot")
        self.assertEqual(payload["kind"], "compound")
        self.assertEqual(len(payload["subtasks"]), 2)
        self.assertEqual(payload["subtasks"][1]["result"]["kind"], "plan")
        json.dumps(payload)  # the whole thing must serialize

    def test_the_web_api_still_answers_a_single_plan(self):
        service = PlannerService(self.db, load_planner_config(ENV, use_llm=False))
        self.assertEqual(service.plan("go to location")["kind"], "plan")

    def test_the_cli_json(self):
        out = io.StringIO()
        code = run("go to location then dock at charger", output_format="json", use_llm=False,
                   stdout=out, db=self.db)
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(set(payload), {"command", "executable", "decomposition", "subtasks", "steps", "skipped"})

    def test_the_cli_table_and_exit_code_on_clarification(self):
        out = io.StringIO()
        code = run("go to location then reticulate the splines", use_llm=False, stdout=out, db=self.db)
        self.assertEqual(code, 3)
        self.assertIn("Compound plan", out.getvalue())
        self.assertIn("needs clarifying", out.getvalue())


if __name__ == "__main__":
    unittest.main()
