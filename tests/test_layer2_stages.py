"""Layer 2, stage by stage: retrieval, traversal, ordering, composition."""

from __future__ import annotations

import io
import json
import unittest

from layer2_planning import load_planner_config, plan_command, print_clarification, print_plan
from layer2_planning.cli import EXIT_CLARIFICATION, build_parser, run
from layer2_planning.compose import (
    METHOD_LLM,
    METHOD_TEMPLATE,
    GroundingError,
    check_grounding,
    compose,
    humanize,
    parse_steps,
    template_descriptions,
)
from layer2_planning.order import CyclicPlan, SOURCE_CHAIN, SOURCE_EDGE, constraints_for, order_plan
from layer2_planning.plan import Clarification, Plan
from layer2_planning.report import dump_json
from layer2_planning.retrieve import (
    Candidate,
    LexicalRetriever,
    VectorRetriever,
    load_skills,
    resolve_goal,
    tokenize,
)
from layer2_planning.traverse import IncompletePlanGraph, Subgraph, retrieve_subgraph
from llm_disambiguator.provider import ServiceError

from . import fake_llm
from .corpus_fixture import ENV, book_named, corpus_db


def planner(**overrides):
    return load_planner_config({**ENV, **overrides}, use_llm=False)


def chai_subgraph() -> Subgraph:
    db, config, _books = corpus_db()
    schema = config.schema
    skills = load_skills(db, schema)
    skill = [s for s in skills if s["skill_scope"] == "make_masala_chai"][0]
    return retrieve_subgraph(db, schema, skill["_key"], skill["name"], skill["skill_scope"])


# --------------------------------------------------------------------------
# Stage 1
# --------------------------------------------------------------------------


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.db, self.config, _ = corpus_db()
        self.schema = self.config.schema
        self.retriever = LexicalRetriever(load_skills(self.db, self.schema))

    def test_idf_discriminates_between_skills_that_all_say_make(self):
        scores = {c.skill: c.score for c in self.retriever.score("make me a chai")}
        self.assertEqual(max(scores, key=scores.get), "make_masala_chai")
        self.assertGreater(scores["make_masala_chai"], scores["make_bed"] * 1.5)

    def test_fuzzy_matching_reaches_a_longer_word(self):
        # "plants" must find "houseplants"; "shirt" must find "tshirt".
        self.assertEqual(self.retriever.score("water the plants")[0].skill, "water_houseplants")
        self.assertEqual(self.retriever.score("fold a shirt")[0].skill, "fold_tshirt")

    def test_scores_are_bounded(self):
        for candidate in self.retriever.score("make me a chai"):
            self.assertGreaterEqual(candidate.score, 0.0)
            self.assertLessEqual(candidate.score, 1.0)

    def test_results_are_sorted_and_deterministic(self):
        first = [(c.skill, c.score) for c in self.retriever.score("cook a burger")]
        second = [(c.skill, c.score) for c in self.retriever.score("cook a burger")]
        self.assertEqual(first, second)
        self.assertEqual(first, sorted(first, key=lambda p: (-p[1], p[0])))

    def test_an_empty_command_scores_nothing(self):
        self.assertEqual(self.retriever.score("   "), [])

    def test_stopwords_are_ignored(self):
        self.assertEqual(tokenize("I would like you to please make a chai"), ["make", "chai"])
        self.assertEqual(tokenize("please could you"), [])

    def test_below_threshold_returns_candidates_not_a_goal(self):
        result = resolve_goal("splines", self.db, self.schema, 0.9, 3, 0.05, self.retriever)
        self.assertFalse(result.resolved)
        self.assertTrue(result.candidates)

    def test_near_tie_is_flagged_ambiguous(self):
        result = resolve_goal("make me a chai", self.db, self.schema, 0.1, 3, 0.99, self.retriever)
        self.assertTrue(result.ambiguous)
        self.assertFalse(result.resolved)

    def test_top_k_bounds_the_candidate_list(self):
        result = resolve_goal("splines", self.db, self.schema, 0.9, 2, 0.05, self.retriever)
        self.assertEqual(len(result.candidates), 2)

    def test_an_empty_graph_says_so(self):
        from .fake_writable_arango import make_db

        result = resolve_goal("anything", make_db(), self.schema, 0.3, 3, 0.05)
        self.assertFalse(result.resolved)
        self.assertIn("no skills", result.reason)

    def test_the_method_used_is_recorded(self):
        result = resolve_goal("make me a chai", self.db, self.schema, 0.1, 3, 0.05, self.retriever)
        self.assertEqual(result.method, "lexical")

    def test_the_vector_retriever_reports_itself_unavailable(self):
        # Station 5 has not built the index, so the planner must fall back.
        self.assertFalse(VectorRetriever(self.db, self.schema).available)


