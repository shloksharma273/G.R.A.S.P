"""The build half of the API — a project's knowledge graph becomes its PlanGraph.

`bridge.py` runs the five stations and knows nothing about threads or HTTP; this
runs it as a polled job and serializes what it did. The split is the same one
`generate.py` makes against `rulebook_generator`, and for the same reason: the
pipeline stays testable without a server, and the server holds no pipeline logic
that could drift from the CLIs.

**This is the only writing service in `grasp_web`.** Two things guard it, and
neither is decoration:

* a build is refused unless the caller passed the project's current state back,
  so "rebuild" is always a decision taken against what is actually there rather
  than against a listing that may be minutes old;
* one build at a time per project, because two concurrent scoped writes to the
  same collections would interleave a purge with a write.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

from kg_read_harness.errors import ConfigError, HarnessError

from . import bridge
from .jobs import STATE_DONE, Job, JobStore
from .projects import discover, find, graph_url

STAGE_STARTING = "starting the bridge"


@dataclass
class BuildService:
    """Runs bridge builds in the background and reports on them."""

    connect: Any = None
    provider: Any = None
    _jobs: JobStore = field(default_factory=JobStore)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _busy: set[str] = field(default_factory=set)

    # --- configuration ------------------------------------------------------

    def config(self):
        from kg_read_harness.config import load_config

        return load_config()

    def db(self):
        """A handle on the one configured database."""
        from kg_read_harness.client import connect

        if self.connect is not None:
            return self.connect()
        return connect(self.config())

    def health(self) -> dict[str, Any]:
        """The database this server is pointed at, or why it cannot be reached."""
        try:
            config = self.config()
        except ConfigError as error:
            return {"available": False, "reason": error.message, "hint": error.hint}
        try:
            self.db()
        except HarnessError as error:
            return {
                "available": False,
                "reason": error.message,
                "hint": error.hint,
                "database": config.database,
                "endpoint": config.url,
            }
        return {
            "available": True,
            "database": config.database,
            "endpoint": config.url,
            "writes": self._can_write(),
        }

    @staticmethod
    def _can_write() -> bool:
        """Whether Station 5 has credentials to write with."""
        try:
            from plangraph_writer.config import load_writer_config

            load_writer_config(dry_run=True)
            return True
        except ConfigError:
            return False

    # --- reads --------------------------------------------------------------

    def projects(self) -> dict[str, Any]:
        """Every project in the database, with a deep link to each built graph."""
        try:
            config = self.config()
            db = self.db()
        except HarnessError as error:
            return {"error": error.message, "hint": error.hint, "projects": []}

        rows = []
        for project in discover(db, type_field=config.entity_type_field):
            row = project.to_dict()
            row["building"] = project.name in self._building()
            row["graphs"] = {
                name: graph_url(config.url, config.database, f"{project.name}{suffix}")
                for name, suffix, present in (
                    ("corpus", "_CorpusGraph", project.has_corpus),
                    ("kg", "_kg", project.has_kg),
                    ("plangraph", "_PlanGraph", project.has_plangraph),
                )
                if present
            }
            rows.append(row)

        return {
            "database": config.database,
            "endpoint": config.url,
            "writes": self._can_write(),
            "projects": rows,
        }

    def _building(self) -> set[str]:
        with self._lock:
            return set(self._busy)

    # --- the one write ------------------------------------------------------

    def start(self, project: str, dry_run: bool = False) -> dict[str, Any]:
        """Begin a build. Returns the job to poll, or a refusal to show."""
        name = (project or "").strip()
        if not name:
            return {"error": "No project named."}

        try:
            config = self.config()
            db = self.db()
        except HarnessError as error:
            return {"error": error.message, "hint": error.hint}

        entry = find(db, name, type_field=config.entity_type_field)
        if entry is None:
            return {
                "error": f"{name!r} is not a project in {config.database}.",
                "hint": "reload the page; the database may have changed since it was listed.",
            }
        if not entry.buildable:
            return {"error": f"{name} cannot be built.", "hint": entry.blocked_reason}
        if not dry_run and not self._can_write():
            return {
                "error": "no write credentials are configured, so nothing can be written.",
                "hint": "set ARANGO_WRITE_USERNAME / ARANGO_WRITE_PASSWORD, or give the "
                "configured user write access to this database.",
            }

        with self._lock:
            if name in self._busy:
                return {
                    "error": f"a build of {name} is already running.",
                    "hint": "wait for it to finish; two builds would interleave their writes.",
                }
            self._busy.add(name)

        job = self._jobs.create(stage=STAGE_STARTING, source=name)
        job.detail = {
            "project": name,
            "dry_run": dry_run,
            "replacing": list(entry.scopes),
            "stations": [],
        }
        return self._jobs.run(job, lambda j: self._run(j, name, dry_run)).to_dict()

    def _run(self, job: Job, project: str, dry_run: bool) -> None:
        try:
            db = self.db()

            def on_stage(stage: str, report: Any) -> None:
                job.stage = stage
                if report is not None:
                    job.detail["stations"] = job.detail.get("stations", []) + [
                        report.to_dict()
                    ]

            result = bridge.build(
                project,
                db,
                dry_run=dry_run,
                provider=self.provider,
                write_db=self._write_db(project, dry_run),
                on_stage=on_stage,
            )
            payload = result.to_dict()
            config = self.config()
            payload["graph_url"] = (
                graph_url(config.url, config.database, result.graph)
                if result.ok and not dry_run
                else ""
            )
            job.result = payload
            job.state = STATE_DONE
        finally:
            with self._lock:
                self._busy.discard(project)

    def _write_db(self, project: str, dry_run: bool) -> Any:
        """A handle for Station 5, which may write as a different user.

        `ARANGO_WRITE_USERNAME` exists because the reading user can be read-only.
        Ignoring it here would write with Station 1's credentials and fail at the
        database rather than at the configuration.
        """
        from kg_read_harness.client import connect

        config = bridge.writer_config(project, dry_run=dry_run)
        if config.arango.username == self.config().username:
            return None  # same credentials; the read handle is fine
        return connect(config.arango)

    def status(self, job_id: str) -> dict[str, Any] | None:
        return self._jobs.status(job_id)
