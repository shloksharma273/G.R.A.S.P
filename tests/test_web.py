"""The planning UI: the service, the routes, and the read-only guarantee."""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from grasp_web import PlannerService, make_server
from grasp_web.cli import serving_env
from layer2_planning import load_planner_config

from .corpus_fixture import ENV, corpus_db


def service(**overrides) -> PlannerService:
    db, _writer, _books = corpus_db()
    return PlannerService(db, load_planner_config({**ENV, **overrides}, use_llm=False))


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.service = service()

    def test_health_reports_what_it_is_connected_to(self):
        health = self.service.health()
        self.assertEqual(health["database"], "test_shlok")
        self.assertEqual(health["graph"], "roboticsPlanner_PlanGraph")
        self.assertEqual(health["skills"], 6)

    def test_health_says_when_there_is_no_vector_index(self):
        health = self.service.health()
        self.assertFalse(health["vector_index"])
        self.assertEqual(health["retrieval"], "lexical")

    def test_health_reports_templated_phrasing_without_a_key(self):
        self.assertEqual(self.service.health()["phrasing"], "templates")
        self.assertIsNone(self.service.health()["model"])

    def test_skills_lists_every_skill_with_step_counts(self):
        skills = self.service.skills()
        self.assertEqual(len(skills), 6)
        names = {s["name"] for s in skills}
        self.assertIn("make_masala_chai", names)
        chai = next(s for s in skills if s["name"] == "make_masala_chai")
        self.assertEqual(chai["steps"], 11)
        self.assertEqual(chai["scope"], "make_masala_chai")

    def test_skills_are_sorted(self):
        names = [s["name"] for s in self.service.skills()]
        self.assertEqual(names, sorted(names))

    def test_a_command_returns_a_plan(self):
        payload = self.service.plan("make me a masala chai")
        self.assertEqual(payload["kind"], "plan")
        self.assertEqual(payload["goal"], "make_masala_chai")
        self.assertEqual(payload["steps"][0]["action"], "place_pan")

    def test_the_plan_carries_the_contract_fields(self):
        step = self.service.plan("cook a burger")["steps"][0]
        self.assertEqual(
            set(step), {"order", "action", "description", "requires", "produces", "uses", "interface"}
        )

    def test_an_unresolvable_command_clarifies(self):
        payload = self.service.plan("reticulate the splines")
        self.assertEqual(payload["kind"], "clarification")
        self.assertTrue(payload["reason"])
        self.assertTrue(payload["candidates"])

    def test_an_empty_command_is_refused_without_planning(self):
        self.assertEqual(self.service.plan("   ")["kind"], "error")

    def test_the_llm_toggle_cannot_turn_on_without_a_key(self):
        payload = self.service.plan("make the bed", use_llm=True)
        self.assertEqual(payload["meta"]["composer"], "template")

    def test_the_service_never_writes(self):
        db, _writer, _books = corpus_db()
        before = {name: len(c.documents) for name, c in db._collections.items()}
        self.service.plan("make me a masala chai")
        self.service.skills()
        after = {name: len(c.documents) for name, c in db._collections.items()}
        self.assertEqual(before, after)


class RouteTests(unittest.TestCase):
    """A real server on a real socket — the routing is the thing being tested."""

    @classmethod
    def setUpClass(cls):
        cls.server = make_server(service(), host="127.0.0.1", port=0, quiet=True)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path: str):
        with urllib.request.urlopen(self.url(path), timeout=10) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")

    def post(self, path: str, payload, raw: bytes | None = None):
        body = raw if raw is not None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.url(path), data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    # --- GET ---------------------------------------------------------------

    def test_root_serves_the_page(self):
        status, body, content_type = self.get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"G.R.A.S.P", body)

    def test_static_css_and_js(self):
        for path, expected in (("/static/app.css", "text/css"), ("/static/app.js", "javascript")):
            with self.subTest(path=path):
                status, body, content_type = self.get(path)
                self.assertEqual(status, 200)
                self.assertIn(expected, content_type)
                self.assertTrue(body)

    def test_health_route(self):
        status, body, _ = self.get("/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["skills"], 6)

    def test_skills_route(self):
        status, body, _ = self.get("/api/skills")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["skills"]), 6)

    def test_unknown_route_is_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/nope")
        self.assertEqual(caught.exception.code, 404)

    def test_a_path_traversal_is_refused(self):
        """The static handler resolves inside its own directory, and only there."""
        for attempt in ("/static/../../.env", "/static/../server.py", "/static/..%2f.env"):
            with self.subTest(attempt=attempt):
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    self.get(attempt)
                self.assertEqual(caught.exception.code, 404)

    def test_nosniff_is_set(self):
        with urllib.request.urlopen(self.url("/"), timeout=10) as response:
            self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")

    # --- POST --------------------------------------------------------------

    def test_plan_route_returns_a_plan(self):
        status, payload = self.post("/api/plan", {"command": "make me a masala chai", "use_llm": False})
        self.assertEqual(status, 200)
        self.assertEqual(payload["kind"], "plan")
        self.assertEqual(len(payload["steps"]), 11)

    def test_plan_route_clarifies(self):
        _status, payload = self.post("/api/plan", {"command": "xyzzy", "use_llm": False})
        self.assertEqual(payload["kind"], "clarification")

    def test_malformed_json_is_a_400_not_a_crash(self):
        status, payload = self.post("/api/plan", None, raw=b"{not json")
        self.assertEqual(status, 400)
        self.assertIn("invalid JSON", payload["message"])

    def test_a_non_object_body_is_refused(self):
        status, _payload = self.post("/api/plan", ["a", "list"])
        self.assertEqual(status, 400)

    def test_an_oversized_body_is_refused(self):
        status, _payload = self.post("/api/plan", {"command": "x" * 200_000})
        self.assertEqual(status, 413)

    def test_posting_elsewhere_is_404(self):
        status, _payload = self.post("/api/skills", {"command": "hi"})
        self.assertEqual(status, 404)

    def test_the_server_survives_a_bad_request_and_keeps_serving(self):
        self.post("/api/plan", None, raw=b"garbage")
        status, payload = self.post("/api/plan", {"command": "cook a burger", "use_llm": False})
        self.assertEqual(status, 200)
        self.assertEqual(payload["kind"], "plan")


