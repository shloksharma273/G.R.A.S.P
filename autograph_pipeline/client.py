"""The HTTP surface the pipeline drives: platform auth, ACP, File Manager, AutoGraph.

Everything goes through the one platform gateway (`ARANGO_URL`):

    /_db/_system/_open/auth                     username + password -> JWT
    /_platform/acp/v1/...                       projects and the services they run
    /_platform/filemanager/_db/{db}/rag-input   the files a corpus build reads
    /autograph/{id}/v1/...                      one AutoGraph service per project

Contracts are AutoGraph's (`docs/user_facing_documentation.md`) and the
platform's, cross-checked against the AutoGraph QA toolkit, which drives the
same routes live. Two traits of those services shape this module:

* **Casing is mixed.** Request bodies are accepted in snake_case, but the
  gateway answers in camelCase. `field()` reads either, so nothing downstream has
  to know which service it is talking to.
* **Tokens expire mid-run.** A knowledge-graph build takes longer than a JWT
  lives, so a 401 re-authenticates once and replays the request.

Standard library only, like the rest of the bridge.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from kg_read_harness.errors import AuthError, ConnectivityError, HarnessError

ACP = "/_platform/acp/v1"
FILE_MANAGER = "/_platform/filemanager"

#: The Helm chart ACP installs an AutoGraph service from.
AUTOGRAPH_CHART = "arangodb-autograph"

DEFAULT_TIMEOUT = 120.0

#: File Manager caps a listing page at 1000 (rag_input.yaml).
LIST_PAGE = 1000

#: (method, url, headers, body, timeout) -> (status, body bytes)
Transport = Callable[[str, str, Mapping[str, str], "bytes | None", float], "tuple[int, bytes]"]


class ApiError(HarnessError):
    """A service answered, and the answer was not a success."""

    label = "AutoGraph API error"
    exit_code = 10

    def __init__(self, method: str, path: str, status: int, body: Any) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        super().__init__(f"{method} {path} -> HTTP {status}: {describe(body)}")

    @property
    def text(self) -> str:
        """The response rendered as text, for matching on a service's own words."""
        return describe(self.body, limit=4000)


def describe(body: Any, limit: int = 400) -> str:
    """A response body as one line: the service's message when it has one."""
    if isinstance(body, dict):
        for key in ("message", "errorMessage", "error_message", "error"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value[:limit]
        return json.dumps(body)[:limit]
    return str(body or "")[:limit]


def camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in rest)


def field(payload: Any, name: str, default: Any = None) -> Any:
    """Read a snake_case field in either casing the gateway may have used."""
    if not isinstance(payload, dict):
        return default
    for key in (name, camel(name)):
        if key in payload and payload[key] is not None:
            return payload[key]
    return default


