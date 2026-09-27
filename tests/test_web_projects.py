"""The projects page: discovery, the bridge runner, and the build routes.

No test here reaches the live database. `FakeCatalog` stands in for one, which
keeps the suite offline and lets the states that matter be constructed directly -
a project with only a corpus, one whose knowledge graph uses an ontology the
bridge does not understand, one mid-build.
"""

from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from grasp_web import PlannerService, make_server
from grasp_web.api import PlannerRegistry
from grasp_web.builder import BuildService
from grasp_web.jobs import STATE_DONE, STATE_RUNNING, JobStore
from grasp_web.projects import (
    STAGE_CORPUS,
    STAGE_KG,
    STAGE_PLAN,
    Project,
    discover,
    find,
    graph_url,
)
from layer2_planning import load_planner_config

from .corpus_fixture import ENV as PLANNER_ENV, corpus_db


class FakeCatalog:
    """Just enough database to be discovered: graphs, counts, and two queries."""

    def __init__(self, graphs, counts=None, types=None, scopes=None):
        self._graphs = list(graphs)
        self._counts = dict(counts or {})
        self._types = dict(types or {})
        self._scopes = dict(scopes or {})

    def graphs(self):
        return [{"name": name} for name in self._graphs]

    def has_collection(self, name):
        return name in self._counts

    def collection(self, name):
        counts = self._counts

        class _Collection:
            def count(self):
                return counts.get(name, 0)

        return _Collection()

    class _Aql:
        def __init__(self, outer):
            self.outer = outer

        def execute(self, query, bind_vars=None, **kwargs):
            bind_vars = bind_vars or {}
            target = bind_vars.get("@entities") or bind_vars.get("@skills") or ""
            if "@entities" in bind_vars:
                return iter(self.outer._types.get(target, []))
            return iter(self.outer._scopes.get(target, []))

    @property
    def aql(self):
        return FakeCatalog._Aql(self)


def catalog(**kwargs):
    return FakeCatalog(**kwargs)


ROBOT_TYPES = ["skill", "tool", "object", "state"]


class StageTests(unittest.TestCase):
    """A project's stage is the furthest graph it has, and nothing else."""

    def test_corpus_only(self):
        db = catalog(graphs=["demo_CorpusGraph"])
        self.assertEqual(discover(db)[0].stage, STAGE_CORPUS)

    def test_knowledge_graph(self):
        db = catalog(
            graphs=["demo_CorpusGraph", "demo_kg"],
            counts={"demo_Entities": 40, "demo_Relations": 90},
            types={"demo_Entities": ROBOT_TYPES},
        )
        entry = discover(db)[0]
        self.assertEqual(entry.stage, STAGE_KG)
        self.assertTrue(entry.buildable)
        self.assertFalse(entry.plannable)

    def test_plangraph(self):
        db = catalog(
            graphs=["demo_CorpusGraph", "demo_kg", "demo_PlanGraph"],
            counts={
                "demo_Entities": 40, "demo_Relations": 90,
                "demo_Skills": 2, "demo_PlanEdges": 50,
            },
            types={"demo_Entities": ROBOT_TYPES},
            scopes={"demo_Skills": ["make_tea", "make_toast"]},
        )
        entry = discover(db)[0]
        self.assertEqual(entry.stage, STAGE_PLAN)
        self.assertTrue(entry.plannable)
        self.assertEqual(entry.scopes, ["make_tea", "make_toast"])

    def test_an_empty_plangraph_is_not_a_built_one(self):
        """The graph can exist with nothing in it; the counts are what decide."""
        db = catalog(
            graphs=["demo_kg", "demo_PlanGraph"],
            counts={"demo_Entities": 4, "demo_Relations": 9, "demo_PlanEdges": 0},
            types={"demo_Entities": ROBOT_TYPES},
        )
        self.assertEqual(discover(db)[0].stage, STAGE_KG)

    def test_arangos_own_graphs_are_not_projects(self):
        self.assertEqual(discover(catalog(graphs=["_viewpointGraph"])), [])

    def test_the_furthest_along_are_listed_first(self):
        db = catalog(
            graphs=["a_CorpusGraph", "z_kg", "z_PlanGraph"],
            counts={"z_Entities": 4, "z_Relations": 9, "z_Skills": 1, "z_PlanEdges": 9},
            types={"z_Entities": ROBOT_TYPES},
        )
        self.assertEqual([p.name for p in discover(db)], ["z", "a"])

    def test_find_names_one(self):
        db = catalog(graphs=["demo_CorpusGraph"])
        self.assertIsNotNone(find(db, "demo"))
        self.assertIsNone(find(db, "nope"))


