"""A small HTTP server for the planning UI.

Standard library only. The project has kept its required dependency list at one
package on purpose, and a JSON API over four routes does not justify a web
framework. `ThreadingHTTPServer` is enough: a request spends its time in one
ArangoDB traversal and at most one model call, and those are independent.

Routes:

    GET  /                       the planning page
    GET  /projects               the project list: discover, build, view, ask
    GET  /generate               the rulebook generator page
    GET  /repo                   the repository generator page
    GET  /static/<file>          their assets
    GET  /api/health             what the planner is connected to
    GET  /api/skills             what it can plan
    POST /api/plan               a command in, a plan or a clarification out
    GET  /api/generate/health    what the generator is configured to do
    GET  /api/generate/library   rulebooks already on disk
    POST /api/generate           start a generation, get a job id
    GET  /api/generate/<job>     poll that job
    POST /api/repo/tree          a docs repo in, its procedure pages out
    POST /api/repo/generate      chosen pages in, a generation job out
    GET  /api/projects           every project in the database and its stage
    POST /api/projects/build     build one project's PlanGraph, get a job id
    GET  /api/projects/build/<j> poll that build
    GET  /api/kg/health          whether rulebooks can be built into a PlanGraph
    POST /api/kg/build           rulebooks -> AutoGraph -> PlanGraph, get a job id
    GET  /api/kg/build/<job>     poll that run, stage by stage

Generation is slow enough to need a job: the POST starts one and returns
immediately, and the page polls for the stage it has reached.

Bound to localhost by default. This is a developer tool over a database with
write credentials in its environment, so it should not be listening on a public
interface without someone deciding that deliberately.
"""

from __future__ import annotations

import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from . import __version__
from .api import PlannerRegistry, PlannerService
from .builder import BuildService
from .generate import STAGE_READING_REPO, GeneratorService
from .kgbuild import KnowledgeBuildService
from .repo import RepoBrowser, RepoError

STATIC = Path(__file__).resolve().parent / "static"

#: Refuse a body larger than this rather than reading it into memory.
MAX_BODY = 64 * 1024


