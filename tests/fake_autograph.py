"""An in-memory platform: auth, ACP, File Manager and one AutoGraph per project.

It is a transport, not a mock of the client - `Platform` sends real HTTP-shaped
requests (paths, query strings, JSON and multipart bodies) and this answers them
the way the services do, including the parts that shape the pipeline:

* responses in camelCase, as the gateway emits them;
* a full corpus build of an already-built category refused with
  409 REBUILD_NOT_ALLOWED;
* async operations that take a few polls to finish;
* an orchestration with nothing stale refused with 409 "Nothing to orchestrate";
* ACP keeping a project's service record after its release is deleted.

`legacy_labels=True` reproduces older services that matched the strategizer and
orchestrator against the encoded module only.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from typing import Any

JWT = "jwt-1"


def _encode(project: str, category: str) -> str:
    return "_".join(p.replace("_", "%5F") for p in (project, category))


@dataclass
class Service:
    project: str
    service_id: str
    status: str = "DEPLOYED"
    routable: bool = True
    env: dict[str, str] = field(default_factory=dict)
    corpus: set[str] = field(default_factory=set)  # categories built into the corpus
    strategies: dict[str, dict[str, Any]] = field(default_factory=dict)  # cluster -> row
    kg: set[str] = field(default_factory=set)  # imported partition ids
    builds: dict[str, dict[str, Any]] = field(default_factory=dict)
    jobs: dict[str, dict[str, Any]] = field(default_factory=dict)
    orchestrations: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def base(self) -> str:
        return f"/autograph/{self.service_id.rsplit('-', 1)[-1]}"


class FakePlatform:
    def __init__(
        self,
        *,
        polls: int = 2,
        legacy_labels: bool = False,
        strategizer_types: tuple[str, ...] = ("DEVICE", "LOCATION"),
    ) -> None:
        self.polls = polls
        self.legacy_labels = legacy_labels
        self.strategizer_types = list(strategizer_types)
        self.require_fps_user = False
        #: Database users and their access to the project database; None = the
        #: login may not list users.
        self.db_users: dict[str, str] | None = {"root": "rw", "fps_recovery": "rw", "fps-old": "none"}
        self.jwt = JWT
        self.logins = 0
        self.projects: dict[str, dict[str, Any]] = {}
        self.services: dict[str, Service] = {}
        self.files: list[dict[str, Any]] = []
        self.calls: list[tuple[str, str]] = []
        self.bodies: list[tuple[str, str, Any]] = []
        # failure injection
        self.build_outcome: dict[str, Any] = {}
        self.strategizer_failure = ""
        self.orchestration_failure = ""
        self._ids = 0

    # --- setup helpers -----------------------------------------------------

    def add_project(
        self, name: str, *, service: bool = True, routable: bool = True, stuck: bool = False
    ) -> Service | None:
        self.projects[name] = {
            "projectName": name,
            "projectType": "autograph",
            "projectMetadata": {},
            "modelSettings": {
                "chatApiProvider": "openai",
                "embeddingApiProvider": "openai",
                "chatModel": "gpt-test",
                "embeddingModel": "embed-test",
                "chatSecretProfileId": "chat-profile",
                "embeddingSecretProfileId": "embed-profile",
            },
        }
        if not service:
            return None
        svc = self._new_service(name)
        svc.routable = routable
        svc.stuck = stuck  # installed, but its pod never answers
        return svc

    def _new_service(self, project: str, env: dict[str, str] | None = None) -> Service:
        self._ids += 1
        service_id = f"arangodb-autograph-s{self._ids:04d}"
        svc = Service(project=project, service_id=service_id, env=dict(env or {}))
        self.services[service_id] = svc
        self.projects[project]["projectMetadata"] = {
            "autographService": {
                "serviceId": service_id,
                "serviceUrl": f"https://deployment.internal.svc:8529{svc.base}/",
            }
        }
        return svc

    def add_file(self, project: str, category: str, name: str, content: bytes) -> None:
        self.files = [f for f in self.files if not (f["name"] == name and f["scope"] == [project, category])]
        self.files.append(
            {"id": f"rag-input-{name}", "name": name, "scope": [project, category], "size": len(content)}
        )

    def service_for(self, project: str) -> Service:
        return next(s for s in self.services.values() if s.project == project and s.routable)

    def build_everything(self, project: str, category: str, entity_types: list[str]) -> None:
        """A category already built end to end, as the AutoGraph UI would leave it."""
        svc = self.service_for(project)
        svc.corpus.add(category)
        module = _encode(project, category)
        svc.strategies[f"cluster_{module}_0"] = {
            "clusterId": f"cluster_{module}_0",
            "strategyType": "FullGraphRAG",
            "ragPartitionId": f"{module}_0_a",
            "entityTypes": list(entity_types),
            "parameters": {"module": module},
        }
        svc.kg.add(f"{module}_0_a")

    def writes(self) -> list[tuple[str, str]]:
        return [(m, p) for m, p in self.calls if m != "GET" and not p.endswith("/_open/auth")]

    # --- transport -----------------------------------------------------------

    def __call__(self, method, url, headers, body, timeout):
        parsed = urllib.parse.urlparse(url)
        path, query = parsed.path, urllib.parse.parse_qs(parsed.query)
        self.calls.append((method, path))

        if path == "/_db/_system/_open/auth":
            self.logins += 1
            creds = json.loads(body)
            if creds.get("password") != "secret":
                return self._json(401, {"error": True, "errorMessage": "wrong credentials"})
            return self._json(200, {"jwt": self.jwt})

        if headers.get("Authorization") != f"Bearer {self.jwt}":
            return self._json(401, {"error": True, "errorMessage": "not authorized"})

        payload = None
        if body and headers.get("Content-Type", "").startswith("application/json"):
            payload = json.loads(body)
        self.bodies.append((method, path, payload if payload is not None else body))

        if path == "/_db/_system/_api/user":
            if self.db_users is None:
                return self._json(403, {"errorMessage": "forbidden"})
            return self._json(200, {"result": [{"user": u} for u in self.db_users]})
        match = re.match(r"^/_db/_system/_api/user/([^/]+)/database/([^/]+)$", path)
        if match:
            if self.db_users is None:
                return self._json(403, {"errorMessage": "forbidden"})
            user = urllib.parse.unquote(match.group(1))
            if user not in self.db_users:
                return self._json(404, {"errorMessage": "user not found"})
            return self._json(200, {"result": self.db_users[user]})

        if path.startswith("/_platform/acp/v1"):
            return self._acp(method, path[len("/_platform/acp/v1"):], payload)
        if path.startswith("/_platform/filemanager/"):
            return self._file_manager(method, query, headers, body)
        match = re.match(r"^/autograph/([^/]+)(/v1/.*)$", path)
        if match:
            svc = next((s for s in self.services.values() if s.base == f"/autograph/{match.group(1)}"), None)
            if svc is not None and getattr(svc, "stuck", False):
                return self._json(503, {"message": "upstream connect error: connection refused"})
            if svc is None or not svc.routable or svc.status != "DEPLOYED":
                return self._json(404, {"error": True, "errorMessage": f"unknown path '{path}'"})
            return self._autograph(svc, method, match.group(2), query, payload)
        return self._json(404, {"error": True, "errorMessage": f"unknown path '{path}'"})

    @staticmethod
    def _json(status: int, payload: Any) -> tuple[int, bytes]:
        return status, json.dumps(payload).encode("utf-8")

    # --- ACP -----------------------------------------------------------------

    def _acp(self, method, path, payload):
        if method == "GET" and path.startswith("/project_by_name/"):
            name = path.rsplit("/", 1)[-1]
            if name not in self.projects:
                return self._json(400, {"code": 3, "message": "project not found"})
            return self._json(200, self.projects[name])
        if method == "POST" and path == "/project":
            name = payload["project_name"]
            if name in self.projects:
                return self._json(409, {"message": "exists"})
            self.projects[name] = {"projectName": name, "projectMetadata": {}, "modelSettings": {}}
            return self._json(200, {"projectName": name})
        if method == "POST" and path == "/service":
            env = payload["env"]
            if self.require_fps_user and not env.get("fps_recovery_username"):
                return self._json(400, {"code": 3, "message": "fps_recovery_username is required to install the service, please provide it in the install request"})
            svc = self._new_service(env["genai_project_name"], env)
            svc.status = "DEPLOYING"
            self._deploy_polls = self.polls
            return self._json(200, {"serviceInfo": {"serviceId": svc.service_id}})
        if method == "GET" and path.startswith("/service/"):
            svc = self.services.get(path.rsplit("/", 1)[-1])
            if svc is None or not svc.routable:
                return self._json(500, {"message": "Release `x` not found"})
            if svc.status == "DEPLOYING":
                self._deploy_polls -= 1
                if self._deploy_polls <= 0:
                    svc.status = "DEPLOYED"
            return self._json(200, {"serviceInfo": {"serviceId": svc.service_id, "status": svc.status}})
        return self._json(404, {"message": "Not Found"})

    # --- File Manager ----------------------------------------------------------

    def _file_manager(self, method, query, headers, body):
        if method == "GET":
            offset = int(query.get("offset", ["0"])[0])
            limit = int(query.get("limit", ["100"])[0])
            return self._json(200, {"files": self.files[offset: offset + limit]})
        if method == "POST":
            text = body.decode("utf-8", "replace")
            fields = re.findall(r'name="(\w+)"\r\n\r\n([^\r]*)\r\n', text)
            name = next(v for k, v in fields if k == "name")
            scope = [v for k, v in fields if k == "scope"]
            content = body.split(b"application/octet-stream\r\n\r\n", 1)[1].rsplit(b"\r\n--", 1)[0]
            self.add_file(scope[0], scope[1] if len(scope) > 1 else "", name, content)
            return self._json(200, {"id": f"rag-input-{name}"})
        return self._json(405, {"message": "method"})

    # --- AutoGraph -----------------------------------------------------------

    def _category_files(self, svc: Service, category: str) -> list[dict[str, Any]]:
        return [f for f in self.files if f["scope"] == [svc.project, category]]

    def _resolve(self, svc: Service, label: str, *, strict: bool) -> str | None:
        """A category label -> the bare category it names, or None."""
        categories = {f["scope"][1] for f in self.files if f["scope"][0] == svc.project}
        for category in categories:
            if label == _encode(svc.project, category):
                return category
            if label == category and not (strict and self.legacy_labels):
                return category
        return None

    def _autograph(self, svc: Service, method, path, query, payload):
        project = svc.project
        if path == "/v1/health":
            return self._json(200, {"status": "SERVING", "message": "Service is healthy"})

        if method == "GET" and path == f"/v1/projects/{project}/overview":
            return self._json(200, self._overview(svc))

        if method == "POST" and path == "/v1/corpus/builds":
            category = payload["categories"][0]
            if not self._category_files(svc, category):
                return self._json(400, {"message": f"category {category!r} has no files"})
            if category in svc.corpus and not payload.get("incremental"):
                return self._json(409, {"message": "REBUILD_NOT_ALLOWED: module already built"})
            build_id = f"cb_{len(svc.builds) + 1}"
            svc.builds[build_id] = {"category": category, "left": self.polls}
            return self._json(202, {"corpusBuildId": build_id, "graphName": f"{project}_CorpusGraph"})

        match = re.match(r"^/v1/corpus/builds/(.+)$", path)
        if method == "GET" and match:
            build = svc.builds.get(match.group(1))
            if build is None:
                return self._json(404, {"message": "unknown build"})
            build["left"] -= 1
            if build["left"] > 0:
                return self._json(200, {"status": "running", "progress": 55, "message": "Creating similarity edges..."})
            outcome = dict(self.build_outcome)
            if outcome.get("status") == "failed":
                return self._json(200, {"status": "failed", "errorCode": "UNKNOWN_ERROR", "error": outcome.get("error", "boom")})
            svc.corpus.add(build["category"])
            files = len(self._category_files(svc, build["category"]))
            return self._json(200, {
                "status": "completed", "progress": 100, "message": outcome.get("message", "Build completed successfully!"),
                "graphName": f"{project}_CorpusGraph", "filesWritten": files, "documentsCreated": files,
                "clusterCount": 0 if outcome.get("errorCode") == "CORPUS_TOO_SMALL" else 1,
                **({"errorCode": outcome["errorCode"]} if outcome.get("errorCode") else {}),
            })

        if method == "POST" and path == "/v1/rag-strategizer/analyze":
            label = payload["categories"][0]
            job_id = f"strat_{len(svc.jobs) + 1}"
            category = self._resolve(svc, label, strict=True)
            svc.jobs[job_id] = {"left": self.polls, "category": category, "label": label,
                                "complexity": payload["complexity"]}
            return self._json(202, {"strategizeJobId": job_id})

        match = re.match(r"^/v1/rag-strategizer/jobs/(.+)$", path)
        if method == "GET" and match:
            job = svc.jobs[match.group(1)]
            job["left"] -= 1
            if job["left"] > 0:
                return self._json(200, {"status": "running", "clustersTotal": 1, "clustersDone": 0, "message": "Analyzing 1 clusters"})
            if job["category"] is None:
                return self._json(200, {"status": "failed", "message": f"Unknown categories: [{job['label']!r}]"})
            if self.strategizer_failure:
                return self._json(200, {"status": "failed", "message": self.strategizer_failure})
            module = _encode(project, job["category"])
            full = job["complexity"] == "very_high"
            cluster = f"cluster_{module}_0"
            svc.strategies.setdefault(cluster, {
                "clusterId": cluster,
                "strategyType": "FullGraphRAG" if full else "VectorRAG",
                "ragPartitionId": f"{module}_0_{'a' if full else 'b'}",
                "entityTypes": list(self.strategizer_types) if full else [],
                "parameters": {"module": module},
            })
            return self._json(200, {"status": "completed", "clustersTotal": 1, "clustersDone": 1, "message": "Analyzed 1 clusters: 1 stored"})

        if method == "GET" and path == "/v1/rag-strategizer/strategy":
            rows = list(svc.strategies.values())
            return self._json(200, {"strategies": rows, "totalStrategies": len(rows)})

        match = re.match(r"^/v1/rag-strategizer/strategy/(.+)$", path)
        if method == "PATCH" and match:
            row = svc.strategies.get(match.group(1))
            if row is None:
                return self._json(404, {"message": "no strategy"})
            row["strategyType"] = payload["strategy_type"]
            row["entityTypes"] = list(payload.get("entity_types") or []) if payload["strategy_type"] == "FullGraphRAG" else []
            return self._json(200, {"strategy": row})

        if method == "POST" and path == "/v1/orchestrate":
            labels = payload.get("categories") or []
            categories = [self._resolve(svc, label, strict=True) for label in labels]
            if any(c is None for c in categories):
                return self._json(400, {"message": f"Unknown categories: {labels}. These module labels do not exist in corpus_rags."})
            stale = [
                row["ragPartitionId"] for row in svc.strategies.values()
                if row["ragPartitionId"] not in svc.kg
                and (not categories or row["parameters"]["module"] in {_encode(project, c) for c in categories})
            ]
            if not stale:
                return self._json(409, {"message": "Nothing to orchestrate: every category is already built"})
            oid = f"orch_{len(svc.orchestrations) + 1}"
            svc.orchestrations[oid] = {"left": self.polls, "partitions": stale}
            return self._json(202, {"orchestrationId": oid, "success": True})

        match = re.match(r"^/v1/orchestrate/(.+)$", path)
        if method == "GET" and match:
            run = svc.orchestrations.get(match.group(1))
            if run is None:
                return self._json(404, {"message": "unknown"})
            run["left"] -= 1
            total = len(run["partitions"])
            if run["left"] > 0:
                return self._json(200, {"status": "running", "phase": "importing", "totalJobs": total})
            if self.orchestration_failure:
                return self._json(200, {
                    "status": "failed", "phase": "finished", "totalJobs": total, "failedJobs": total,
                    "message": f"Orchestration finished with failures: 0 succeeded, {total} failed",
                    "strategySummary": {"total": total, "failed": total, "failures": [
                        {"ragPartitionId": p, "errorMessage": self.orchestration_failure} for p in run["partitions"]
                    ]},
                })
            svc.kg.update(run["partitions"])
            return self._json(200, {
                "status": "completed", "phase": "finished", "totalJobs": total, "completedJobs": total,
                "entitiesAdded": 42, "message": f"Orchestration completed: {total} succeeded, 0 failed",
            })

        match = re.match(rf"^/v1/projects/{re.escape(project)}/categories/(.+)$", path)
        if method == "DELETE" and match:
            category = match.group(1)
            if not self._category_files(svc, category):
                return self._json(404, {"message": "category not found"})
            module = _encode(project, category)
            for cluster, row in list(svc.strategies.items()):
                if row["parameters"]["module"] == module:
                    svc.kg.discard(row["ragPartitionId"])
                    del svc.strategies[cluster]
            svc.corpus.discard(category)
            deleted = 0
            if query.get("delete_files") == ["true"]:
                before = len(self.files)
                self.files = [f for f in self.files if f["scope"] != [project, category]]
                deleted = before - len(self.files)
            return self._json(200, {"deleted": True, "category": category, "graphUpdated": True, "filesDeleted": deleted})

        return self._json(404, {"message": f"no route {method} {path}"})

    def _overview(self, svc: Service) -> dict[str, Any]:
        project = svc.project
        categories = sorted({f["scope"][1] for f in self.files if f["scope"][0] == project})
        rows = []
        without, new = [], []
        for category in categories:
            module = _encode(project, category)
            ours = [r for r in svc.strategies.values() if r["parameters"]["module"] == module]
            needs_strategies = not ours
            if needs_strategies and category in svc.corpus:
                without.append(category)
            if any(r["ragPartitionId"] not in svc.kg for r in ours):
                new.append(category)
            rows.append({
                "name": category,
                "documentCount": len(self._category_files(svc, category)),
                "needsCorpusUpdate": category not in svc.corpus,
                "needsStrategies": needs_strategies,
            })
        return {
            "project": project,
            "knowledgeGraph": {
                "status": "built" if svc.kg and not new else ("stale" if svc.kg else "not_built"),
                "entityCount": 42 * len(svc.kg),
                "relationshipCount": 99 * len(svc.kg),
                "newCategories": new,
            },
            "strategies": {"categoriesWithoutStrategies": without},
            "categories": rows,
        }
