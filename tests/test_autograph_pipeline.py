"""The AutoGraph pipeline: rulebooks -> File Manager -> corpus -> strategies ->
ontology -> knowledge graph -> PlanGraph.

No test here reaches a network. `FakePlatform` answers the client's real HTTP
requests - paths, query strings, JSON and multipart bodies - the way the
services do, so what is tested is the wire contract as well as the stage logic.
"""

from __future__ import annotations

import io
import json
import unittest
from types import SimpleNamespace

from autograph_pipeline import Pipeline, Platform, Rulebook, encode_module, load_pipeline_config
from autograph_pipeline.cli import run as cli_run
from autograph_pipeline.client import ApiError, camel, encode_multipart, field, service_path
from autograph_pipeline.pipeline import (
    STAGE_CONNECT,
    STAGE_CORPUS,
    STAGE_KG,
    STAGE_ONTOLOGY,
    STAGE_PLANGRAPH,
    STAGE_STRATEGIZE,
    STAGE_UPLOAD,
    STAGES,
    STATUS_DONE,
    STATUS_FAILED,
    STATUS_PLANNED,
    STATUS_SKIPPED,
)
from kg_read_harness.errors import AuthError, ConfigError

from .fake_autograph import FakePlatform

ENV = {
    "ARANGO_URL": "https://platform.example",
    "ARANGO_DB": "robots",
    "ARANGO_USERNAME": "root",
    "ARANGO_PASSWORD": "secret",
    "AUTOGRAPH_POLL_SECONDS": "0",
}

ONTOLOGY = ["SKILL", "PRIMITIVE", "OBJECT", "STATE"]

BOOKS = [
    Rulebook("rulebook_go_to_location.md", b"# go to location\n" * 20),
    Rulebook("rulebook_dock_at_charger.md", b"# dock at charger\n" * 20),
]


def plangraph_ok(project, on_stage):
    on_stage("writing the PlanGraph", SimpleNamespace(detail="wrote 2 skill scope(s)"))
    return {"ok": True, "scopes": ["go_to_location", "dock_at_charger"], "graph": f"{project}_PlanGraph"}


class PipelineCase(unittest.TestCase):
    def setUp(self):
        self.fake = FakePlatform()
        self.progress: list[tuple[str, str]] = []
        self.plangraph_calls: list[str] = []

    def platform(self, **overrides):
        return Platform(
            url="https://platform.example", database="robots", username="root",
            password="secret", transport=self.fake, **overrides,
        )

    def pipeline(self, project="robots", category="nav", books=BOOKS, env=None, **kwargs):
        def plangraph(project, on_stage):
            self.plangraph_calls.append(project)
            return plangraph_ok(project, on_stage)

        kwargs.setdefault("build_plangraph", plangraph)
        return Pipeline(
            self.platform(),
            load_pipeline_config({**ENV, **(env or {})}),
            project,
            category,
            list(books),
            on_progress=lambda stage, message: self.progress.append((stage, message)),
            sleep=lambda _seconds: None,
            **kwargs,
        )

    def statuses(self, result):
        return {s.stage: s.status for s in result.stages}


