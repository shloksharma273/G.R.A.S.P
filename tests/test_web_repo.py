"""The repository page: parsing a repo, listing its docs, and generating from them.

No test here reaches GitHub. `FakeGitHub` stands in for the two hosts the module
is allowed to talk to, which keeps the suite offline and lets the awkward cases -
a used up rate limit, a private repo, a truncated tree - be tested at all.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from email.message import Message

from grasp_web import PlannerService, make_server
from grasp_web.generate import STATE_DONE
from grasp_web.repo import (
    MAX_SELECTED,
    MODE_CODE,
    MODE_DOCS,
    RepoBrowser,
    RepoError,
    is_doc,
    parse_repo,
    score,
)
from layer2_planning import load_planner_config

from .corpus_fixture import ENV as PLANNER_ENV, corpus_db
from .test_web_generate import ScriptedGenerator, wait

#: A small stand-in for a docs repo, with the shapes that matter: a procedure
#: page, an index, a translated copy, a changelog, and a non-markdown file.
TREE = {
    "truncated": False,
    "tree": [
        {"type": "blob", "path": "en/flight_modes_mc/land.md", "size": 4096},
        {"type": "blob", "path": "en/flight_modes_mc/takeoff.md", "size": 3072},
        {"type": "blob", "path": "en/advanced_config/prearm_arm_disarm.md", "size": 8192},
        {"type": "blob", "path": "en/index.md", "size": 1024},
        {"type": "blob", "path": "zh/flight_modes_mc/land.md", "size": 4096},
        {"type": "blob", "path": "en/CHANGELOG.md", "size": 512},
        {"type": "blob", "path": "en/assets/diagram.png", "size": 9999},
        {"type": "tree", "path": "en/flight_modes_mc", "size": 0},
    ],
}

PAGES = {
    "en/flight_modes_mc/land.md": "# Land Mode\n\nThe vehicle descends and disarms.",
    "en/flight_modes_mc/takeoff.md": "# Takeoff Mode\n\nThe vehicle climbs to altitude.",
    "en/advanced_config/prearm_arm_disarm.md": "# Arming\n\nEngage the safety switch first.",
    "en/index.md": "# Index\n\nWelcome.",
}


class FakeGitHub:
    """A scripted replacement for `repo._get`, recording what was asked for."""

    def __init__(self, tree=None, pages=None, branch="main"):
        self.tree = TREE if tree is None else tree
        self.pages = PAGES if pages is None else pages
        self.branch = branch
        self.calls: list[str] = []
        self.fail: Exception | None = None

    def __call__(self, url: str, accept: str = "") -> bytes:
        self.calls.append(url)
        if self.fail is not None:
            raise self.fail
        if "/git/trees/" in url:
            return json.dumps(self.tree).encode()
        if url.rstrip("/").endswith(tuple(f"/{part}" for part in ("PX4-user_guide",))) or (
            "api.github.com/repos/" in url and "/git/trees/" not in url
        ):
            return json.dumps({"default_branch": self.branch}).encode()
        for path, body in self.pages.items():
            if url.endswith(path):
                return body.encode()
        raise AssertionError(f"unexpected fetch: {url}")


def browser(case, fake: FakeGitHub | None = None) -> tuple[RepoBrowser, FakeGitHub]:
    """A browser whose GitHub is `fake`, restored when the test ends.

    Restoring matters: a leaked patch would silently disarm the SSRF guard test,
    which is the one test here that must exercise the real function.
    """
    import grasp_web.repo as module

    fake = fake or FakeGitHub()
    original = module._get
    module._get = fake  # type: ignore[assignment]
    # `case` is a TestCase in a test and the class itself in setUpClass.
    register = case.addClassCleanup if isinstance(case, type) else case.addCleanup
    register(lambda: setattr(module, "_get", original))
    return RepoBrowser(), fake


class ParseTests(unittest.TestCase):
    def test_a_full_url(self):
        ref = parse_repo("https://github.com/PX4/PX4-user_guide")
        self.assertEqual(ref.slug, "PX4/PX4-user_guide")
        self.assertEqual(ref.ref, "")

    def test_a_bare_slug(self):
        self.assertEqual(parse_repo("PX4/PX4-user_guide").slug, "PX4/PX4-user_guide")

    def test_a_tree_url_carries_the_branch_and_subpath(self):
        ref = parse_repo("https://github.com/PX4/PX4-user_guide/tree/main/en/flight_modes_mc")
        self.assertEqual(ref.ref, "main")
        self.assertEqual(ref.path, "en/flight_modes_mc")

    def test_a_git_suffix_is_tolerated(self):
        self.assertEqual(parse_repo("https://github.com/a/b.git").slug, "a/b")

    def test_another_host_is_refused(self):
        """Only GitHub is read - see the module docstring on why that is a limit."""
        with self.assertRaises(RepoError):
            parse_repo("https://gitlab.com/a/b")

    def test_an_arbitrary_url_is_refused(self):
        with self.assertRaises(RepoError):
            parse_repo("http://169.254.169.254/latest/meta-data/")

    def test_nothing_is_an_actionable_error(self):
        with self.assertRaises(RepoError) as caught:
            parse_repo("  ")
        self.assertIn("PX4", caught.exception.hint)


class FilterTests(unittest.TestCase):
    def test_markdown_is_a_doc(self):
        self.assertTrue(is_doc("en/flight_modes_mc/land.md"))
        self.assertTrue(is_doc("docs/guide.mdx"))

    def test_other_files_are_not(self):
        self.assertFalse(is_doc("en/assets/diagram.png"))

    def test_translations_are_dropped(self):
        """A translated copy of a page the repo already has in English."""
        self.assertFalse(is_doc("zh/flight_modes_mc/land.md"))
        self.assertFalse(is_doc("ko/index.md"))
        self.assertTrue(is_doc("en/index.md"))

    def test_machinery_is_dropped(self):
        for path in ("CHANGELOG.md", "README.md", "node_modules/x/y.md", ".github/PULL.md"):
            with self.subTest(path=path):
                self.assertFalse(is_doc(path))

    def test_procedure_paths_outrank_section_indexes(self):
        self.assertGreater(score("en/flight_modes_mc/land.md"), score("en/index.md"))
        self.assertGreater(score("en/advanced_config/prearm_arm_disarm.md"), score("en/api.md"))


class TreeTests(unittest.TestCase):
    def test_it_lists_only_documentation(self):
        api, _fake = browser(self)
        listing = api.tree("PX4/PX4-user_guide")
        paths = [f["path"] for f in listing["files"]]
        self.assertIn("en/flight_modes_mc/land.md", paths)
        self.assertNotIn("zh/flight_modes_mc/land.md", paths)
        self.assertNotIn("en/assets/diagram.png", paths)
        self.assertNotIn("en/CHANGELOG.md", paths)

    def test_the_likely_procedures_come_first(self):
        api, _fake = browser(self)
        listing = api.tree("PX4/PX4-user_guide")
        self.assertGreater(listing["files"][0]["score"], 0)
        self.assertEqual(listing["files"][-1]["path"], "en/index.md")

    def test_a_subpath_narrows_the_listing(self):
        api, _fake = browser(self)
        listing = api.tree("https://github.com/PX4/PX4-user_guide/tree/main/en/flight_modes_mc")
        self.assertEqual(
            sorted(f["path"] for f in listing["files"]),
            ["en/flight_modes_mc/land.md", "en/flight_modes_mc/takeoff.md"],
        )

    def test_the_default_branch_is_looked_up_when_none_is_given(self):
        api, fake = browser(self, FakeGitHub(branch="stable"))
        self.assertEqual(api.tree("PX4/PX4-user_guide")["ref"], "stable")

    def test_a_named_branch_is_used_without_a_lookup(self):
        api, fake = browser(self)
        api.tree("https://github.com/PX4/PX4-user_guide/tree/v1.15/en")
        self.assertFalse([c for c in fake.calls if c.endswith("/PX4-user_guide")])

    def test_the_tree_is_cached(self):
        """Picking files is iterative; re-fetching the tree would burn the rate limit."""
        api, fake = browser(self)
        api.tree("PX4/PX4-user_guide")
        before = len(fake.calls)
        second = api.tree("PX4/PX4-user_guide")
        self.assertEqual(len(fake.calls), before)
        self.assertTrue(second["from_cache"])

    def test_a_truncated_tree_says_so(self):
        api, _fake = browser(self, FakeGitHub(tree={**TREE, "truncated": True}))
        self.assertTrue(api.tree("PX4/PX4-user_guide")["truncated"])

    def test_a_repo_with_no_docs_is_an_actionable_error(self):
        api, _fake = browser(self, FakeGitHub(tree={"tree": [{"type": "blob", "path": "main.cpp"}]}))
        with self.assertRaises(RepoError) as caught:
            api.tree("PX4/PX4-Autopilot")
        self.assertIn("no markdown documentation", caught.exception.message)


class DocumentTests(unittest.TestCase):
    def test_the_chosen_pages_become_one_document(self):
        api, _fake = browser(self)
        doc = api.document(
            "PX4/PX4-user_guide",
            ["en/flight_modes_mc/takeoff.md", "en/flight_modes_mc/land.md"],
            ref="main",
        )
        self.assertIn("The vehicle climbs to altitude.", doc["text"])
        self.assertIn("The vehicle descends and disarms.", doc["text"])

    def test_each_page_keeps_its_path_as_a_heading(self):
        """Without it the rulebook's source is untraceable once the files are one blob."""
        api, _fake = browser(self)
        doc = api.document("PX4/PX4-user_guide", ["en/flight_modes_mc/land.md"], ref="main")
        self.assertIn("# en/flight_modes_mc/land.md", doc["text"])

    def test_the_source_url_points_back_at_the_repo(self):
        api, _fake = browser(self)
        doc = api.document("PX4/PX4-user_guide", ["en/index.md"], ref="main")
        self.assertEqual(doc["source_url"], "https://github.com/PX4/PX4-user_guide/tree/main")

    def test_duplicate_choices_are_read_once(self):
        api, _fake = browser(self)
        doc = api.document("PX4/PX4-user_guide", ["en/index.md", "en/index.md"], ref="main")
        self.assertEqual(doc["paths"], ["en/index.md"])

    def test_choosing_nothing_is_an_error(self):
        api, _fake = browser(self)
        with self.assertRaises(RepoError):
            api.document("PX4/PX4-user_guide", [], ref="main")

    def test_too_many_pages_is_refused_with_the_reason(self):
        """A rulebook describes one procedure, not a whole guide."""
        api, _fake = browser(self)
        with self.assertRaises(RepoError) as caught:
            api.document("PX4/PX4-user_guide", [f"en/p{i}.md" for i in range(MAX_SELECTED + 1)])
        self.assertIn("one procedure", caught.exception.hint)

    def test_a_non_document_path_is_refused(self):
        api, _fake = browser(self)
        with self.assertRaises(RepoError):
            api.document("PX4/PX4-user_guide", ["en/assets/diagram.png"], ref="main")

    def test_the_size_cap_reports_what_it_skipped(self):
        import grasp_web.repo as module

        big = {f"en/p{i}.md": "word " * 60_000 for i in range(3)}
        api, _fake = browser(self, FakeGitHub(pages=big))
        doc = api.document("PX4/PX4-user_guide", list(big), ref="main")
        self.assertTrue(doc["skipped"], "an over-cap selection should name what it dropped")
        self.assertLess(len(doc["paths"]), len(big))




