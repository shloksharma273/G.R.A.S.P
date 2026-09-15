"""The generator page: the job model, its routes, and the page itself."""

from __future__ import annotations

import json
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from grasp_web import GeneratorService, PlannerService, make_server
from grasp_web.generate import MAX_JOBS, STATE_DONE, STATE_ERROR, STATE_RUNNING
from layer2_planning import load_planner_config

from . import fake_llm
from .corpus_fixture import ENV as PLANNER_ENV, corpus_db
from .test_rulebook_generator import CHAI, responder

GEN_ENV = {
    "LLM_API_KEY": "test-key-not-real",
    "LLM_MODEL": "test/model-1",
    "RULEBOOK_CACHE": "0",
    "RULEBOOK_CHECK_GROUNDING": "0",
    "RULEBOOK_OUTPUT_DIR": "generated",
}

TRANSCRIPT = "first boil the water in the kettle then pour the tea into the cup"


class ScriptedGenerator(GeneratorService):
    """A GeneratorService whose config and model are fixed, so no key is needed."""

    def __init__(self, respond=None, env=None):
        super().__init__(provider=fake_llm.FakeProvider(fake_llm.config(), respond or responder(CHAI)))
        self._env = {**GEN_ENV, **(env or {})}

    def config(self):
        from rulebook_generator.config import load_generator_config

        return load_generator_config(self._env)


def wait(service: GeneratorService, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = service.status(job_id)
        if job and job["state"] != STATE_RUNNING:
            return job
        time.sleep(0.02)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


class HealthTests(unittest.TestCase):
    def test_reports_what_it_is_configured_to_do(self):
        health = ScriptedGenerator().health()
        self.assertTrue(health["available"])
        self.assertEqual(health["model"], "test/model-1")
        self.assertEqual(health["strictness"], "normal")
        self.assertEqual(health["writes"], ["accept"])

    def test_lenient_strictness_widens_what_is_written(self):
        health = ScriptedGenerator(env={"RULEBOOK_STRICTNESS": "lenient"}).health()
        self.assertEqual(health["writes"], ["accept", "flag_for_review"])

    def test_without_a_key_it_says_so_rather_than_failing(self):
        """The planning UI must still serve when the generator cannot run."""
        service = ScriptedGenerator()
        service._env = {}
        health = service.health()
        self.assertFalse(health["available"])
        self.assertIn("API key", health["reason"])
        self.assertTrue(health["hint"])

    def test_it_reports_whether_captions_can_be_fetched(self):
        self.assertIn("youtube", ScriptedGenerator().health())


class JobTests(unittest.TestCase):
    """Generation is slow, so it runs as a job the page can poll."""

    def test_a_pasted_transcript_generates(self):
        service = ScriptedGenerator()
        started = service.start(transcript_text=TRANSCRIPT)
        self.assertIn("id", started)

        job = wait(service, started["id"])
        self.assertEqual(job["state"], STATE_DONE)
        self.assertEqual(job["result"]["verdict"], "accept")
        self.assertEqual(job["result"]["extraction"]["intermediate"]["skill"], "make_tea")

    def test_the_result_carries_the_markdown_and_a_filename(self):
        service = ScriptedGenerator()
        job = wait(service, service.start(transcript_text=TRANSCRIPT)["id"])
        self.assertIn("**make_tea**", job["result"]["markdown"])
        self.assertEqual(job["result"]["filename"], "rulebook_make_tea.md")

    def test_the_result_carries_the_implied_plan(self):
        service = ScriptedGenerator()
        job = wait(service, service.start(transcript_text=TRANSCRIPT)["id"])
        self.assertEqual(job["result"]["validation"]["plan"], ["boil_water", "pour_tea"])

    def test_stages_come_from_the_pipeline(self):
        """The page names real boundaries, not an invented progress bar.

        Observed through `status()`, the way the page sees it - wrapping the
        private runner would break the moment its signature legitimately changed,
        which is exactly what happened the first time this was written.
        """
        from rulebook_generator.pipeline import STAGE_EXTRACTING, STAGE_RENDERING, STAGE_VALIDATING

        service = ScriptedGenerator()
        job_id = service.start(transcript_text=TRANSCRIPT)["id"]

        seen = []
        deadline = time.time() + 10
        while time.time() < deadline:
            job = service.status(job_id)
            if job["stage"] not in seen:
                seen.append(job["stage"])
            if job["state"] != STATE_RUNNING:
                break
            time.sleep(0.005)

        # Whatever the poller caught, the stage must have advanced past the one
        # this module owns into the pipeline's own.
        self.assertIn(
            seen[-1],
            {STAGE_EXTRACTING, STAGE_RENDERING, STAGE_VALIDATING},
            f"stage never reached the pipeline: {seen}",
        )

    def test_a_non_procedural_transcript_is_rejected_not_crashed(self):
        service = ScriptedGenerator(
            respond=responder({"not_procedural": True, "reason": "a music video"})
        )
        job = wait(service, service.start(transcript_text=TRANSCRIPT)["id"])
        self.assertEqual(job["state"], STATE_DONE)
        self.assertEqual(job["result"]["verdict"], "reject")
        self.assertIn("music video", job["result"]["reason"])

    def test_an_empty_transcript_is_an_error_with_a_hint(self):
        service = ScriptedGenerator()
        job = wait(service, service.start(transcript_text="[Music]\n[Applause]")["id"])
        self.assertEqual(job["state"], STATE_ERROR)
        self.assertIn("no usable words", job["error"])

    def test_a_bad_link_fails_the_job_not_the_server(self):
        service = ScriptedGenerator()
        job = wait(service, service.start(url="https://example.com/not-a-video")["id"])
        self.assertEqual(job["state"], STATE_ERROR)
        self.assertTrue(job["error"])

    def test_starting_with_nothing_is_refused_up_front(self):
        self.assertIn("error", ScriptedGenerator().start())

    def test_without_a_key_starting_is_refused_with_the_reason(self):
        service = ScriptedGenerator()
        service._env = {}
        started = service.start(transcript_text=TRANSCRIPT)
        self.assertIn("error", started)
        self.assertNotIn("id", started)

    def test_an_unknown_job_is_none(self):
        self.assertIsNone(ScriptedGenerator().status("nope"))

    def test_jobs_are_bounded(self):
        """An unbounded dict of past generations is a slow leak."""
        service = ScriptedGenerator()
        ids = [service.start(transcript_text=f"{TRANSCRIPT} {n}")["id"] for n in range(MAX_JOBS + 4)]
        for job_id in ids[-MAX_JOBS:]:
            wait(service, job_id)
        self.assertIsNone(service.status(ids[0]))
        self.assertIsNotNone(service.status(ids[-1]))


class LibraryTests(unittest.TestCase):
    def test_it_lists_rulebooks_on_disk(self):
        rulebooks = ScriptedGenerator().rulebooks()
        if not rulebooks:
            self.skipTest("no generated rulebooks on disk")
        for book in rulebooks:
            self.assertTrue(book["name"])
            self.assertTrue(book["markdown"].startswith("#"))

    def test_a_missing_directory_is_empty_not_an_error(self):
        service = ScriptedGenerator(env={"RULEBOOK_OUTPUT_DIR": "no_such_directory_here"})
        self.assertEqual(service.rulebooks(), [])


class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db, _writer, _books = corpus_db()
        planner = PlannerService(db, load_planner_config(PLANNER_ENV, use_llm=False))
        cls.generator = ScriptedGenerator()
        cls.server = make_server(
            planner, host="127.0.0.1", port=0, quiet=True, generator=cls.generator
        )
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def get(self, path):
        with urllib.request.urlopen(self.url(path), timeout=10) as response:
            return response.status, response.read(), response.headers.get("Content-Type", "")

    def post(self, path, payload):
        request = urllib.request.Request(
            self.url(path),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_the_generate_page_is_served(self):
        status, body, content_type = self.get("/generate")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"rulebook", body.lower())

    def test_its_script_is_served(self):
        status, body, content_type = self.get("/static/generate.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", content_type)
        self.assertTrue(body)

    def test_generator_health_route(self):
        status, body, _ = self.get("/api/generate/health")
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["available"])

    def test_library_route(self):
        status, body, _ = self.get("/api/generate/library")
        self.assertEqual(status, 200)
        self.assertIn("rulebooks", json.loads(body))

    def test_a_generation_runs_end_to_end_over_http(self):
        status, started = self.post("/api/generate", {"transcript": TRANSCRIPT})
        self.assertEqual(status, 200)
        job = wait(self.generator, started["id"])
        self.assertEqual(job["state"], STATE_DONE)

        _status, polled, _ = self.get(f"/api/generate/{started['id']}")
        payload = json.loads(polled)
        self.assertEqual(payload["result"]["verdict"], "accept")

    def test_starting_with_nothing_is_a_400(self):
        status, payload = self.post("/api/generate", {})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_an_unknown_job_is_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/generate/deadbeef")
        self.assertEqual(caught.exception.code, 404)

    def test_the_planning_routes_still_work(self):
        status, payload = self.post("/api/plan", {"command": "make me a masala chai", "use_llm": False})
        self.assertEqual(status, 200)
        self.assertEqual(payload["kind"], "plan")


