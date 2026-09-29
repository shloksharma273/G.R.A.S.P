"""The browser half of rulebooks -> PlanGraph: split generations and the build job.

Nothing reaches a network: generations run against a scripted model, and builds
against `FakePlatform`, which answers the pipeline's real HTTP requests.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from grasp_web import PlannerService, make_server
from grasp_web.jobs import STATE_DONE, STATE_ERROR, STATE_RUNNING
from grasp_web.kgbuild import KnowledgeBuildService
from layer2_planning import load_planner_config

from .corpus_fixture import ENV as PLANNER_ENV, corpus_db
from .fake_autograph import FakePlatform
from .test_autograph_pipeline import ENV as PIPELINE_ENV, plangraph_ok
from .test_split import responder as split_responder
from .test_web_generate import ScriptedGenerator, wait

TRANSCRIPT = "the openamrobot localises, navigates, docks and undocks " * 20


def wait_run(service: KnowledgeBuildService, job_id: str, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = service.status(job_id)
        if job and job["state"] != STATE_RUNNING:
            return job
        time.sleep(0.02)
    raise AssertionError(f"run {job_id} did not finish within {timeout}s")


def split_generation():
    generator = ScriptedGenerator(respond=split_responder())
    job = generator.start(transcript_text=TRANSCRIPT, split=True)
    return generator, wait(generator, job["id"])


class SplitJobTest(unittest.TestCase):
    def test_a_split_generation_returns_the_reference_and_each_task(self):
        _, done = split_generation()
        self.assertEqual(done["state"], STATE_DONE, done["error"])
        result = done["result"]
        self.assertEqual(result["kind"], "split")
        self.assertEqual(
            [t["filename"] for t in result["tasks"]],
            ["rulebook_go_to_pose.md", "rulebook_dock_at_charger.md", "rulebook_undock_from_charger.md"],
        )
        self.assertEqual(result["accepted"], 3)
        self.assertEqual(result["assumes"]["undock_from_charger"], ["robot_docked"])
        self.assertTrue(all(t["markdown"] for t in result["tasks"]))

    def test_a_finished_generations_rulebooks_can_be_read_back_by_name(self):
        generator, done = split_generation()
        files = generator.rulebook_files(done["id"], ["rulebook_dock_at_charger.md"])
        self.assertEqual(files[0][0], "rulebook_dock_at_charger.md")
        self.assertIn("**dock_at_charger** is a high-level skill", files[0][1])

    def test_a_name_the_generation_did_not_produce_is_refused(self):
        from kg_read_harness.errors import HarnessError

        generator, done = split_generation()
        with self.assertRaises(HarnessError):
            generator.rulebook_files(done["id"], ["rulebook_nope.md"])
        with self.assertRaises(HarnessError):
            generator.rulebook_files("no-such-job", ["rulebook_dock_at_charger.md"])


class LibraryFilesTest(unittest.TestCase):
    def test_only_listed_names_are_read_so_a_path_cannot_escape(self):
        from kg_read_harness.errors import HarnessError

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "rulebook_a.md").write_text("# a\n\n**x** — y.\n")
            generator = ScriptedGenerator(env={"RULEBOOK_OUTPUT_DIR": tmp})
            self.assertEqual(generator.library_files(["rulebook_a.md"])[0][0], "rulebook_a.md")
            with self.assertRaises(HarnessError):
                generator.library_files(["../.env"])


class BuildServiceTest(unittest.TestCase):
    def setUp(self):
        self.generator, done = split_generation()
        self.job_id = done["id"]
        self.fake = FakePlatform()
        self.fake.add_project("robots")
        from autograph_pipeline.client import Platform

        self.service = KnowledgeBuildService(
            job_files=self.generator.rulebook_files,
            library_files=self.generator.library_files,
            platform=Platform("https://platform.example", "robots", "root", "secret", transport=self.fake),
            build_plangraph=plangraph_ok,
            env=PIPELINE_ENV,
            sleep=lambda _s: None,
        )
        self.files = ["rulebook_go_to_pose.md", "rulebook_dock_at_charger.md"]

    def start(self, **overrides):
        payload = {"job": self.job_id, "files": self.files, "project": "robots", "category": "nav"}
        payload.update(overrides)
        return self.service.start(payload)

    def test_a_check_reports_every_stage_and_writes_nothing(self):
        done = wait_run(self.service, self.start()["id"])
        self.assertEqual(done["state"], STATE_DONE, done["error"])
        self.assertTrue(done["result"]["ok"])
        statuses = [row["status"] for row in done["detail"]["stages"]]
        self.assertEqual(statuses[0], "done")
        self.assertTrue(all(s == "planned" for s in statuses[1:]))
        self.assertEqual(self.fake.writes(), [])

    def test_a_build_runs_every_stage_and_uploads_exactly_the_graded_markdown(self):
        done = wait_run(self.service, self.start(write=True)["id"])
        self.assertTrue(done["result"]["ok"], done["result"]["failed"])
        self.assertEqual({row["status"] for row in done["detail"]["stages"]}, {"done"})
        self.assertEqual(sorted(f["name"] for f in self.fake.files), sorted(self.files))
        expected = dict(self.generator.rulebook_files(self.job_id, self.files))
        for f in self.fake.files:
            self.assertEqual(f["size"], len(expected[f["name"]].encode()))
        self.assertTrue(done["detail"]["log"])
        self.assertEqual(done["stage"], "finished")

    def test_a_stopped_build_names_the_stage_and_the_fix(self):
        self.fake.orchestration_failure = "boom"
        done = wait_run(self.service, self.start(write=True)["id"])
        self.assertFalse(done["result"]["ok"])
        self.assertEqual(done["result"]["failed_stage"], "building the knowledge graph")
        self.assertEqual(done["stage"], "stopped at building the knowledge graph")
        failed = [r for r in done["detail"]["stages"] if r["status"] == "failed"]
        self.assertEqual(len(failed), 1)

    def test_bad_names_are_refused_before_anything_starts(self):
        for field, value in (("project", "../x"), ("category", ""), ("project", "9lives")):
            with self.subTest(field=field, value=value):
                self.assertIn("error", self.start(**{field: value}))

    def test_no_rulebooks_is_refused(self):
        self.assertIn("error", self.start(files=[]))

    def test_a_rulebook_the_generation_did_not_make_is_refused(self):
        refused = self.start(files=["rulebook_nope.md"])
        self.assertIn("no rulebook named", refused["error"])

    def test_one_run_per_project_at_a_time(self):
        gate = threading.Event()
        self.service.build_plangraph = lambda project, on_stage: (gate.wait(5), plangraph_ok(project, on_stage))[1]
        first = self.start(write=True)
        second = self.start()
        self.assertIn("already going", second["error"])
        gate.set()
        wait_run(self.service, first["id"])
        self.assertFalse(self.start().get("error"))

    def test_health_names_the_stages(self):
        health = self.service.health()
        self.assertTrue(health["available"])
        self.assertEqual(len(health["stages"]), 7)
        self.assertEqual(health["ontology"], ["SKILL", "PRIMITIVE", "OBJECT", "STATE"])

    def test_without_platform_credentials_health_says_why(self):
        service = KnowledgeBuildService(env={"ARANGO_URL": "x"})
        health = service.health()
        self.assertFalse(health["available"])
        self.assertIn("ARANGO_DB", health["reason"])


class RouteTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db, _writer, _books = corpus_db()
        planner = PlannerService(db, load_planner_config(PLANNER_ENV, use_llm=False))
        cls.generator = ScriptedGenerator(respond=split_responder())
        cls.fake = FakePlatform()
        cls.fake.add_project("robots")
        from autograph_pipeline.client import Platform

        cls.kgbuild = KnowledgeBuildService(
            job_files=cls.generator.rulebook_files,
            library_files=cls.generator.library_files,
            platform=Platform("https://platform.example", "robots", "root", "secret", transport=cls.fake),
            build_plangraph=plangraph_ok,
            env=PIPELINE_ENV,
            sleep=lambda _s: None,
        )
        cls.server = make_server(
            planner, host="127.0.0.1", port=0, quiet=True, generator=cls.generator, kgbuild=cls.kgbuild
        )
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def call(self, path, payload=None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                body = response.read()
                kind = response.headers.get("Content-Type", "")
                return response.status, json.loads(body) if "json" in kind else body.decode()
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def poll(self, path):
        deadline = time.time() + 10
        while time.time() < deadline:
            _, job = self.call(path)
            if job["state"] != "running":
                return job
            time.sleep(0.05)
        raise AssertionError(path)

    def test_split_generation_then_build_over_http(self):
        status, job = self.call("/api/generate", {"transcript": TRANSCRIPT, "split": True})
        self.assertEqual(status, 200)
        done = self.poll(f"/api/generate/{job['id']}")
        self.assertEqual(done["result"]["kind"], "split")

        status, run = self.call(
            "/api/kg/build",
            {"job": job["id"], "files": ["rulebook_dock_at_charger.md"], "project": "robots",
             "category": "http", "write": True},
        )
        self.assertEqual(status, 200, run)
        finished = self.poll(f"/api/kg/build/{run['id']}")
        self.assertTrue(finished["result"]["ok"], finished["result"])
        self.assertEqual(len(finished["detail"]["stages"]), 7)

    def test_a_refused_build_is_a_400(self):
        status, body = self.call("/api/kg/build", {"files": [], "project": "robots", "category": "x"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

    def test_an_unknown_run_is_a_404(self):
        status, _ = self.call("/api/kg/build/nope")
        self.assertEqual(status, 404)

    def test_health_route(self):
        status, health = self.call("/api/kg/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["available"])

    def test_the_pages_load_the_build_script(self):
        for page in ("/generate", "/repo", "/projects"):
            with self.subTest(page):
                _, html = self.call(page)
                self.assertIn('<script src="/static/kgbuild.js"></script>', html)
                self.assertNotIn("<script>", html)
        status, script = self.call("/static/kgbuild.js")
        self.assertEqual(status, 200)
        self.assertIn("function buildPanel", script)


if __name__ == "__main__":
    unittest.main()