class FreshBuildTest(PipelineCase):
    def test_every_stage_runs_in_order_on_a_new_category(self):
        self.fake.add_project("robots")
        result = self.pipeline(write=True).run()

        self.assertTrue(result.ok, result.failed)
        self.assertEqual([s.stage for s in result.stages], list(STAGES))
        self.assertEqual(set(self.statuses(result).values()), {STATUS_DONE})
        self.assertEqual(result.service, "/autograph/s0001")
        self.assertEqual(self.plangraph_calls, ["robots"])
        self.assertEqual(result.plangraph["scopes"], ["go_to_location", "dock_at_charger"])

    def test_files_land_in_the_two_level_scope(self):
        self.fake.add_project("robots")
        self.pipeline(write=True).run()
        self.assertEqual(
            sorted((f["name"], tuple(f["scope"])) for f in self.fake.files),
            [
                ("rulebook_dock_at_charger.md", ("robots", "nav")),
                ("rulebook_go_to_location.md", ("robots", "nav")),
            ],
        )

    def test_every_cluster_gets_the_bridge_ontology_before_the_importer_runs(self):
        self.fake.add_project("robots")
        self.pipeline(write=True).run()
        svc = self.fake.service_for("robots")
        (row,) = svc.strategies.values()
        self.assertEqual(row["strategyType"], "FullGraphRAG")
        self.assertEqual(row["entityTypes"], ONTOLOGY)
        order = [p for m, p in self.fake.writes()]
        patch = next(i for i, p in enumerate(order) if "/rag-strategizer/strategy/" in p)
        orchestrate = order.index("/autograph/s0001/v1/orchestrate")
        self.assertLess(patch, orchestrate)

    def test_the_strategizer_is_asked_for_full_graph_rag_everywhere(self):
        self.fake.add_project("robots")
        self.pipeline(write=True).run()
        body = next(b for m, p, b in self.fake.bodies if p.endswith("/rag-strategizer/analyze"))
        self.assertEqual(body, {"project": "robots", "complexity": "very_high", "categories": ["nav"]})

    def test_orchestration_is_scoped_to_the_category(self):
        self.fake.add_project("robots")
        self.pipeline(write=True, env={"AUTOGRAPH_REPLICAS": "2"}).run()
        body = next(b for m, p, b in self.fake.bodies if p.endswith("/v1/orchestrate"))
        self.assertEqual(body["categories"], ["nav"])
        self.assertEqual(body["replicas"], 2)

    def test_polling_is_reported_as_progress(self):
        self.fake.add_project("robots")
        self.pipeline(write=True).run()
        stages = {stage for stage, _ in self.progress}
        self.assertTrue({STAGE_UPLOAD, STAGE_CORPUS, STAGE_STRATEGIZE, STAGE_KG, STAGE_PLANGRAPH} <= stages)
        self.assertIn((STAGE_CORPUS, "55% Creating similarity edges..."), self.progress)

    def test_no_plangraph_builder_skips_the_last_stage(self):
        self.fake.add_project("robots")
        result = self.pipeline(write=True, build_plangraph=None).run()
        self.assertTrue(result.ok)
        self.assertEqual(self.statuses(result)[STAGE_PLANGRAPH], STATUS_SKIPPED)


class ResumeTest(PipelineCase):
    def test_a_rerun_skips_everything_already_built(self):
        self.fake.add_project("robots")
        self.pipeline(write=True).run()
        before = len(self.fake.writes())

        result = self.pipeline(write=True).run()
        self.assertTrue(result.ok, result.failed)
        statuses = self.statuses(result)
        for stage in (STAGE_UPLOAD, STAGE_CORPUS, STAGE_STRATEGIZE, STAGE_ONTOLOGY, STAGE_KG):
            self.assertEqual(statuses[stage], STATUS_SKIPPED, stage)
        # Nothing new was uploaded, built, patched or orchestrated.
        self.assertEqual(len(self.fake.writes()), before)
        # The PlanGraph write is idempotent per scope, so it runs again.
        self.assertEqual(statuses[STAGE_PLANGRAPH], STATUS_DONE)

    def test_a_run_interrupted_after_the_corpus_resumes_at_the_strategizer(self):
        self.fake.add_project("robots")
        svc = self.fake.service_for("robots")
        for book in BOOKS:
            self.fake.add_file("robots", "nav", book.name, book.content)
        svc.corpus.add("nav")

        result = self.pipeline(write=True).run()
        statuses = self.statuses(result)
        self.assertEqual(statuses[STAGE_UPLOAD], STATUS_SKIPPED)
        self.assertEqual(statuses[STAGE_CORPUS], STATUS_SKIPPED)
        self.assertEqual(statuses[STAGE_STRATEGIZE], STATUS_DONE)
        self.assertEqual(statuses[STAGE_KG], STATUS_DONE)

    def test_a_category_in_the_corpus_but_behind_its_files_is_appended(self):
        self.fake.add_project("robots")
        svc = self.fake.service_for("robots")
        svc.corpus.add("nav")  # in the corpus, but the files have been re-listed as new
        original = self.fake._overview

        def behind(service):
            payload = original(service)
            for row in payload["categories"]:
                row["needsCorpusUpdate"] = True
            return payload

        self.fake._overview = behind
        result = self.pipeline(write=True, build_plangraph=None).run()
        builds = [b for m, p, b in self.fake.bodies if p.endswith("/v1/corpus/builds")]
        self.assertEqual([b["incremental"] for b in builds], [False, True])
        self.assertEqual(self.statuses(result)[STAGE_CORPUS], STATUS_DONE)

    def test_nothing_to_orchestrate_is_a_success(self):
        self.fake.add_project("robots")
        self.pipeline(write=True).run()
        svc = self.fake.service_for("robots")
        original = self.fake._overview

        def says_new(service):
            payload = original(service)
            payload["knowledgeGraph"]["newCategories"] = ["nav"]
            return payload

        self.fake._overview = says_new
        result = self.pipeline(write=True).run()
        self.assertTrue(result.ok, result.failed)
        kg = next(s for s in result.stages if s.stage == STAGE_KG)
        self.assertEqual(kg.status, STATUS_SKIPPED)
        self.assertIn("nothing to orchestrate", kg.detail)
        self.assertEqual(len(svc.orchestrations), 1)