class CodeModeTests(unittest.TestCase):
    """Reading a repo for the calls it exposes, rather than the prose it ships."""

    CODE_TREE = {
        "truncated": False,
        "tree": [
            {"type": "blob", "path": "ur_dashboard_msgs/srv/GetRobotMode.srv", "size": 200},
            {"type": "blob", "path": "ur_robot_driver/src/dashboard_client_ros.cpp", "size": 30000},
            {"type": "blob", "path": "ur_robot_driver/doc/dashboard_client.rst", "size": 9000},
            {"type": "blob", "path": "ur_robot_driver/test/test_driver.cpp", "size": 5000},
            {"type": "blob", "path": "ur_description/meshes/base.png", "size": 99999},
        ],
    }
    CODE_PAGES = {
        "ur_dashboard_msgs/srv/GetRobotMode.srv": "---\nbool success\nstring message",
        "ur_robot_driver/src/dashboard_client_ros.cpp": 'create_service<std_srvs::srv::Trigger>("~/power_on"',
        "ur_robot_driver/doc/dashboard_client.rst": "brake_release (std_srvs/Trigger)",
    }

    def _browser(self):
        return browser(self, FakeGitHub(tree=self.CODE_TREE, pages=self.CODE_PAGES))

    def test_docs_mode_lists_only_prose(self):
        api, _fake = self._browser()
        paths = [f["path"] for f in api.tree("a/b", mode=MODE_DOCS)["files"]]
        self.assertIn("ur_robot_driver/doc/dashboard_client.rst", paths)
        self.assertNotIn("ur_robot_driver/src/dashboard_client_ros.cpp", paths)

    def test_code_mode_lists_the_sources_too(self):
        api, _fake = self._browser()
        paths = [f["path"] for f in api.tree("a/b", mode=MODE_CODE)["files"]]
        self.assertIn("ur_robot_driver/src/dashboard_client_ros.cpp", paths)
        self.assertIn("ur_dashboard_msgs/srv/GetRobotMode.srv", paths)

    def test_tests_are_skipped_in_code_mode(self):
        """A test names the interfaces it exercises and declares none of them."""
        api, _fake = self._browser()
        paths = [f["path"] for f in api.tree("a/b", mode=MODE_CODE)["files"]]
        self.assertNotIn("ur_robot_driver/test/test_driver.cpp", paths)

    def test_binaries_are_never_listed(self):
        api, _fake = self._browser()
        for mode in (MODE_DOCS, MODE_CODE):
            paths = [f["path"] for f in api.tree("a/b", mode=mode)["files"]]
            self.assertNotIn("ur_description/meshes/base.png", paths)

    def test_an_interface_definition_outranks_a_source_file(self):
        api, _fake = self._browser()
        files = api.tree("a/b", mode=MODE_CODE)["files"]
        self.assertTrue(files[0]["path"].endswith((".srv", ".action", ".msg")))

    def test_the_two_modes_do_not_share_a_cache_entry(self):
        """They list different files for the same repo."""
        api, fake = self._browser()
        api.tree("a/b", mode=MODE_DOCS)
        before = len(fake.calls)
        api.tree("a/b", mode=MODE_CODE)
        self.assertGreater(len(fake.calls), before)

    def test_the_mode_is_reported_back(self):
        api, _fake = self._browser()
        self.assertEqual(api.tree("a/b", mode=MODE_CODE)["mode"], MODE_CODE)

    def test_a_source_file_cannot_be_read_in_docs_mode(self):
        api, _fake = self._browser()
        with self.assertRaises(RepoError):
            api.document("a/b", ["ur_robot_driver/src/dashboard_client_ros.cpp"], ref="main")

    def test_a_source_file_reads_in_code_mode(self):
        api, _fake = self._browser()
        doc = api.document(
            "a/b", ["ur_robot_driver/src/dashboard_client_ros.cpp"], ref="main", mode=MODE_CODE
        )
        self.assertIn("create_service", doc["text"])
        self.assertEqual(doc["mode"], MODE_CODE)