# --------------------------------------------------------------------------
# Stage 2
# --------------------------------------------------------------------------


class TraversalTests(unittest.TestCase):
    def setUp(self):
        self.subgraph = chai_subgraph()

    def test_it_gathers_the_whole_subgraph(self):
        book = book_named("make_masala_chai")
        self.assertEqual(set(self.subgraph.primitives), set(book.primitives))
        self.assertTrue(self.subgraph.states)
        self.assertTrue(self.subgraph.objects)

    def test_it_gathers_every_edge_type(self):
        self.assertTrue(self.subgraph.requires)
        self.assertTrue(self.subgraph.produces)
        self.assertTrue(self.subgraph.uses)
        self.assertTrue(self.subgraph.precedes)

    def test_it_is_bounded_to_the_scope(self):
        self.assertEqual(self.subgraph.skill_scope, "make_masala_chai")
        self.assertNotIn("flip_patty", self.subgraph.primitives)

    def test_no_orphan_preconditions_on_a_healthy_graph(self):
        self.assertEqual(self.subgraph.orphan_preconditions(), [])

    def test_a_skill_with_no_primitives_is_an_error_not_a_fabrication(self):
        from .fake_writable_arango import make_db

        db, config, _ = corpus_db()
        with self.assertRaises(IncompletePlanGraph):
            retrieve_subgraph(make_db(), config.schema, "nope", "nope", "nope")

    def test_orphan_preconditions_are_detected(self):
        subgraph = Subgraph(skill="s", skill_scope="s")
        subgraph.requires = {"a": ["needed"]}
        subgraph.produces = {"a": ["other"]}
        self.assertEqual(subgraph.orphan_preconditions(), [("a", "needed")])


# --------------------------------------------------------------------------
# Stage 3
# --------------------------------------------------------------------------


class OrderingTests(unittest.TestCase):
    def test_constraints_come_from_both_sources(self):
        constraints = constraints_for(chai_subgraph())
        self.assertTrue(any(SOURCE_EDGE in s for s in constraints.values()))
        self.assertTrue(any(SOURCE_CHAIN in s for s in constraints.values()))

    def test_the_two_sources_agree_on_a_bridge_built_graph(self):
        ordering = order_plan(chai_subgraph())
        self.assertEqual(ordering.agreed, len(ordering.constraints))

    def test_ordering_works_from_the_state_chain_alone(self):
        """A PlanGraph missing its precedes edges must still order correctly."""
        subgraph = chai_subgraph()
        with_edges = order_plan(subgraph).steps
        subgraph.precedes = []
        self.assertEqual(order_plan(subgraph).steps, with_edges)

    def test_ties_break_by_name(self):
        subgraph = Subgraph(skill="s", skill_scope="s", primitives=["b", "a"])
        subgraph.produces = {"a": ["x"], "b": ["y"]}
        self.assertEqual(order_plan(subgraph).steps, ["a", "b"])

    def test_unconstrained_primitives_still_appear(self):
        subgraph = Subgraph(skill="s", skill_scope="s", primitives=["a", "b", "loner"])
        subgraph.produces = {"a": ["x"]}
        subgraph.requires = {"b": ["x"]}
        steps = order_plan(subgraph).steps
        self.assertIn("loner", steps)
        self.assertLess(steps.index("a"), steps.index("b"))

    def test_a_cycle_is_a_hard_error_naming_the_edges(self):
        subgraph = Subgraph(skill="s", skill_scope="s", primitives=["a", "b"])
        subgraph.precedes = [("a", "b"), ("b", "a")]
        with self.assertRaises(CyclicPlan) as caught:
            order_plan(subgraph)
        self.assertTrue(caught.exception.edges)

    def test_ordering_is_deterministic(self):
        subgraph = chai_subgraph()
        self.assertEqual(order_plan(subgraph).steps, order_plan(subgraph).steps)


# --------------------------------------------------------------------------
# Stage 4
# --------------------------------------------------------------------------