class PageTests(unittest.TestCase):
    """The page is served as-is, so its contents are worth asserting."""

    def setUp(self):
        self.static = Path("grasp_web/static")

    def test_the_palette_is_arangos(self):
        css = (self.static / "app.css").read_text(encoding="utf-8")
        for token in ("#044926", "#b9ff38", "#befe99", "#151d25"):
            self.assertIn(token, css, f"{token} missing from the palette")

    def test_the_page_loads_its_own_assets_only(self):
        """No CDN, no external font, no third-party script.

        The SVG namespace URI is an XML identifier, never a fetch, so it is the
        one URL allowed to appear.
        """
        import re

        html = (self.static / "index.html").read_text(encoding="utf-8")
        urls = re.findall(r"https?://[^\s\"\'<>)]+", html)
        self.assertEqual(
            [u for u in urls if u != "http://www.w3.org/2000/svg"], [], "external asset found"
        )

    def test_the_stylesheet_pulls_nothing_external(self):
        import re

        css = (self.static / "app.css").read_text(encoding="utf-8")
        self.assertEqual(re.findall(r"@import|url\(\s*[\"\']?https?:", css), [])

    def test_user_content_is_escaped(self):
        """Skill names and model-written wording are never trusted as markup."""
        js = (self.static / "app.js").read_text(encoding="utf-8")
        self.assertIn("const esc =", js)
        self.assertIn("&lt;", js)

    def test_the_retrieval_pill_is_labelled_graphical(self):
        js = (self.static / "app.js").read_text(encoding="utf-8")
        self.assertIn('lexical: "graphical"', js)

    def test_the_api_still_reports_the_real_retrieval_method(self):
        """The relabel is presentational; what a plan claims stays accurate."""
        self.assertEqual(service().health()["retrieval"], "lexical")

    def test_the_page_explains_where_the_order_comes_from(self):
        html = (self.static / "index.html").read_text(encoding="utf-8")
        self.assertIn("precondition graph", html)


class ProjectFlagTests(unittest.TestCase):
    """--project points the server at another PlanGraph without editing the env."""

    BASE = {"PROJECT_NAME": "kitchen", "ARANGO_DB": "test_shlok"}

    def test_no_flag_leaves_the_environment_alone(self):
        self.assertIs(serving_env(None, self.BASE), self.BASE)

    def test_the_project_becomes_the_graph(self):
        env = serving_env("px4Planner", self.BASE)
        self.assertEqual(env["PROJECT_NAME"], "px4Planner")
        self.assertEqual(env["ARANGO_DB"], "test_shlok")

    def test_it_overrides_an_explicit_prefix(self):
        """Otherwise the flag is silently ignored wherever PLANGRAPH_PREFIX is set."""
        env = serving_env("px4Planner", {**self.BASE, "PLANGRAPH_PREFIX": "kitchen"})
        self.assertEqual(env["PLANGRAPH_PREFIX"], "px4Planner")

    def test_the_prefix_reaches_the_planner_config(self):
        config = load_planner_config(serving_env("px4Planner", {**ENV}), use_llm=False)
        self.assertEqual(config.schema.graph_name, "px4Planner_PlanGraph")


if __name__ == "__main__":
    unittest.main()