class DryRunTest(PipelineCase):
    def test_a_dry_run_writes_nothing(self):
        self.fake.add_project("robots")
        result = self.pipeline(write=False).run()
        self.assertTrue(result.ok)
        self.assertEqual(self.fake.writes(), [])
        self.assertEqual(self.fake.files, [])

    def test_stages_after_the_first_with_work_are_predicted(self):
        self.fake.add_project("robots")
        result = self.pipeline(write=False).run()
        statuses = self.statuses(result)
        self.assertEqual(statuses[STAGE_CONNECT], STATUS_DONE)
        self.assertEqual(statuses[STAGE_UPLOAD], STATUS_PLANNED)
        upload = next(s for s in result.stages if s.stage == STAGE_UPLOAD)
        self.assertEqual(upload.data["uploaded"], [b.name for b in BOOKS])
        later = [s for s in result.stages if s.stage not in (STAGE_CONNECT, STAGE_UPLOAD)]
        self.assertTrue(all(s.status == STATUS_PLANNED for s in later))
        self.assertTrue(all("once uploading the rulebooks has" in s.detail for s in later))

    def test_a_dry_run_over_a_built_project_says_so_stage_by_stage(self):
        self.fake.add_project("robots")
        self.pipeline(write=True).run()
        result = self.pipeline(write=False).run()
        statuses = self.statuses(result)
        for stage in (STAGE_UPLOAD, STAGE_CORPUS, STAGE_STRATEGIZE, STAGE_ONTOLOGY, STAGE_KG):
            self.assertEqual(statuses[stage], STATUS_SKIPPED)
        self.assertEqual(statuses[STAGE_PLANGRAPH], STATUS_PLANNED)