class CompositionTests(unittest.TestCase):
    """FR-5, FR-6 — the LLM phrases; the graph orders."""

    def setUp(self):
        self.subgraph = chai_subgraph()
        self.steps = order_plan(self.subgraph).steps

    def _provider(self, responder):
        return fake_llm.FakeProvider(fake_llm.config(), responder)

    def _good(self, _system, user):
        actions = [
            line.split("action: ", 1)[1].strip()
            for line in user.splitlines()
            if "action: " in line
        ]
        return json.dumps(
            {"steps": [{"action": a, "description": f"Do {a}."} for a in actions]}
        )

    def test_a_well_formed_reply_is_used(self):
        result = compose(self.steps, self.subgraph, self._provider(self._good))
        self.assertEqual(result.method, METHOD_LLM)
        self.assertEqual(result.descriptions["place_pan"], "Do place_pan.")

    def test_no_provider_means_templates(self):
        result = compose(self.steps, self.subgraph, None)
        self.assertEqual(result.method, METHOD_TEMPLATE)
        self.assertTrue(all(result.descriptions.values()))

    def test_reordering_is_rejected(self):
        from layer2_planning.compose import build_user_message

        user = build_user_message(self.steps, self.subgraph)
        payload = json.loads(self._good("", user))
        payload["steps"].reverse()
        with self.assertRaises(GroundingError):
            check_grounding(parse_steps(json.dumps(payload)), self.steps)

    def test_a_dropped_step_is_rejected(self):
        with self.assertRaises(GroundingError):
            check_grounding([{"action": self.steps[0], "description": "x"}], self.steps)

    def test_an_invented_step_is_rejected(self):
        items = [{"action": s, "description": "x"} for s in self.steps]
        items.append({"action": "polish_the_pan", "description": "invented"})
        with self.assertRaises(GroundingError):
            check_grounding(items, self.steps)

    def test_a_renamed_step_is_rejected(self):
        items = [{"action": s, "description": "x"} for s in self.steps]
        items[2]["action"] = "something_else"
        with self.assertRaises(GroundingError):
            check_grounding(items, self.steps)

    def test_a_bad_reply_is_reprompted_once_then_falls_back(self):
        provider = self._provider(fake_llm.malformed("not json at all"))
        result = compose(self.steps, self.subgraph, provider)
        self.assertEqual(provider.requests_made, 2)
        self.assertEqual(result.method, METHOD_TEMPLATE)
        self.assertTrue(result.reprompted)
        self.assertIn("grounding guard", result.fallback_reason)

    def test_a_successful_reprompt_is_used(self):
        provider = self._provider(fake_llm.malformed_once(self._good))
        result = compose(self.steps, self.subgraph, provider)
        self.assertEqual(result.method, METHOD_LLM)
        self.assertTrue(result.reprompted)

    def test_a_service_error_falls_back_without_raising(self):
        result = compose(self.steps, self.subgraph, self._provider(fake_llm.failing()))
        self.assertEqual(result.method, METHOD_TEMPLATE)
        self.assertIn("unavailable", result.fallback_reason)

    def test_the_fallback_preserves_the_order(self):
        result = compose(self.steps, self.subgraph, self._provider(fake_llm.failing()))
        self.assertEqual(list(result.descriptions), self.steps)

    def test_missing_description_falls_back_per_step(self):
        items = [{"action": s} for s in self.steps]
        descriptions = check_grounding(items, self.steps)
        self.assertTrue(all(descriptions.values()))

    def test_humanize(self):
        self.assertEqual(humanize("add_tea_leaves"), "Add tea leaves.")

    def test_templates_mention_the_objects(self):
        descriptions = template_descriptions(["strain"], self.subgraph)
        self.assertIn("using", descriptions["strain"])

    def test_templates_never_double_the_article(self):
        subgraph = Subgraph(skill="s", skill_scope="s", primitives=["clear_bed"])
        subgraph.uses = {"clear_bed": ["the_bed", "pillow"]}
        sentence = template_descriptions(["clear_bed"], subgraph)["clear_bed"]
        self.assertNotIn("the the", sentence)
        self.assertIn("the bed", sentence)
        self.assertIn("the pillow", sentence)

    def test_the_prompt_never_asks_for_ordering(self):
        from layer2_planning.compose import SYSTEM_PROMPT

        self.assertIn("not yours to change", SYSTEM_PROMPT)
        self.assertIn("same order", SYSTEM_PROMPT)