class Handler(BaseHTTPRequestHandler):
    server_version = f"grasp-web/{__version__}"
    service: PlannerService  # set on the server, read through self.server

    # --- plumbing -----------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        """One tidy line per request instead of BaseHTTPRequestHandler's default."""
        if not getattr(self.server, "quiet", False):
            print(f"  {self.command} {self.path} -> {args[1] if len(args) > 1 else ''}")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        # The API is same-origin only; no CORS header is issued on purpose.
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, payload: Any, status: int = 200) -> None:
        self._send(
            status,
            json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _query(self) -> dict[str, str]:
        from urllib.parse import parse_qs, urlparse

        raw = parse_qs(urlparse(self.path).query)
        return {key: values[0] for key, values in raw.items() if values}

    def _planner(self, project: str = "") -> PlannerService:
        """The planner for one project, or the server's default.

        A registry is optional so a server built the old way - one service, one
        project - still works exactly as it did.
        """
        registry: PlannerRegistry | None = getattr(self.server, "registry", None)
        if project and registry is not None:
            return registry.for_project(project)
        return self.server.service  # type: ignore[attr-defined]

    def _static(self, name: str) -> None:
        # Resolve inside STATIC so a crafted path cannot escape the directory.
        target = (STATIC / name).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            self._json({"error": "not found"}, 404)
            return
        content_type, _ = mimetypes.guess_type(target.name)
        self._send(200, target.read_bytes(), content_type or "application/octet-stream")

    # --- routes -------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - the base class names it
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        service: PlannerService = self.server.service  # type: ignore[attr-defined]

        generator: GeneratorService = self.server.generator  # type: ignore[attr-defined]

        query = self._query()
        builder: BuildService = self.server.builder  # type: ignore[attr-defined]

        if path == "/":
            self._static("index.html")
        elif path == "/projects":
            self._static("projects.html")
        elif path == "/generate":
            self._static("generate.html")
        elif path == "/repo":
            self._static("repo.html")
        elif path.startswith("/static/"):
            self._static(path[len("/static/") :])
        elif path == "/api/health":
            self._json(self._planner(query.get("project", "")).health())
        elif path == "/api/skills":
            self._json({"skills": self._planner(query.get("project", "")).skills()})
        elif path == "/api/projects":
            self._json(builder.projects())
        elif path.startswith("/api/projects/build/"):
            job = builder.status(path[len("/api/projects/build/") :])
            self._json(job if job else {"error": "no such build"}, 200 if job else 404)
        elif path == "/api/kg/health":
            self._json(self.server.kgbuild.health())  # type: ignore[attr-defined]
        elif path.startswith("/api/kg/build/"):
            run = self.server.kgbuild.status(path[len("/api/kg/build/") :])  # type: ignore[attr-defined]
            self._json(run if run else {"error": "no such run"}, 200 if run else 404)
        elif path == "/api/generate/health":
            self._json(generator.health())
        elif path == "/api/generate/library":
            self._json({"rulebooks": generator.rulebooks()})
        elif path.startswith("/api/generate/"):
            job = generator.status(path[len("/api/generate/") :])
            self._json(job if job else {"error": "no such job"}, 200 if job else 404)
        else:
            self._json({"error": f"no route for {path}"}, 404)

    do_HEAD = do_GET

    def do_POST(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0].rstrip("/") or "/"
        if path not in (
            "/api/plan",
            "/api/generate",
            "/api/repo/tree",
            "/api/repo/generate",
            "/api/projects/build",
            "/api/kg/build",
        ):
            self._json({"error": f"no route for {path}"}, 404)
            return

        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            self._json({"kind": "error", "message": "bad Content-Length"}, 400)
            return
        if length > MAX_BODY:
            self._json({"kind": "error", "message": "request too large"}, 413)
            return

        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError as error:
            self._json({"kind": "error", "message": f"invalid JSON ({error})"}, 400)
            return
        if not isinstance(payload, dict):
            self._json({"kind": "error", "message": "expected a JSON object"}, 400)
            return

        if path == "/api/generate":
            generator: GeneratorService = self.server.generator  # type: ignore[attr-defined]
            started = generator.start(
                url=str(payload.get("url", "")),
                transcript_text=str(payload.get("transcript", "")),
                split=bool(payload.get("split")),
            )
            self._json(started, 400 if started.get("error") else 200)
            return

        if path == "/api/kg/build":
            kgbuild: KnowledgeBuildService = self.server.kgbuild  # type: ignore[attr-defined]
            started = kgbuild.start(payload)
            registry: PlannerRegistry | None = getattr(self.server, "registry", None)
            if registry is not None and payload.get("write") and not started.get("error"):
                # The PlanGraph this run writes is exactly what a cached planner holds.
                registry.invalidate(str(payload.get("project", "")))
            self._json(started, 400 if started.get("error") else 200)
            return

        if path.startswith("/api/repo/"):
            self._repo(path, payload)
            return

        if path == "/api/projects/build":
            builder: BuildService = self.server.builder  # type: ignore[attr-defined]
            started = builder.start(
                project=str(payload.get("project", "")),
                dry_run=bool(payload.get("dry_run")),
            )
            # A build rewrites exactly what a cached planner holds, so the cache
            # for that project is dropped the moment one is accepted.
            registry: PlannerRegistry | None = getattr(self.server, "registry", None)
            if registry is not None and not started.get("error"):
                registry.invalidate(str(payload.get("project", "")))
            self._json(started, 400 if started.get("error") else 200)
            return

        service = self._planner(str(payload.get("project", "")))
        use_llm = payload.get("use_llm")
        self._json(
            service.plan(
                str(payload.get("command", "")),
                use_llm=bool(use_llm) if use_llm is not None else None,
            )
        )

    def _repo(self, path: str, payload: dict[str, Any]) -> None:
        """The two repository routes, sharing one error translation.

        A `RepoError` here is an ordinary outcome - a typo, a private repo, a used
        up rate limit - so it is reported as the actionable message it already
        carries rather than as a server fault.
        """
        browser: RepoBrowser = self.server.browser  # type: ignore[attr-defined]
        url = str(payload.get("url", ""))

        try:
            mode = str(payload.get("mode", "docs"))
            if path == "/api/repo/tree":
                self._json(browser.tree(url, mode=mode))
                return

            paths = payload.get("paths")
            if not isinstance(paths, list):
                self._json({"error": "expected a list of paths"}, 400)
                return
            chosen = [str(p) for p in paths]
            ref = str(payload.get("ref", ""))

            # Fail fast on an unreadable request, before a job exists to report
            # it: the browser should be told "no files chosen" by the POST, not a
            # second later by a job that errored.
            reference = browser.parse_check(url, chosen)

            generator: GeneratorService = self.server.generator  # type: ignore[attr-defined]
            started = generator.start(
                prepare=lambda: browser.document(url, chosen, ref=ref, mode=mode),
                prepare_stage=STAGE_READING_REPO,
                source_label=f"{reference} \u2014 {len(chosen)} file(s)",
                register="code" if mode == "code" else "manual",
                split=bool(payload.get("split")),
            )
            self._json(started, 400 if started.get("error") else 200)
        except RepoError as error:
            self._json({"error": error.message, "hint": error.hint}, 400)
        except Exception as error:  # a bad repo must not take the server down
            self._json({"error": f"{error.__class__.__name__}: {error}"}, 500)


def make_server(
    service: PlannerService,
    host: str = "127.0.0.1",
    port: int = 8080,
    quiet: bool = False,
    generator: GeneratorService | None = None,
    browser: RepoBrowser | None = None,
    builder: BuildService | None = None,
    registry: PlannerRegistry | None = None,
    kgbuild: KnowledgeBuildService | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.service = service  # type: ignore[attr-defined]
    server.registry = registry  # type: ignore[attr-defined]
    server.builder = builder or BuildService()  # type: ignore[attr-defined]
    server.generator = generator or GeneratorService()  # type: ignore[attr-defined]
    server.kgbuild = kgbuild or KnowledgeBuildService(  # type: ignore[attr-defined]
        job_files=server.generator.rulebook_files,  # type: ignore[attr-defined]
        library_files=server.generator.library_files,  # type: ignore[attr-defined]
    )
    server.browser = browser or RepoBrowser()  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    server.daemon_threads = True
    return server