class GuardTest(PipelineCase):
    def test_new_rulebooks_for_a_built_category_need_a_rebuild(self):
        self.fake.add_project("robots")
        self.pipeline(write=True, books=BOOKS[:1]).run()
        writes = len(self.fake.writes())

        result = self.pipeline(write=True).run()
        self.assertFalse(result.ok)
        self.assertEqual(result.failed_stage, STAGE_UPLOAD)
        self.assertIn("rulebook_dock_at_charger.md", result.failed)
        self.assertIn("--rebuild", result.error.hint)
        self.assertEqual(len(self.fake.writes()), writes)

    def test_a_changed_rulebook_for_a_built_category_needs_a_rebuild(self):
        self.fake.add_project("robots")
        self.pipeline(write=True).run()
        edited = [Rulebook(BOOKS[0].name, BOOKS[0].content + b"one more line\n"), BOOKS[1]]
        result = self.pipeline(write=True, books=edited).run()
        self.assertEqual(result.failed_stage, STAGE_UPLOAD)
        self.assertIn(BOOKS[0].name, result.failed)

    def test_extra_files_already_in_the_category_are_named(self):
        self.fake.add_project("robots")
        self.fake.add_file("robots", "nav", "notes.md", b"x")
        result = self.pipeline(write=True, build_plangraph=None).run()
        upload = next(s for s in result.stages if s.stage == STAGE_UPLOAD)
        self.assertEqual(upload.data["extra"], ["notes.md"])
        self.assertIn("notes.md", upload.detail)

    def test_two_rulebooks_with_one_name_are_refused(self):
        with self.assertRaises(ConfigError):
            self.pipeline(books=[BOOKS[0], Rulebook(BOOKS[0].name, b"other")])

    def test_a_category_imported_under_another_ontology_needs_a_rebuild(self):
        self.fake.add_project("robots")
        for book in BOOKS:
            self.fake.add_file("robots", "nav", book.name, book.content)
        self.fake.build_everything("robots", "nav", ["DEVICE", "ROOM"])

        result = self.pipeline(write=True).run()
        self.assertEqual(result.failed_stage, STAGE_ONTOLOGY)
        self.assertIn("DEVICE", result.failed)
        self.assertIn("--rebuild", result.error.hint)
        self.assertFalse(any("/rag-strategizer/strategy/" in p for _, p in self.fake.writes()))


class RebuildTest(PipelineCase):
    def test_rebuild_deletes_the_category_with_its_files_then_builds_it_again(self):
        self.fake.add_project("robots")
        self.pipeline(write=True, books=BOOKS[:1]).run()

        result = self.pipeline(write=True, rebuild=True).run()
        self.assertTrue(result.ok, result.failed)
        delete = [
            (m, p) for m, p in self.fake.calls if m == "DELETE"
        ]
        self.assertEqual(delete, [("DELETE", "/autograph/s0001/v1/projects/robots/categories/nav")])
        # delete_files rides in the query string; as JSON it would be ignored.
        self.assertIsNone(next(b for m, p, b in self.fake.bodies if m == "DELETE"))
        self.assertEqual(sorted(f["name"] for f in self.fake.files), sorted(b.name for b in BOOKS))
        self.assertEqual(self.statuses(result)[STAGE_CORPUS], STATUS_DONE)
        self.assertEqual(len(self.fake.service_for("robots").orchestrations), 2)
        upload = next(s for s in result.stages if s.stage == STAGE_UPLOAD)
        self.assertIn("deleted category nav", upload.detail)

    def test_rebuild_of_a_category_that_was_never_built_just_builds(self):
        self.fake.add_project("robots")
        result = self.pipeline(write=True, rebuild=True).run()
        self.assertTrue(result.ok, result.failed)
        self.assertFalse(any(m == "DELETE" for m, _ in self.fake.calls))

    def test_a_dry_rebuild_only_says_what_it_would_delete(self):
        self.fake.add_project("robots")
        self.pipeline(write=True).run()
        writes = len(self.fake.writes())
        result = self.pipeline(write=False, rebuild=True).run()
        upload = next(s for s in result.stages if s.stage == STAGE_UPLOAD)
        self.assertEqual(upload.status, STATUS_PLANNED)
        self.assertIn("would delete category nav", upload.detail)
        self.assertEqual(len(self.fake.writes()), writes)


class LegacyLabelTest(PipelineCase):
    def test_encoded_module_labels_are_the_fallback(self):
        self.fake = FakePlatform(legacy_labels=True)
        self.fake.add_project("robots")
        result = self.pipeline(write=True).run()
        self.assertTrue(result.ok, result.failed)
        labels = [
            b["categories"] for m, p, b in self.fake.bodies
            if p.endswith(("/rag-strategizer/analyze", "/v1/orchestrate"))
        ]
        self.assertEqual(labels, [["nav"], ["robots_nav"], ["nav"], ["robots_nav"]])

    def test_encoding_escapes_underscores_inside_a_segment(self):
        self.assertEqual(encode_module("robots", "nav"), "robots_nav")
        self.assertEqual(encode_module("my_robot", "nav_2"), "my%5Frobot_nav%5F2")