class GuardTests(unittest.TestCase):
    """The fetcher is handed a URL by whoever opens the page."""

    def test_a_non_github_host_is_never_fetched(self):
        import grasp_web.repo as module

        with self.assertRaises(RepoError) as caught:
            module._get("http://169.254.169.254/latest/meta-data/")
        self.assertIn("only GitHub", caught.exception.hint)

    def test_a_used_up_rate_limit_explains_the_token(self):
        import grasp_web.repo as module

        headers = Message()
        headers["X-RateLimit-Remaining"] = "0"
        error = urllib.error.HTTPError(
            "https://api.github.com/x", 403, "Forbidden", headers, None
        )
        translated = module._http_error(error, "https://api.github.com/x")
        self.assertIn("GITHUB_TOKEN", translated.hint)

    def test_a_missing_repo_says_it_may_be_private(self):
        import grasp_web.repo as module

        error = urllib.error.HTTPError("https://api.github.com/x", 404, "", Message(), None)
        self.assertIn("private", module._http_error(error, "x").message)



class CoverageTests(unittest.TestCase):
    """What became of the selection - not a gate issue, but the user's question."""

    DOC = (
        "# en/arm.md\n\npress the safety switch, then arm the vehicle\n\n"
        "# en/land.md\n\nthe vehicle descends slowly and touches down"
    )
    BOOK = {"primitives": [{"name": "press_safety_switch"}, {"name": "arm_vehicle"}]}

    def test_a_page_no_step_came_from_is_named(self):
        from grasp_web.generate import unused_pages

        self.assertEqual(
            unused_pages(self.DOC, ["en/arm.md", "en/land.md"], self.BOOK), ["en/land.md"]
        )

    def test_a_page_every_word_matches_is_not_named(self):
        from grasp_web.generate import unused_pages

        book = {"primitives": [{"name": "press_safety_switch"}, {"name": "vehicle_descends"}]}
        self.assertEqual(unused_pages(self.DOC, ["en/arm.md", "en/land.md"], book), [])

    def test_a_single_shared_word_is_not_enough(self):
        """"vehicle" is on every page of a drone manual; one hit must prove nothing."""
        from grasp_web.generate import unused_pages

        book = {"primitives": [{"name": "press_safety_switch"}]}
        self.assertIn("en/land.md", unused_pages(self.DOC, ["en/arm.md", "en/land.md"], book))

    def test_no_rulebook_reports_nothing(self):
        from grasp_web.generate import unused_pages

        self.assertEqual(unused_pages(self.DOC, ["en/arm.md"], None), [])