def urllib_transport(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


def encode_multipart(
    fields: Iterable[tuple[str, str]], file_field: str, filename: str, content: bytes
) -> tuple[bytes, str]:
    """A multipart/form-data body. Returns `(body, content_type)`.

    Fields may repeat - File Manager builds a two-level scope from two `scope`
    fields, and sending it once lands the file at the project root, where a
    category build cannot see it.
    """
    boundary = f"----grasp{uuid.uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; '
        f'filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n'.encode("utf-8")
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


@dataclass
class Platform:
    """An authenticated session on the platform gateway."""

    url: str
    database: str
    username: str | None = None
    password: str | None = None
    token: str | None = None
    transport: Transport = urllib_transport
    timeout: float = DEFAULT_TIMEOUT

    def __post_init__(self) -> None:
        self.url = self.url.rstrip("/")
        self._jwt = self.token or ""

    # --- auth ---------------------------------------------------------------

    def authenticate(self) -> None:
        """Mint a JWT from username + password. A supplied token is used as-is."""
        if self.token:
            self._jwt = self.token
            return
        if self.username is None:
            raise AuthError(
                "no credentials to authenticate with.",
                "set ARANGO_USERNAME + ARANGO_PASSWORD, or ARANGO_AUTH_TOKEN.",
            )
        body = json.dumps({"username": self.username, "password": self.password or ""})
        status, raw = self._send(
            "POST",
            "/_db/_system/_open/auth",
            {"Content-Type": "application/json"},
            body.encode("utf-8"),
            self.timeout,
        )
        if status in (401, 403):
            raise AuthError(
                f"the platform rejected the credentials for {self.username!r}.",
                "check ARANGO_USERNAME / ARANGO_PASSWORD.",
            )
        payload = _decode(raw)
        if status >= 300 or not field(payload, "jwt"):
            raise ApiError("POST", "/_open/auth", status, payload)
        self._jwt = payload["jwt"]

    # --- transport ----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        params: Mapping[str, Any] | None = None,
        raw: tuple[bytes, str] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """One call. Re-authenticates once on a 401; raises ApiError on any non-2xx."""
        if not self._jwt:
            self.authenticate()
        target = path
        if params:
            target += "?" + urllib.parse.urlencode(params, doseq=True)

        headers = {"Accept": "application/json"}
        data: bytes | None = None
        if raw is not None:
            data, headers["Content-Type"] = raw
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        for attempt in (0, 1):
            headers["Authorization"] = f"Bearer {self._jwt}"
            status, payload = self._send(method, target, headers, data, timeout or self.timeout)
            if status == 401 and attempt == 0 and not self.token:
                self.authenticate()
                continue
            break

        decoded = _decode(payload)
        if status == 401:
            raise AuthError(
                f"{method} {path} was refused: the token is not valid for this platform.",
                "check the credentials, or that the user can reach this database.",
            )
        if not 200 <= status < 300:
            raise ApiError(method, path, status, decoded)
        return decoded

    def _send(
        self, method: str, path: str, headers: Mapping[str, str], data: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        url = self.url + path
        # Reads are replayed on a dropped connection; writes are not, because a
        # write that timed out most likely landed, and a second corpus build or a
        # second upload is not a harmless retry.
        attempts = 3 if method == "GET" else 1
        for attempt in range(attempts):
            try:
                return self.transport(method, url, headers, data, timeout)
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as error:
                if attempt == attempts - 1:
                    raise ConnectivityError(
                        f"could not reach {self.url} ({error.__class__.__name__}: {error}).",
                        "check ARANGO_URL and that the platform is up.",
                    ) from None
                time.sleep(2 * (attempt + 1))
        raise AssertionError("unreachable")

    # --- ACP: projects and services ------------------------------------------

    def project(self, name: str) -> dict[str, Any] | None:
        """The ACP project record, or None when there is no such project.

        ACP answers an unknown project with 400 rather than 404, so both mean
        "absent" here.
        """
        try:
            return self.request("GET", f"{ACP}/project_by_name/{self.database}/{name}")
        except ApiError as error:
            if error.status in (400, 404):
                return None
            raise

    def create_project(self, name: str, description: str = "") -> dict[str, Any]:
        return self.request(
            "POST",
            f"{ACP}/project",
            body={
                "project_name": name,
                "project_type": "autograph",
                "project_db_name": self.database,
                "project_description": description or "built by G.R.A.S.P",
            },
        )

    def service(self, service_id: str) -> dict[str, Any] | None:
        """A service's install record, or None once its Helm release is gone."""
        try:
            payload = self.request("GET", f"{ACP}/service/{service_id}")
        except ApiError as error:
            # A deleted release answers 500 "Release ... not found", not 404.
            if error.status in (404, 500) and "not found" in error.text.lower():
                return None
            raise
        return payload.get("serviceInfo", payload) if isinstance(payload, dict) else None

    def deploy_service(self, env: Mapping[str, str], chart: str = AUTOGRAPH_CHART) -> str:
        payload = self.request(
            "POST", f"{ACP}/service", body={"service_name": chart, "env": dict(env)}
        )
        info = field(payload, "service_info") or {}
        service_id = field(info, "service_id")
        if not service_id:
            raise ApiError("POST", f"{ACP}/service", 200, payload)
        return service_id

    # --- database users -------------------------------------------------------

    def users(self) -> list[str] | None:
        """Every database user's name, or None when this login may not list them."""
        try:
            payload = self.request("GET", "/_db/_system/_api/user")
        except ApiError as error:
            if error.status in (401, 403, 404):
                return None
            raise
        return [row.get("user", "") for row in field(payload, "result") or [] if row.get("user")]

    def access(self, user: str, database: str) -> str | None:
        """`rw`, `ro` or `none` for `user` on `database`; None when it cannot be read."""
        try:
            payload = self.request(
                "GET", f"/_db/_system/_api/user/{urllib.parse.quote(user, safe='')}/database/{database}"
            )
        except ApiError as error:
            if error.status in (401, 403, 404):
                return None
            raise
        level = field(payload, "result")
        return str(level) if level is not None else None

    # --- File Manager -------------------------------------------------------

    def files(self, scope: list[str]) -> list[dict[str, Any]]:
        """Every RAG input whose scope is exactly `scope`, latest versions only."""
        found: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.request(
                "GET",
                f"{FILE_MANAGER}/_db/{self.database}/rag-input",
                params={"limit": LIST_PAGE, "offset": offset},
            )
            rows = field(page, "files") or []
            found.extend(row for row in rows if list(row.get("scope") or []) == list(scope))
            if len(rows) < LIST_PAGE:
                return found
            offset += LIST_PAGE

    def upload(self, name: str, content: bytes, scope: list[str]) -> str:
        """Upload one file under `scope`. Returns its File Manager id.

        There is no batch endpoint; one request per file. Re-uploading a name
        into the same scope supersedes the earlier file as a new version.
        """
        fields = [("name", name)] + [("scope", part) for part in scope]
        raw = encode_multipart(fields, "file", name, content)
        payload = self.request(
            "POST", f"{FILE_MANAGER}/_db/{self.database}/rag-input", raw=raw, timeout=300
        )
        return field(payload, "id") or field(payload, "file_id") or ""

    def autograph(self, base: str) -> "AutoGraph":
        return AutoGraph(self, "/" + base.strip("/"))


@dataclass
class AutoGraph:
    """One project's AutoGraph service, addressed through the gateway."""

    platform: Platform
    base: str

    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        return self.platform.request(method, self.base + path, **kwargs)

    def health(self) -> dict[str, Any]:
        return self._call("GET", "/v1/health", timeout=30)

    def overview(self, project: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/projects/{project}/overview")

    def build_corpus(self, categories: list[str], incremental: bool = False) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/corpus/builds",
            body={
                "embedding_strategy": "first_chunk",
                "categories": list(categories),
                "incremental": incremental,
            },
        )

    def build_status(self, build_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/corpus/builds/{build_id}")

    def analyze(self, project: str, complexity: str, categories: list[str]) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/rag-strategizer/analyze",
            body={"project": project, "complexity": complexity, "categories": list(categories)},
        )

    def strategizer_job(self, job_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/rag-strategizer/jobs/{job_id}")

    def strategies(self) -> list[dict[str, Any]]:
        return field(self._call("GET", "/v1/rag-strategizer/strategy"), "strategies") or []

    def patch_strategy(
        self, cluster_id: str, strategy_type: str, entity_types: list[str]
    ) -> dict[str, Any]:
        return self._call(
            "PATCH",
            f"/v1/rag-strategizer/strategy/{cluster_id}",
            body={
                "strategy_type": strategy_type,
                "entity_types": list(entity_types),
                "extract_images": False,
            },
        )

    def orchestrate(
        self, project: str, categories: list[str], replicas: int, max_retries: int
    ) -> dict[str, Any]:
        return self._call(
            "POST",
            "/v1/orchestrate",
            body={
                "project": project,
                "categories": list(categories),
                "replicas": replicas,
                "max_retries": max_retries,
            },
        )

    def orchestration(self, orchestration_id: str) -> dict[str, Any]:
        return self._call("GET", f"/v1/orchestrate/{orchestration_id}")

    def delete_category(self, project: str, category: str, delete_files: bool) -> dict[str, Any]:
        # delete_files is a query parameter: sent as JSON it is silently ignored.
        return self._call(
            "DELETE",
            f"/v1/projects/{project}/categories/{category}",
            params={"delete_files": "true" if delete_files else "false"},
        )


def service_path(service_url: str) -> str:
    """The gateway path of a service from the internal URL ACP records.

    `https://deployment.<ns>.svc:8529/autograph/uqabo/` -> `/autograph/uqabo`.
    Only the path is reusable; the host is the cluster-internal address.
    """
    if "://" in service_url:
        service_url = service_url.split("://", 1)[1]
        service_url = service_url.split("/", 1)[1] if "/" in service_url else ""
    return "/" + service_url.strip("/")


def _decode(raw: bytes) -> Any:
    if not raw:
        return {}
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


__all__ = [
    "ACP",
    "AUTOGRAPH_CHART",
    "ApiError",
    "AutoGraph",
    "FILE_MANAGER",
    "Platform",
    "camel",
    "describe",
    "encode_multipart",
    "field",
    "service_path",
    "urllib_transport",
]