class FailureTest(PipelineCase):
    def test_a_failed_corpus_build_stops_with_the_service_reason(self):
        self.fake.add_project("robots")
        self.fake.build_outcome = {"status": "failed", "error": "all files failed to parse"}
        result = self.pipeline(write=True).run()
        self.assertEqual(result.failed_stage, STAGE_CORPUS)
        self.assertIn("all files failed to parse", result.failed)
        self.assertEqual(self.statuses(result)[STAGE_CORPUS], STATUS_FAILED)
        self.assertNotIn(STAGE_STRATEGIZE, self.statuses(result))

    def test_a_corpus_with_no_clusters_stops_before_the_strategizer(self):
        self.fake.add_project("robots")
        self.fake.build_outcome = {"errorCode": "CORPUS_TOO_SMALL", "message": "no clusters"}
        result = self.pipeline(write=True).run()
        self.assertEqual(result.failed_stage, STAGE_CORPUS)
        self.assertIn("no clusters", result.failed)

    def test_a_partial_parse_is_a_warning_not_a_failure(self):
        self.fake.add_project("robots")
        self.fake.build_outcome = {"errorCode": "FILE_PARSER_PARTIAL_FAILURE", "message": "1 file failed"}
        result = self.pipeline(write=True).run()
        self.assertTrue(result.ok, result.failed)
        corpus = next(s for s in result.stages if s.stage == STAGE_CORPUS)
        self.assertIn("FILE_PARSER_PARTIAL_FAILURE", corpus.detail)

    def test_a_failed_strategizer_names_its_message(self):
        self.fake.add_project("robots")
        self.fake.strategizer_failure = "CORPUS_TOO_SMALL"
        result = self.pipeline(write=True).run()
        self.assertEqual(result.failed_stage, STAGE_STRATEGIZE)
        self.assertIn("CORPUS_TOO_SMALL", result.failed)

    def test_a_failed_orchestration_names_each_failed_partition(self):
        self.fake.add_project("robots")
        self.fake.orchestration_failure = "Insufficient data quality"
        result = self.pipeline(write=True).run()
        self.assertEqual(result.failed_stage, STAGE_KG)
        self.assertIn("robots_nav_0_a: Insufficient data quality", result.failed)
        self.assertEqual(self.plangraph_calls, [])

    def test_a_bridge_that_writes_nothing_fails_the_last_stage(self):
        self.fake.add_project("robots")
        result = self.pipeline(
            write=True,
            build_plangraph=lambda project, on_stage: {"ok": False, "failed": "no SKILL -> PRIMITIVE"},
        ).run()
        self.assertEqual(result.failed_stage, STAGE_PLANGRAPH)
        self.assertIn("no SKILL -> PRIMITIVE", result.failed)

    def test_a_build_that_never_finishes_times_out_with_its_last_state(self):
        self.fake = FakePlatform(polls=10_000)
        self.fake.add_project("robots")
        ticks = iter(range(0, 10_000_000, 60))
        pipeline = self.pipeline(write=True, env={"AUTOGRAPH_CORPUS_TIMEOUT": "300"})
        pipeline.clock = lambda: float(next(ticks))
        result = pipeline.run()
        self.assertEqual(result.failed_stage, STAGE_CORPUS)
        self.assertIn("did not finish within 5 minute(s)", result.failed)
        self.assertIn("Creating similarity edges", result.failed)

    def test_a_missing_project_without_provision_is_refused(self):
        result = self.pipeline(write=True).run()
        self.assertEqual(result.failed_stage, STAGE_CONNECT)
        self.assertIn("no AutoGraph project named 'robots'", result.failed)
        self.assertIn("--provision", result.error.hint)

    def test_a_service_whose_release_is_gone_is_named(self):
        self.fake.add_project("robots", routable=False)
        result = self.pipeline(write=True).run()
        self.assertEqual(result.failed_stage, STAGE_CONNECT)
        self.assertIn("arangodb-autograph-s0001 is no longer running", result.failed)