class OntologyTests(unittest.TestCase):
    """A KG can exist and still hold nothing the bridge can plan."""

    def _project(self, types):
        db = catalog(
            graphs=["demo_kg"],
            counts={"demo_Entities": 40, "demo_Relations": 90},
            types={"demo_Entities": types},
        )
        return discover(db)[0]

    def test_the_robot_ontology_is_buildable(self):
        self.assertTrue(self._project(ROBOT_TYPES).buildable)

    def test_tool_counts_as_a_primitive(self):
        """Live AutoGraph builds label primitives `tool`; Station 2 resolves it."""
        entry = self._project(["skill", "tool", "state"])
        self.assertIn("PRIMITIVE", entry.roles)
        self.assertTrue(entry.buildable)

    def test_an_unrelated_ontology_is_refused_with_its_types(self):
        entry = self._project(["insurance_claim", "adjuster", "policy_clause"])
        self.assertFalse(entry.buildable)
        self.assertIn("adjuster", entry.blocked_reason)

    def test_plural_types_are_refused_and_the_reason_says_so(self):
        """A live project names its types `skills`/`primitives`, which do not resolve."""
        entry = self._project(["skills", "primitives", "object", "state"])
        self.assertFalse(entry.buildable)
        self.assertIn("singular", entry.blocked_reason)

    def test_a_kg_with_no_relations_is_not_buildable(self):
        db = catalog(
            graphs=["demo_kg"],
            counts={"demo_Entities": 40, "demo_Relations": 0},
            types={"demo_Entities": ROBOT_TYPES},
        )
        entry = discover(db)[0]
        self.assertFalse(entry.buildable)
        self.assertIn("no relationships", entry.blocked_reason)

    def test_a_project_built_by_another_route_reports_no_ontology_problem(self):
        """A direct-ingest PlanGraph has no KG and does not need one."""
        db = catalog(
            graphs=["demo_PlanGraph"],
            counts={"demo_Skills": 1, "demo_PlanEdges": 20},
            scopes={"demo_Skills": ["make_tea"]},
        )
        entry = discover(db)[0]
        self.assertTrue(entry.plannable)
        self.assertFalse(entry.buildable)


class GraphUrlTests(unittest.TestCase):
    def test_it_points_at_arangos_own_viewer(self):
        url = graph_url("https://db.example.com/", "test_shlok", "demo_PlanGraph")
        self.assertEqual(
            url,
            "https://db.example.com/ui/test_shlok/graphs/demo_PlanGraph",
        )


class JobStoreTests(unittest.TestCase):
    def test_a_job_runs_and_finishes(self):
        store = JobStore()
        job = store.create(stage="starting")

        def target(j):
            j.result = {"ok": True}
            j.state = STATE_DONE

        store.run(job, target)
        for _ in range(200):
            if store.status(job.id)["state"] != STATE_RUNNING:
                break
            import time

            time.sleep(0.01)
        self.assertEqual(store.status(job.id)["state"], STATE_DONE)

    def test_a_raising_job_becomes_an_error_not_a_crash(self):
        import time

        store = JobStore()
        job = store.create()
        store.run(job, lambda _j: (_ for _ in ()).throw(ValueError("boom")))
        for _ in range(200):
            if store.status(job.id)["state"] != STATE_RUNNING:
                break
            time.sleep(0.01)
        self.assertIn("boom", store.status(job.id)["error"])

    def test_old_jobs_are_dropped(self):
        store = JobStore(max_jobs=3)
        ids = [store.create().id for _ in range(5)]
        self.assertIsNone(store.status(ids[0]))
        self.assertIsNotNone(store.status(ids[-1]))


class ScriptedBuilder(BuildService):
    """A BuildService over a fake catalog, with no real connection."""

    def __init__(self, db, writes=True):
        super().__init__(connect=lambda: db)
        self._writes = writes

    def config(self):
        from kg_read_harness.config import load_config

        return load_config(
            {
                "ARANGO_URL": "https://db.invalid",
                "ARANGO_DB": "test_shlok",
                "ARANGO_USERNAME": "root",
                "ARANGO_PASSWORD": "",
                "PROJECT_NAME": "demo",
            }
        )

    def _can_write(self):
        return self._writes


