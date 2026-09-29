"""Rulebooks to PlanGraph from the browser: the AutoGraph pipeline as a polled job.

`autograph_pipeline` uploads a module of rulebooks and drives AutoGraph through
the corpus graph, the strategies, the ontology and the knowledge graph, then runs
the bridge. That takes anywhere from minutes to an hour, so the POST starts it
and the page polls - and each poll carries every stage's status plus the latest
thing the running stage reported, so the page can show where the build is
rather than a spinner.

Two kinds of run, and the page always does them in this order:

* a **check** (`write: false`) reads the project and says what each stage would
  do. It uploads nothing and writes nothing.
* a **build** (`write: true`) does it.

Guarded like the bridge build next door: one run per project at a time, because
two would race each other's uploads and orchestrations, and nothing runs unless
the request names the rulebooks by a generation job or by file name - the
browser never uploads markdown itself, so what reaches AutoGraph is exactly what
the gate graded.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from kg_read_harness.errors import ConfigError, HarnessError

from .jobs import STATE_DONE, STATE_ERROR, Job, JobStore

#: Project and category names end up in collection names, File Manager scopes
#: and URL paths. AutoGraph accepts more than this; nothing here needs more.
NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")

#: An ArangoDB user name, as the File Parsing recovery user is named.
USER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@-]{0,63}$")

#: The most recent progress lines a poll carries, so the page can show a log.
LOG_LINES = 40

STAGE_QUEUED = "starting the pipeline"


@dataclass
class KnowledgeBuildService:
    """Runs `autograph_pipeline` in the background and reports on it."""

    #: (name, markdown) pairs for the chosen rulebooks, from a generation job.
    job_files: Callable[[str, list[str]], list[tuple[str, str]]] | None = None
    #: (name, markdown) pairs for rulebooks already on disk.
    library_files: Callable[[list[str]], list[tuple[str, str]]] | None = None
    #: Test seams: a platform to talk to, and the PlanGraph stage.
    platform: Any = None
    build_plangraph: Any = None
    env: Any = None
    sleep: Any = None
    _jobs: JobStore = field(default_factory=JobStore)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _busy: set[str] = field(default_factory=set)

    # --- configuration ------------------------------------------------------

    def config(self):
        from autograph_pipeline.config import load_pipeline_config

        return load_pipeline_config(self.env)

    def health(self) -> dict[str, Any]:
        """Whether a build can be started, and with what defaults."""
        from autograph_pipeline.pipeline import STAGES

        try:
            config = self.config()
        except ConfigError as error:
            return {"available": False, "reason": error.message, "hint": error.hint}
        return {
            "available": True,
            "endpoint": config.url,
            "database": config.database,
            "complexity": config.complexity,
            "ontology": list(config.ontology),
            "stages": list(STAGES),
            "provision_ready": bool(config.fps_recovery_username),
        }

    # --- starting a run -----------------------------------------------------

    def start(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Begin a check or a build. Returns the job to poll, or a refusal."""
        project = str(payload.get("project") or "").strip()
        category = str(payload.get("category") or "").strip()
        for label, value in (("project", project), ("category", category)):
            if not NAME.match(value):
                return {
                    "error": f"{label} {value!r} is not a usable name.",
                    "hint": "start with a letter; use letters, digits, - and _ (64 at most).",
                }

        files = payload.get("files")
        if not isinstance(files, list) or not files or not all(isinstance(f, str) for f in files):
            return {"error": "no rulebooks chosen.", "hint": "tick at least one rulebook."}

        try:
            rulebooks = self._rulebooks(payload, files)
            config = self.config()
        except HarnessError as error:
            return {"error": error.message, "hint": error.hint}

        fps_user = str(payload.get("fps_user") or "").strip()
        if fps_user and not USER.match(fps_user):
            return {"error": f"{fps_user!r} is not a usable user name.", "hint": "leave it empty to find one."}

        write = bool(payload.get("write"))
        options = {
            "write": write,
            "rebuild": bool(payload.get("rebuild")),
            "provision": bool(payload.get("provision")),
            "fps_user": fps_user,
            "plangraph": payload.get("plangraph", True) is not False,
        }

        with self._lock:
            if project in self._busy:
                return {
                    "error": f"a run for {project} is already going.",
                    "hint": "wait for it to finish; two would race each other's uploads.",
                }
            self._busy.add(project)

        from autograph_pipeline.pipeline import STAGES

        job = self._jobs.create(stage=STAGE_QUEUED, source=project)
        job.detail = {
            "project": project,
            "category": category,
            "files": [r.name for r in rulebooks],
            **options,
            "stages": [{"stage": stage, "status": "pending", "detail": ""} for stage in STAGES],
            "current": "",
            "message": "",
            "log": [],
        }
        return self._jobs.run(
            job, lambda j: self._run(j, config, project, category, rulebooks, options)
        ).to_dict()

    def _rulebooks(self, payload: dict[str, Any], files: list[str]):
        from autograph_pipeline.pipeline import Rulebook

        source = str(payload.get("job") or "").strip()
        if source:
            if self.job_files is None:
                raise HarnessError("this server cannot read generation jobs.")
            pairs = self.job_files(source, files)
        else:
            if self.library_files is None:
                raise HarnessError("this server cannot read rulebooks from disk.")
            pairs = self.library_files(files)
        return [Rulebook(name=name, content=markdown.encode("utf-8")) for name, markdown in pairs]

    def _run(self, job: Job, config: Any, project: str, category: str, rulebooks, options) -> None:
        from autograph_pipeline.client import Platform
        from autograph_pipeline.pipeline import Pipeline
        from autograph_pipeline.plangraph import bridge_builder

        try:
            platform = self.platform or Platform(
                url=config.url,
                database=config.database,
                username=config.username,
                password=config.password,
                token=config.auth_token,
            )
            builder = None
            if options["plangraph"]:
                builder = self.build_plangraph or bridge_builder(self.env)

            def on_stage(stage: str, report: Any) -> None:
                rows = job.detail["stages"]
                row = next((r for r in rows if r["stage"] == stage), None)
                if row is None:
                    return
                if report is None:
                    row["status"] = "running"
                    job.detail["current"] = stage
                    job.detail["message"] = ""
                    job.stage = stage
                else:
                    row.update(status=report.status, detail=report.detail)

            def on_progress(stage: str, message: str) -> None:
                job.detail["message"] = message
                job.stage = f"{stage}: {message}"
                log = job.detail["log"]
                log.append({"stage": stage, "message": message})
                del log[:-LOG_LINES]

            kwargs = {"sleep": self.sleep} if self.sleep is not None else {}
            result = Pipeline(
                platform,
                config,
                project,
                category,
                rulebooks,
                write=options["write"],
                rebuild=options["rebuild"],
                provision=options["provision"],
                fps_user=options["fps_user"] or None,
                build_plangraph=builder,
                on_stage=on_stage,
                on_progress=on_progress,
                **kwargs,
            ).run()

            payload = result.to_dict()
            if result.error is not None:
                payload["hint"] = result.error.hint or ""
            job.result = payload
            job.detail["current"] = ""
            job.stage = "finished" if result.ok else f"stopped at {result.failed_stage}"
            job.state = STATE_DONE
        except HarnessError as error:
            job.state = STATE_ERROR
            job.error = error.render()
        finally:
            with self._lock:
                self._busy.discard(project)

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.status(job_id)


__all__ = ["KnowledgeBuildService", "NAME"]