class PageTests(unittest.TestCase):
    def setUp(self):
        self.static = Path("grasp_web/static")

    def test_both_pages_carry_the_nav(self):
        for name in ("index.html", "generate.html"):
            with self.subTest(name=name):
                html = (self.static / name).read_text(encoding="utf-8")
                self.assertIn('href="/generate"', html)
                self.assertIn('href="/"', html)
                self.assertIn('aria-current="page"', html)

    def test_the_generator_page_loads_only_its_own_assets(self):
        """No CDN, no external font, no third-party script.

        Only `src` and `href` load something - a URL inside placeholder text is
        an example for the reader, not a request.
        """
        import re

        html = (self.static / "generate.html").read_text(encoding="utf-8")
        loaded = re.findall(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", html)
        external = [u for u in loaded if u.startswith(("http://", "https://"))]
        self.assertEqual(external, [], f"external asset(s): {external}")

    def test_generated_content_is_escaped(self):
        """The rulebook markdown is model-written; it is never trusted markup.

        It reaches the DOM through `highlight()`, which is the one place that
        has to escape before it adds its own spans.
        """
        js = (self.static / "generate.js").read_text(encoding="utf-8")
        self.assertIn("const esc =", js)
        highlight = js[js.index("function highlight("):]
        self.assertIn("esc(markdown)", highlight[: highlight.index("}")])

    def test_the_page_states_why_the_gate_exists(self):
        html = (self.static / "generate.html").read_text(encoding="utf-8")
        self.assertIn("precondition", html)
        self.assertIn("accept", html)


if __name__ == "__main__":
    unittest.main()