class ProvisionTest(PipelineCase):
    def test_an_installed_service_that_never_answers_is_not_redeployed(self):
        self.fake.add_project("robots", stuck=True)
        result = self.pipeline(write=True, provision=True).run()
        self.assertEqual(result.failed_stage, STAGE_CONNECT)
        self.assertIn("is installed for robots", result.failed)
        self.assertIn("ImagePullBackOff", result.error.hint)
        self.assertEqual(len(self.fake.services), 1)

    def test_a_dead_service_is_redeployed_with_the_projects_model_settings(self):
        self.fake.add_project("robots", routable=False)
        result = self.pipeline(write=True, provision=True).run()
        self.assertTrue(result.ok, result.failed)
        self.assertEqual(result.service, "/autograph/s0002")
        env = self.fake.services["arangodb-autograph-s0002"].env
        self.assertEqual(env["genai_project_name"], "robots")
        self.assertEqual(env["db_name"], "robots")
        self.assertEqual(env["chat_model"], "gpt-test")
        self.assertEqual(env["embedding_secret_profile_id"], "embed-profile")

    def test_a_new_project_is_created_and_takes_model_settings_from_another(self):
        self.fake.add_project("template", service=False)
        result = self.pipeline(
            project="fresh", write=True, provision=True, env={"AUTOGRAPH_MODEL_FROM": "template"}
        ).run()
        self.assertTrue(result.ok, result.failed)
        self.assertIn("fresh", self.fake.projects)
        env = next(s.env for s in self.fake.services.values() if s.project == "fresh")
        self.assertEqual(env["embedding_model"], "embed-test")
        connect = result.stages[0]
        self.assertIn("created the project and deployed", connect.detail)

    def test_a_platform_that_needs_an_fps_user_says_which_variable_to_set(self):
        self.fake.require_fps_user = True
        self.fake.db_users = None  # this login cannot list users, so none is found
        self.fake.add_project("robots", routable=False)
        result = self.pipeline(write=True, provision=True).run()
        self.assertEqual(result.failed_stage, STAGE_CONNECT)
        self.assertIsInstance(result.error, ConfigError)
        self.assertIn("AUTOGRAPH_FPS_RECOVERY_USERNAME", result.error.hint)

    def test_the_fps_user_is_sent_when_configured(self):
        self.fake.require_fps_user = True
        self.fake.add_project("robots", routable=False)
        result = self.pipeline(
            write=True, provision=True, env={"AUTOGRAPH_FPS_RECOVERY_USERNAME": "fps"}
        ).run()
        self.assertTrue(result.ok, result.failed)
        self.assertEqual(self.fake.services["arangodb-autograph-s0002"].env["fps_recovery_username"], "fps")

    def test_the_fps_user_is_found_in_the_database_when_none_is_named(self):
        self.fake.require_fps_user = True
        self.fake.add_project("robots", routable=False)
        result = self.pipeline(write=True, provision=True).run()
        self.assertTrue(result.ok, result.failed)
        env = self.fake.services["arangodb-autograph-s0002"].env
        self.assertEqual(env["fps_recovery_username"], "fps_recovery")

    def test_a_named_fps_user_without_rw_is_refused_before_deploying(self):
        self.fake.add_project("robots", routable=False)
        result = self.pipeline(write=True, provision=True, fps_user="fps-old").run()
        self.assertEqual(result.failed_stage, STAGE_CONNECT)
        self.assertIn("'none' access", result.failed)
        self.assertFalse(any(p.endswith("/service") and m == "POST" for m, p in self.fake.calls))

    def test_the_check_names_the_deploy_settings_and_catches_a_bad_user(self):
        self.fake.add_project("robots", routable=False)
        checked = self.pipeline(write=False, provision=True).run()
        self.assertTrue(checked.ok)
        self.assertIn("FPS recovery user fps_recovery", checked.stages[0].detail)
        self.assertIn("gpt-test", checked.stages[0].detail)
        bad = self.pipeline(write=False, provision=True, fps_user="fps-old").run()
        self.assertEqual(bad.failed_stage, STAGE_CONNECT)
        self.assertEqual(self.fake.writes(), [])

    def test_when_users_cannot_be_listed_the_check_warns(self):
        self.fake.db_users = None
        self.fake.add_project("robots", routable=False)
        checked = self.pipeline(write=False, provision=True).run()
        self.assertIn("no FPS recovery user found", checked.stages[0].detail)

    def test_no_model_settings_anywhere_is_a_configuration_error(self):
        result = self.pipeline(project="fresh", write=True, provision=True).run()
        self.assertEqual(result.failed_stage, STAGE_CONNECT)
        self.assertIsInstance(result.error, ConfigError)
        self.assertIn("AUTOGRAPH_MODEL_FROM", result.error.hint)

    def test_a_dry_provision_creates_nothing(self):
        self.fake.add_project("template", service=False)
        result = self.pipeline(
            project="fresh", write=False, provision=True, env={"AUTOGRAPH_MODEL_FROM": "template"}
        ).run()
        self.assertTrue(result.ok, result.failed)
        self.assertEqual(self.fake.writes(), [])
        self.assertNotIn("fresh", self.fake.projects)

    def test_a_check_without_model_settings_fails_before_the_build_would(self):
        result = self.pipeline(project="fresh", write=False, provision=True).run()
        self.assertEqual(result.failed_stage, STAGE_CONNECT)
        self.assertIsInstance(result.error, ConfigError)
        self.assertNotIn("fresh", self.fake.projects)

    def test_a_configured_service_path_bypasses_acp(self):
        svc = self.fake.add_project("robots")
        result = self.pipeline(write=True, env={"AUTOGRAPH_SERVICE_PATH": svc.base + "/"}).run()
        self.assertTrue(result.ok, result.failed)
        self.assertFalse(any("/project_by_name/" in p for _, p in self.fake.calls))


