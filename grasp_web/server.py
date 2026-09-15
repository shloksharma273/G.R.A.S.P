"""A small HTTP server for the planning UI.

Standard library only. The project has kept its required dependency list at one
package on purpose, and a JSON API over four routes does not justify a web
framework. `ThreadingHTTPServer` is enough: a request spends its time in one
ArangoDB traversal and at most one model call, and those are independent.

Routes:

    GET  /                       the planning page
    GET  /generate               the rulebook generator page
    GET  /static/<file>          their assets
    GET  /api/health             what the planner is connected to
    GET  /api/skills             what it can plan
    POST /api/plan               a command in, a plan or a clarification out
    GET  /api/generate/health    what the generator is configured to do
    GET  /api/generate/library   rulebooks already on disk
    POST /api/generate           start a generation, get a job id
    GET  /api/generate/<job>     poll that job

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
from .api import PlannerService
from .generate import GeneratorService

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

        if path == "/":
            self._static("index.html")
        elif path == "/generate":
            self._static("generate.html")
        elif path.startswith("/static/"):
            self._static(path[len("/static/") :])
        elif path == "/api/health":
            self._json(service.health())
        elif path == "/api/skills":
            self._json({"skills": service.skills()})
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
        if path not in ("/api/plan", "/api/generate"):
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
            )
            self._json(started, 400 if started.get("error") else 200)
            return

        service: PlannerService = self.server.service  # type: ignore[attr-defined]
        use_llm = payload.get("use_llm")
        self._json(
            service.plan(
                str(payload.get("command", "")),
                use_llm=bool(use_llm) if use_llm is not None else None,
            )
        )


def make_server(
    service: PlannerService,
    host: str = "127.0.0.1",
    port: int = 8080,
    quiet: bool = False,
    generator: GeneratorService | None = None,
) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((host, port), Handler)
    server.service = service  # type: ignore[attr-defined]
    server.generator = generator or GeneratorService()  # type: ignore[attr-defined]
    server.quiet = quiet  # type: ignore[attr-defined]
    server.daemon_threads = True
    return server