class RouteTests(unittest.TestCase):
    """The two routes, over real HTTP, with GitHub and the model both faked."""

    @classmethod
    def setUpClass(cls):
        cls.db, _writer, _books = corpus_db()
        cls.config = load_planner_config({**PLANNER_ENV, "PLANNER_USE_LLM": "0"})
        cls.generator = ScriptedGenerator()
        cls.browser, cls.fake = browser(cls)
        cls.server = make_server(
            PlannerService(cls.db, cls.config),
            host="127.0.0.1",
            port=0,
            quiet=True,
            generator=cls.generator,
            browser=cls.browser,
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
        with urllib.request.urlopen(self.url(path), timeout=30) as response:
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

    def test_the_repo_page_is_served(self):
        status, body, content_type = self.get("/repo")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"rulebook", body.lower())

    def test_its_scripts_are_served(self):
        for asset in ("/static/repo.js", "/static/rulebook.js"):
            with self.subTest(asset=asset):
                status, body, content_type = self.get(asset)
                self.assertEqual(status, 200)
                self.assertIn("javascript", content_type)
                self.assertTrue(body)

    def test_the_tree_route_lists_pages(self):
        status, payload = self.post("/api/repo/tree", {"url": "PX4/PX4-user_guide"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["repo"], "PX4/PX4-user_guide")
        self.assertTrue(payload["files"])

    def test_a_bad_repo_is_a_400_with_a_hint(self):
        status, payload = self.post("/api/repo/tree", {"url": "https://gitlab.com/a/b"})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)
        self.assertIn("hint", payload)

    def test_generating_with_no_pages_is_a_400(self):
        status, payload = self.post(
            "/api/repo/generate", {"url": "PX4/PX4-user_guide", "paths": []}
        )
        self.assertEqual(status, 400)
        self.assertIn("no pages chosen", payload["error"])

    def test_paths_must_be_a_list(self):
        status, payload = self.post(
            "/api/repo/generate", {"url": "PX4/PX4-user_guide", "paths": "land.md"}
        )
        self.assertEqual(status, 400)

    def test_a_generation_runs_end_to_end_over_http(self):
        status, started = self.post(
            "/api/repo/generate",
            {
                "url": "PX4/PX4-user_guide",
                "ref": "main",
                "paths": ["en/flight_modes_mc/takeoff.md", "en/flight_modes_mc/land.md"],
            },
        )
        self.assertEqual(status, 200)

        job = wait(self.generator, started["id"])
        self.assertEqual(job["state"], STATE_DONE)
        self.assertEqual(job["result"]["verdict"], "accept")

    def test_the_result_is_marked_as_documentation(self):
        """The register matters downstream: a manual's preconditions are stated."""
        _status, started = self.post(
            "/api/repo/generate",
            {"url": "PX4/PX4-user_guide", "ref": "main", "paths": ["en/flight_modes_mc/land.md"]},
        )
        job = wait(self.generator, started["id"])
        self.assertEqual(job["result"]["source_kind"], "manual")

    def test_the_job_records_which_pages_were_read(self):
        _status, started = self.post(
            "/api/repo/generate",
            {"url": "PX4/PX4-user_guide", "ref": "main", "paths": ["en/index.md"]},
        )
        job = wait(self.generator, started["id"])
        self.assertEqual(job["detail"]["paths"], ["en/index.md"])
        self.assertEqual(job["detail"]["repo"], "PX4/PX4-user_guide")

    def test_a_page_that_contributed_nothing_is_reported(self):
        """Four pages in, one procedure out - the selection has to hear about it."""
        _status, started = self.post(
            "/api/repo/generate",
            {
                "url": "PX4/PX4-user_guide",
                "ref": "main",
                "paths": ["en/advanced_config/prearm_arm_disarm.md", "en/flight_modes_mc/land.md"],
            },
        )
        job = wait(self.generator, started["id"])
        # The scripted model always returns the tea rulebook, so neither PX4 page
        # can have contributed a step and both must be named.
        self.assertEqual(len(job["detail"]["unused"]), 2)

    def test_the_other_routes_still_work(self):
        status, payload = self.post(
            "/api/plan", {"command": "make me a masala chai", "use_llm": False}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["kind"], "plan")