class ClientTest(unittest.TestCase):
    def test_an_expired_token_is_renewed_once_and_the_call_replayed(self):
        fake = FakePlatform()
        fake.add_project("robots")
        platform = Platform("https://platform.example", "robots", "root", "secret", transport=fake)
        platform.project("robots")
        fake.jwt = "jwt-2"  # the old token no longer validates
        self.assertIsNotNone(platform.project("robots"))
        self.assertEqual(fake.logins, 2)

    def test_bad_credentials_are_an_auth_error(self):
        platform = Platform("https://platform.example", "robots", "root", "nope", transport=FakePlatform())
        with self.assertRaises(AuthError):
            platform.project("robots")

    def test_a_non_success_carries_the_services_message(self):
        platform = Platform("https://platform.example", "robots", "root", "secret", transport=FakePlatform())
        with self.assertRaises(ApiError) as caught:
            platform.request("GET", "/nowhere")
        self.assertEqual(caught.exception.status, 404)
        self.assertIn("unknown path", caught.exception.message)

    def test_fields_are_read_in_either_casing(self):
        self.assertEqual(camel("corpus_build_id"), "corpusBuildId")
        self.assertEqual(field({"corpusBuildId": "cb_1"}, "corpus_build_id"), "cb_1")
        self.assertEqual(field({"corpus_build_id": "cb_2"}, "corpus_build_id"), "cb_2")
        self.assertEqual(field({}, "status", "x"), "x")

    def test_multipart_repeats_the_scope_field(self):
        body, content_type = encode_multipart(
            [("name", "a.md"), ("scope", "robots"), ("scope", "nav")], "file", "a.md", b"hello"
        )
        self.assertTrue(content_type.startswith("multipart/form-data; boundary="))
        self.assertEqual(body.count(b'name="scope"'), 2)
        self.assertIn(b'filename="a.md"', body)
        self.assertIn(b"\r\n\r\nhello\r\n", body)

    def test_only_the_path_of_an_internal_service_url_is_kept(self):
        self.assertEqual(
            service_path("https://deployment.ns.svc:8529/autograph/uqabo/"), "/autograph/uqabo"
        )
        self.assertEqual(service_path("/autograph/uqabo"), "/autograph/uqabo")

    def test_file_listing_pages_and_filters_to_the_exact_scope(self):
        fake = FakePlatform()
        for i in range(1005):
            fake.add_file("robots", "nav" if i % 2 else "other", f"f{i}.md", b"x")
        fake.add_file("robots", "", "root.md", b"x")
        platform = Platform("https://platform.example", "robots", "root", "secret", transport=fake)
        files = platform.files(["robots", "nav"])
        self.assertEqual(len(files), 502)
        self.assertEqual(sum(1 for m, p in fake.calls if p.endswith("/rag-input")), 2)