class BuilderTests(unittest.TestCase):
    KG = dict(
        graphs=["demo_CorpusGraph", "demo_kg"],
        counts={"demo_Entities": 40, "demo_Relations": 90},
        types={"demo_Entities": ROBOT_TYPES},
    )

    def test_projects_carry_a_deep_link_per_built_graph(self):
        listing = ScriptedBuilder(catalog(**self.KG)).projects()
        graphs = listing["projects"][0]["graphs"]
        self.assertIn("corpus", graphs)
        self.assertIn("kg", graphs)
        self.assertNotIn("plangraph", graphs)
        self.assertIn("/ui/test_shlok/graphs/demo_kg", graphs["kg"])

    def test_an_unknown_project_is_refused(self):
        started = ScriptedBuilder(catalog(**self.KG)).start("nope")
        self.assertIn("not a project", started["error"])

    def test_a_project_that_cannot_be_built_is_refused_with_the_reason(self):
        db = catalog(
            graphs=["demo_kg"],
            counts={"demo_Entities": 4, "demo_Relations": 9},
            types={"demo_Entities": ["adjuster", "policy_clause"]},
        )
        started = ScriptedBuilder(db).start("demo")
        self.assertIn("cannot be built", started["error"])
        self.assertIn("adjuster", started["hint"])

    def test_building_without_write_credentials_is_refused(self):
        started = ScriptedBuilder(catalog(**self.KG), writes=False).start("demo")
        self.assertIn("write credentials", started["error"])

    def test_naming_no_project_is_refused(self):
        self.assertIn("error", ScriptedBuilder(catalog(**self.KG)).start(""))

    def test_a_second_build_of_the_same_project_is_refused(self):
        """Two scoped writes at once would interleave a purge with a write."""
        service = ScriptedBuilder(catalog(**self.KG))
        service._busy.add("demo")
        started = service.start("demo")
        self.assertIn("already running", started["error"])