# --------------------------------------------------------------------------
# Pipeline and CLI
# --------------------------------------------------------------------------


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.db, _config, _ = corpus_db()

    def test_the_llm_never_changes_the_order(self):
        """The property the whole design exists to guarantee."""
        without = plan_command("make me a masala chai", self.db, planner())

        def good(_system, user):
            actions = [
                line.split("action: ", 1)[1].strip()
                for line in user.splitlines()
                if "action: " in line
            ]
            return json.dumps(
                {"steps": [{"action": a, "description": "Reworded."} for a in actions]}
            )

        config = load_planner_config({**ENV, "LLM_API_KEY": "k", "LLM_CACHE": "0"}, use_llm=True)
        with_llm = plan_command(
            "make me a masala chai",
            self.db,
            config,
            provider=fake_llm.FakeProvider(fake_llm.config(), good),
        )
        self.assertEqual(without.actions, with_llm.actions)
        self.assertEqual(with_llm.meta["composer"], "llm")
        self.assertEqual(with_llm.steps[0].description, "Reworded.")

    def test_a_hostile_model_cannot_reorder_the_plan(self):
        def hostile(_system, user):
            actions = [
                line.split("action: ", 1)[1].strip()
                for line in user.splitlines()
                if "action: " in line
            ]
            actions.reverse()
            return json.dumps(
                {"steps": [{"action": a, "description": "Wrong."} for a in actions]}
            )

        config = load_planner_config({**ENV, "LLM_API_KEY": "k", "LLM_CACHE": "0"}, use_llm=True)
        plan = plan_command(
            "make me a masala chai",
            self.db,
            config,
            provider=fake_llm.FakeProvider(fake_llm.config(), hostile),
        )
        self.assertEqual(plan.actions[0], "place_pan")
        self.assertEqual(plan.meta["composer"], "template")

    def test_generated_at_is_recorded(self):
        plan = plan_command("cook a burger", self.db, planner(), now="2026-01-01T00:00:00+00:00")
        self.assertEqual(plan.meta["generated_at"], "2026-01-01T00:00:00+00:00")

    def test_the_pipeline_never_writes(self):
        from .fake_writable_arango import FOREIGN_COLLECTIONS

        before = {
            name: len(self.db.collection(name).documents)
            for name in self.db._collections
        }
        plan_command("make the bed", self.db, planner())
        after = {
            name: len(self.db.collection(name).documents)
            for name in self.db._collections
        }
        self.assertEqual(before, after)
        for name in FOREIGN_COLLECTIONS:
            self.assertEqual(len(self.db.collection(name).documents), 1)


class CliTests(unittest.TestCase):
    def setUp(self):
        self.db, _config, _ = corpus_db()
        import os

        for key, value in ENV.items():
            os.environ[key] = value
            self.addCleanup(os.environ.pop, key, None)
        for key in ("LLM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
            os.environ.pop(key, None)

    def test_command_words_are_joined(self):
        self.assertEqual(build_parser().parse_args(["make", "a", "chai"]).command, ["make", "a", "chai"])

    def test_a_plan_exits_zero(self):
        out = io.StringIO()
        code = run("make me a masala chai", use_llm=False, stdout=out, db=self.db)
        self.assertEqual(code, 0)
        self.assertIn("place_pan", out.getvalue())

    def test_a_clarification_exits_three(self):
        out = io.StringIO()
        code = run("reticulate splines", use_llm=False, stdout=out, db=self.db)
        self.assertEqual(code, EXIT_CLARIFICATION)
        self.assertIn("Clarification needed", out.getvalue())

    def test_json_output_is_the_contract(self):
        out = io.StringIO()
        run("cook a burger", output_format="json", use_llm=False, stdout=out, db=self.db)
        payload = json.loads(out.getvalue())
        self.assertEqual(set(payload), {"goal", "command", "steps", "meta"})
        self.assertEqual(set(payload["steps"][0]), {"order", "action", "description", "requires", "produces", "uses"})

    def test_threshold_override(self):
        out = io.StringIO()
        code = run("make me a masala chai", use_llm=False, threshold=0.99, stdout=out, db=self.db)
        self.assertEqual(code, EXIT_CLARIFICATION)

    def test_report_renders_a_plan(self):
        out = io.StringIO()
        plan = plan_command("water the houseplants", self.db, planner())
        print_plan(planner(), plan, out)
        self.assertIn("water_plant", out.getvalue())

    def test_report_renders_a_clarification(self):
        out = io.StringIO()
        result = plan_command("xyzzy", self.db, planner())
        print_clarification(result, out)
        self.assertIn("closest skills", out.getvalue())

    def test_ascii_fallback(self):
        class AsciiStream(io.StringIO):
            encoding = "ascii"

        out = AsciiStream()
        print_plan(planner(), plan_command("make the bed", self.db, planner()), out)
        out.getvalue().encode("ascii")

    def test_json_dump_round_trips(self):
        out = io.StringIO()
        dump_json(plan_command("fold my t-shirt", self.db, planner()), out)
        self.assertEqual(json.loads(out.getvalue())["goal"], "fold_tshirt")


if __name__ == "__main__":
    unittest.main()