class ConfigTest(unittest.TestCase):
    def test_defaults_suit_the_bridge(self):
        config = load_pipeline_config(ENV)
        self.assertEqual(config.complexity, "very_high")
        self.assertEqual(list(config.ontology), ONTOLOGY)
        self.assertEqual(config.replicas, 1)
        self.assertNotIn("secret", json.dumps(config.describe()))

    def test_an_unknown_complexity_is_refused(self):
        with self.assertRaises(ConfigError):
            load_pipeline_config({**ENV, "AUTOGRAPH_COMPLEXITY": "70%"})

    def test_an_ontology_without_skill_and_primitive_is_refused(self):
        with self.assertRaises(ConfigError):
            load_pipeline_config({**ENV, "AUTOGRAPH_ONTOLOGY": "OBJECT,STATE"})

    def test_credentials_are_required(self):
        with self.assertRaises(ConfigError) as caught:
            load_pipeline_config({"ARANGO_URL": "x", "ARANGO_DB": "y"})
        self.assertIn("ARANGO_AUTH_TOKEN", caught.exception.message)

    def test_explicit_model_settings_are_collected(self):
        config = load_pipeline_config({**ENV, "AUTOGRAPH_CHAT_MODEL": "m1", "AUTOGRAPH_EMBEDDING_MODEL": "e1"})
        self.assertEqual(config.model_env, {"chat_model": "m1", "embedding_model": "e1"})


class CliTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        for book in BOOKS:
            (self.dir / book.name).write_bytes(book.content)
        self.fake = FakePlatform()
        self.fake.add_project("robots")

    def tearDown(self):
        self.tmp.cleanup()

    def invoke(self, **kwargs):
        out, err = io.StringIO(), io.StringIO()
        platform = Platform("https://platform.example", "robots", "root", "secret", transport=self.fake)
        code = cli_run(
            [str(self.dir)], project="robots", category="nav", stdout=out, stderr=err,
            platform=platform, env=ENV, build_plangraph=plangraph_ok, **kwargs,
        )
        return code, out.getvalue(), err.getvalue()

    def test_a_plain_run_is_a_dry_run(self):
        code, out, _ = self.invoke()
        self.assertEqual(code, 0)
        self.assertIn("DRY RUN", out)
        self.assertEqual(self.fake.writes(), [])

    def test_write_builds_and_reports_each_stage(self):
        code, out, err = self.invoke(write=True)
        self.assertEqual(code, 0, out)
        for stage in STAGES:
            self.assertIn(stage, out)
        self.assertIn("done.", out)
        self.assertIn("building the corpus graph: build cb_1 started", err)

    def test_json_output_carries_every_stage(self):
        code, out, _ = self.invoke(write=True, output_format="json")
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual([s["stage"] for s in payload["stages"]], list(STAGES))

    def test_a_failed_stage_exits_ten_with_the_fix(self):
        self.fake.orchestration_failure = "boom"
        code, out, _ = self.invoke(write=True)
        self.assertEqual(code, 10)
        self.assertIn("stopped at: building the knowledge graph", out)

    def test_no_rulebooks_is_a_configuration_error(self):
        import tempfile

        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(ConfigError):
                cli_run([empty], project="robots", env=ENV, platform=Platform("x", "y", transport=self.fake))


if __name__ == "__main__":
    unittest.main()