class BridgeTests(unittest.TestCase):
    """All five stations over the chai knowledge graph, reading and writing fakes.

    The fixture's collections are already named `masala_chai_Entities` /
    `_Relations`, which is the convention the bridge derives from a project name -
    so this exercises the real derivation rather than a special case for tests.
    """

    def _run(self, dry_run=False, **env_overrides):
        from grasp_web import bridge

        from . import chai_fixture as chai
        from . import fake_llm
        from .fake_arango import make_db as make_read_db
        from .fake_writable_arango import make_db as make_write_db

        env = chai.env(
            LLM_API_KEY="test-key-not-real",
            LLM_MODEL="test/model-1",
            LLM_CACHE="0",
            **env_overrides,
        )
        provider = fake_llm.FakeProvider(
            fake_llm.config(),
            fake_llm.responder_from({"precondition": ("requires", 0.95)},
                                    default=("produces", 0.95)),
        )
        seen = []
        result = bridge.build(
            "masala_chai",
            make_read_db(chai.entities(), chai.relations()),
            dry_run=dry_run,
            env=env,
            provider=provider,
            write_db=make_write_db(),
            on_stage=lambda stage, report: seen.append(stage),
        )
        return result, seen

    def test_a_build_runs_every_station_in_order(self):
        result, seen = self._run(dry_run=True)
        from grasp_web.bridge import STAGES

        self.assertEqual([s.stage for s in result.stations], list(STAGES))
        # A stage is announced twice - once as it starts, once with its report -
        # so the page can name what it is waiting on before there is anything to
        # show. Collapse the repeats and the order must still be the stations'.
        collapsed = [s for i, s in enumerate(seen) if i == 0 or s != seen[i - 1]]
        self.assertEqual(collapsed, list(STAGES))

    def test_it_produces_a_scoped_plangraph(self):
        result, _seen = self._run(dry_run=True)
        self.assertTrue(result.ok, result.failed)
        self.assertEqual(result.scopes, ["make_masala_chai"])
        self.assertEqual(result.graph, "masala_chai_PlanGraph")

    def test_the_counts_come_from_the_stations_themselves(self):
        result, _seen = self._run(dry_run=True)
        read, classify = result.stations[0], result.stations[1]
        self.assertGreater(read.counts["bundles"], 0)
        self.assertEqual(
            classify.counts["stamped"] + classify.counts["deferred"] + classify.counts["parked"],
            read.counts["bundles"],
        )

    def test_the_cost_separates_the_prepass_from_the_model(self):
        """On some builds the model is never called; saying so is the point."""
        result, _seen = self._run(dry_run=True)
        self.assertIn("lexical", result.llm)
        self.assertIn("by_model", result.llm)
        self.assertEqual(
            result.llm["lexical"] + result.llm["by_model"], result.llm["settled"]
        )

    def test_a_dry_run_writes_nothing(self):
        result, _seen = self._run(dry_run=True)
        self.assertTrue(result.dry_run)

    def test_a_write_reports_what_it_wrote(self):
        result, _seen = self._run(dry_run=False)
        self.assertTrue(result.ok, result.failed)
        self.assertTrue(result.written, "a write should report its collections")
        self.assertIn("masala_chai_PlanEdges", result.written)

    def test_the_collections_are_derived_from_the_project_name(self):
        from grasp_web.bridge import project_config

        from . import chai_fixture as chai
        from kg_read_harness.config import load_config

        base = load_config(chai.env())
        scoped = project_config(base, "other_project")
        self.assertEqual(scoped.entity_collection, "other_project_Entities")
        self.assertEqual(scoped.relation_collection, "other_project_Relations")

    def test_an_empty_knowledge_graph_fails_with_a_reason(self):
        from grasp_web import bridge

        from . import chai_fixture as chai
        from .fake_arango import make_db as make_read_db

        result = bridge.build(
            "masala_chai", make_read_db([], []), dry_run=True, env=chai.env()
        )
        self.assertFalse(result.ok)
        self.assertIn("nothing to build from", result.failed)

    def test_no_model_configured_is_an_actionable_error(self):
        """Without a key the ambiguous bucket cannot be settled at all."""
        from kg_read_harness.errors import HarnessError

        from grasp_web import bridge

        from . import chai_fixture as chai
        from .fake_arango import make_db as make_read_db

        with self.assertRaises(HarnessError) as caught:
            bridge.build(
                "masala_chai",
                make_read_db(chai.entities(), chai.relations()),
                dry_run=True,
                env=chai.env(),  # no LLM key
            )
        self.assertIn("no preconditions", caught.exception.hint)

class RegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _writer, _books = corpus_db()
        cls.config = load_planner_config({**PLANNER_ENV, "PLANNER_USE_LLM": "0"})

    def test_one_service_per_project_and_it_is_cached(self):
        registry = PlannerRegistry(self.db, self.config)
        first = registry.for_project("alpha")
        self.assertIs(first, registry.for_project("alpha"))
        self.assertIsNot(first, registry.for_project("beta"))

    def test_the_prefix_follows_the_project(self):
        registry = PlannerRegistry(self.db, self.config)
        self.assertEqual(registry.config_for("alpha").prefix, "alpha")

    def test_an_unnamed_project_falls_back_to_the_default(self):
        registry = PlannerRegistry(self.db, self.config)
        self.assertEqual(registry.for_project("").config.prefix, self.config.prefix)

    def test_a_build_invalidates_that_project(self):
        """A build rewrites exactly what the cache holds."""
        registry = PlannerRegistry(self.db, self.config)
        first = registry.for_project("alpha")
        registry.invalidate("alpha")
        self.assertIsNot(first, registry.for_project("alpha"))

    def test_invalidating_everything_clears_the_cache(self):
        registry = PlannerRegistry(self.db, self.config)
        registry.for_project("alpha")
        registry.for_project("beta")
        registry.invalidate()
        self.assertEqual(registry.cached(), [])




class StylesheetTests(unittest.TestCase):
    """An element the markup hides must actually be hidden.

    `hidden` is a UA-stylesheet rule, so ANY author `display` on the same element
    beats it. The confirm dialog shipped with `display: grid` and no guard, which
    left it on screen from page load with an empty body - visible, blocking, and
    triggered by nothing. This encodes the rule rather than that one instance.
    """

    def setUp(self):
        import re

        self.re = re
        self.static = Path("grasp_web/static")
        self.css = (self.static / "app.css").read_text(encoding="utf-8")

    def _hidden_selectors(self):
        """Every id/class the HTML hides with the `hidden` attribute."""
        names = set()
        for page in sorted(self.static.glob("*.html")):
            html = page.read_text(encoding="utf-8")
            for tag in self.re.findall(r"<[^>]*\bhidden\b[^>]*>", html):
                for found in self.re.findall(r'id="([^"]+)"', tag):
                    names.add(f"#{found}")
                for found in self.re.findall(r'class="([^"]+)"', tag):
                    names.update(f".{part}" for part in found.split())
        return names

    def _sets_display(self, selector):
        pattern = rf"(?m)^\s*{self.re.escape(selector)}\s*\{{([^}}]*)\}}"
        return any("display" in block for block in self.re.findall(pattern, self.css))

    def test_something_is_hidden_in_the_markup(self):
        """Guard the guard: a silent regex change must not empty this suite."""
        self.assertTrue(self._hidden_selectors())

    def test_every_hidden_element_that_sets_display_has_a_hidden_guard(self):
        for selector in sorted(self._hidden_selectors()):
            if not self._sets_display(selector):
                continue
            with self.subTest(selector=selector):
                self.assertIn(
                    f"{selector}[hidden]",
                    self.css,
                    f"{selector} sets display, so the `hidden` attribute does nothing "
                    f"without a `{selector}[hidden] {{ display: none }}` rule",
                )

    def test_the_confirm_dialog_is_hidden_by_default(self):
        html = (self.static / "projects.html").read_text(encoding="utf-8")
        self.assertIn('id="confirm" hidden', html)
        self.assertIn(".modal[hidden]", self.css)

class RouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db, _writer, _books = corpus_db()
        cls.config = load_planner_config({**PLANNER_ENV, "PLANNER_USE_LLM": "0"})
        cls.catalog = catalog(
            graphs=["demo_CorpusGraph", "demo_kg"],
            counts={"demo_Entities": 40, "demo_Relations": 90},
            types={"demo_Entities": ROBOT_TYPES},
        )
        cls.builder = ScriptedBuilder(cls.catalog)
        cls.registry = PlannerRegistry(cls.db, cls.config)
        cls.server = make_server(
            PlannerService(cls.db, cls.config),
            host="127.0.0.1",
            port=0,
            quiet=True,
            builder=cls.builder,
            registry=cls.registry,
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

    def test_the_projects_page_is_served(self):
        status, body, content_type = self.get("/projects")
        self.assertEqual(status, 200)
        self.assertIn("text/html", content_type)
        self.assertIn(b"project", body.lower())

    def test_its_script_is_served(self):
        status, body, content_type = self.get("/static/projects.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", content_type)

    def test_the_projects_route_lists_them(self):
        status, body, _ = self.get("/api/projects")
        self.assertEqual(status, 200)
        payload = json.loads(body)
        self.assertEqual(payload["database"], "test_shlok")
        self.assertEqual(payload["projects"][0]["name"], "demo")

    def test_an_unbuildable_project_is_a_400(self):
        status, payload = self.post("/api/projects/build", {"project": "nope"})
        self.assertEqual(status, 400)
        self.assertIn("error", payload)

    def test_an_unknown_build_job_is_a_404(self):
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.get("/api/projects/build/deadbeef")
        self.assertEqual(caught.exception.code, 404)

    def test_planning_still_works_without_a_project(self):
        status, payload = self.post(
            "/api/plan", {"command": "make me a masala chai", "use_llm": False}
        )
        self.assertEqual(status, 200)
        self.assertEqual(payload["kind"], "plan")

    def test_naming_a_project_selects_its_planner(self):
        """An unbuilt project has no skills, so it answers rather than guessing."""
        status, payload = self.post(
            "/api/plan",
            {"command": "make me a masala chai", "use_llm": False, "project": "demo"},
        )
        self.assertEqual(status, 200)
        self.assertIn("demo", self.registry.cached())

    def test_health_follows_the_project(self):
        status, body, _ = self.get("/api/health?project=demo")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["graph"], "demo_PlanGraph")

    def test_the_generator_routes_still_work(self):
        status, body, _ = self.get("/api/generate/health")
        self.assertEqual(status, 200)
