"""Rulebooks in, PlanGraph out, through AutoGraph's own API.

    connect      the project's AutoGraph service (deployed first with --provision)
    upload       every rulebook into File Manager under [project, category]
    corpus       POST /v1/corpus/builds             the corpus graph
    strategize   POST /v1/rag-strategizer/analyze   one strategy per cluster
    ontology     PATCH /v1/rag-strategizer/strategy SKILL, PRIMITIVE, OBJECT, STATE
    kg           POST /v1/orchestrate               the knowledge graph
    plangraph    the five bridge stations           the PlanGraph

The category is the module: every rulebook goes in one, so they are clustered
together and land in one knowledge graph partition set.

**Every stage reads before it acts.** The project overview says whether the
corpus, the strategies and the knowledge graph are already current for this
category, and a stage that finds its work done reports it and moves on. That is
what makes a rerun a resume rather than a pile of 409s - AutoGraph refuses a
full rebuild of a built category, and answers an orchestration with nothing
stale as a conflict.

**A plain run writes nothing.** Without `write`, each stage reports what it
would do and stops short of doing it; once one stage would act, the ones after
it can only be predicted, so they are reported as planned too. The same
discipline Station 5 keeps, for a stronger reason: this one uploads files,
starts builds that spend model tokens, and can deploy services.

**The ontology is applied, not hoped for.** The strategizer writes its own
entity types per cluster from an LLM's reading of a sample. The bridge reads
exactly four, so every cluster of this category is patched to them before the
importer runs - and a partition already imported under a different ontology is
reported as needing a rebuild, because a patch after import changes nothing.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from kg_read_harness.errors import ConfigError, HarnessError

from .client import ApiError, AutoGraph, Platform, describe, field as read
from .client import service_path
from .config import MODEL_FIELDS, PipelineConfig

STAGE_CONNECT = "connecting to AutoGraph"
STAGE_UPLOAD = "uploading the rulebooks"
STAGE_CORPUS = "building the corpus graph"
STAGE_STRATEGIZE = "generating strategies"
STAGE_ONTOLOGY = "applying the ontology"
STAGE_KG = "building the knowledge graph"
STAGE_PLANGRAPH = "building the PlanGraph"

STAGES = (
    STAGE_CONNECT,
    STAGE_UPLOAD,
    STAGE_CORPUS,
    STAGE_STRATEGIZE,
    STAGE_ONTOLOGY,
    STAGE_KG,
    STAGE_PLANGRAPH,
)

STATUS_DONE = "done"
STATUS_SKIPPED = "skipped"
STATUS_PLANNED = "planned"
STATUS_FAILED = "failed"

FULL_GRAPH_RAG = "FullGraphRAG"

EXIT_PIPELINE = 10


class PipelineError(HarnessError):
    """A stage could not finish, for a reason the service or the state names."""

    exit_code = EXIT_PIPELINE
    label = "pipeline stopped"


@dataclass(frozen=True)
class Rulebook:
    name: str
    content: bytes

    @classmethod
    def from_path(cls, path: str | Path) -> "Rulebook":
        path = Path(path)
        return cls(name=path.name, content=path.read_bytes())


@dataclass
class StageReport:
    stage: str
    status: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage, "status": self.status, "detail": self.detail, "data": self.data}


@dataclass
class PipelineResult:
    project: str
    category: str
    write: bool
    service: str = ""
    stages: list[StageReport] = field(default_factory=list)
    failed: str = ""
    failed_stage: str = ""
    error: HarnessError | None = None
    plangraph: dict[str, Any] | None = None

    @property
    def ok(self) -> bool:
        return not self.failed

    def to_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "category": self.category,
            "write": self.write,
            "ok": self.ok,
            "service": self.service,
            "stages": [s.to_dict() for s in self.stages],
            "failed": self.failed,
            "failed_stage": self.failed_stage,
            "plangraph": self.plangraph,
        }


@dataclass
class Overview:
    """The parts of `GET /v1/projects/{project}/overview` the stages decide on."""

    categories: dict[str, dict[str, Any]] = field(default_factory=dict)
    without_strategies: set[str] = field(default_factory=set)
    kg_status: str = "not_built"
    new_categories: set[str] = field(default_factory=set)
    entity_count: int = 0
    relationship_count: int = 0

    @classmethod
    def parse(cls, payload: dict[str, Any]) -> "Overview":
        kg = read(payload, "knowledge_graph") or {}
        strategies = read(payload, "strategies") or {}
        return cls(
            categories={
                read(row, "name"): {
                    "documents": int(read(row, "document_count", 0) or 0),
                    "needs_corpus_update": bool(read(row, "needs_corpus_update", False)),
                    "needs_strategies": bool(read(row, "needs_strategies", False)),
                }
                for row in read(payload, "categories") or []
                if read(row, "name")
            },
            without_strategies=set(read(strategies, "categories_without_strategies") or []),
            kg_status=str(read(kg, "status") or "not_built"),
            new_categories=set(read(kg, "new_categories") or []),
            entity_count=int(read(kg, "entity_count", 0) or 0),
            relationship_count=int(read(kg, "relationship_count", 0) or 0),
        )

    def corpus_current(self, category: str) -> bool:
        row = self.categories.get(category)
        return bool(row) and not row["needs_corpus_update"]

    def has_strategies(self, category: str) -> bool:
        row = self.categories.get(category)
        return (
            bool(row) and not row["needs_strategies"] and category not in self.without_strategies
        )

    def in_kg(self, category: str) -> bool:
        return (
            self.kg_status in ("built", "stale")
            and self.has_strategies(category)
            and category not in self.new_categories
        )


def encode_module(project: str, category: str) -> str:
    """The module label AutoGraph persists for `[project, category]`.

    Each scope segment has its `_` percent-encoded, then they are joined with
    `_`. The docs say the bare label is accepted everywhere; older services
    matched the strategizer and orchestrator against this encoded form only, so
    it is the fallback when a bare label is refused.
    """
    return "_".join(part.replace("_", "%5F") for part in (project, category))


PlanGraphBuilder = Callable[[str, Callable[[str, Any], None]], Any]


class Pipeline:
    def __init__(
        self,
        platform: Platform,
        config: PipelineConfig,
        project: str,
        category: str,
        rulebooks: list[Rulebook],
        *,
        write: bool = False,
        rebuild: bool = False,
        provision: bool = False,
        fps_user: str | None = None,
        build_plangraph: PlanGraphBuilder | None = None,
        on_stage: Callable[[str, StageReport | None], None] | None = None,
        on_progress: Callable[[str, str], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not project or not category:
            raise ConfigError("a project and a category are both required.", "name them.")
        names = [r.name for r in rulebooks]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        if duplicates:
            # File Manager keys a file by (scope, name): a second one supersedes
            # the first, so one of these rulebooks would silently not be built.
            raise ConfigError(
                "two rulebooks share a file name: " + ", ".join(duplicates) + ".",
                "rename one; File Manager keeps only the last file of each name.",
            )
        self.platform = platform
        self.config = config
        self.project = project
        self.category = category
        self.rulebooks = list(rulebooks)
        self.write = write
        self.rebuild = rebuild
        self.provision = provision
        #: The File Parsing Service's recovery user for a new service; found in
        #: the database when neither this nor the configuration names one.
        self.fps_user = (fps_user or "").strip() or config.fps_recovery_username
        self.build_plangraph = build_plangraph
        self.on_stage = on_stage or (lambda _stage, _report: None)
        self.on_progress = on_progress or (lambda _stage, _message: None)
        self.sleep = sleep
        self.clock = clock

        self.autograph: AutoGraph | None = None
        self.result = PipelineResult(project=project, category=category, write=write)
        #: Once a stage would act in a dry run, what follows can only be predicted.
        self._pending = ""

    # --- running ------------------------------------------------------------

    def run(self) -> PipelineResult:
        steps = (
            (STAGE_CONNECT, self._connect),
            (STAGE_UPLOAD, self._upload),
            (STAGE_CORPUS, self._corpus),
            (STAGE_STRATEGIZE, self._strategize),
            (STAGE_ONTOLOGY, self._ontology),
            (STAGE_KG, self._knowledge_graph),
            (STAGE_PLANGRAPH, self._plangraph),
        )
        for stage, step in steps:
            self.on_stage(stage, None)
            try:
                report = self._predicted(stage) if self._pending else step()
            except HarnessError as error:
                report = StageReport(stage, STATUS_FAILED, error.message)
                self.result.failed = error.message
                self.result.failed_stage = stage
                self.result.error = error
                self._report(report)
                return self.result
            self._report(report)
        return self.result

    def _report(self, report: StageReport) -> None:
        self.result.stages.append(report)
        self.on_stage(report.stage, report)

    def _predicted(self, stage: str) -> StageReport:
        return StageReport(stage, STATUS_PLANNED, f"would run once {self._pending} has")

    def _plan(self, stage: str, detail: str, **data: Any) -> StageReport:
        """The dry-run outcome of a stage that has work to do."""
        self._pending = self._pending or stage
        return StageReport(stage, STATUS_PLANNED, detail, dict(data))

    def _overview(self) -> Overview:
        assert self.autograph is not None
        try:
            return Overview.parse(self.autograph.overview(self.project))
        except ApiError as error:
            # A project whose graphs do not exist yet answers "not ready"; that is
            # an empty overview, not a failure. A 404 names a project mismatch
            # and is one.
            if error.status == 503:
                return Overview()
            if error.status == 404:
                raise PipelineError(
                    f"the AutoGraph service at {self.autograph.base} does not serve "
                    f"project {self.project!r}: {describe(error.body)}",
                    "each AutoGraph service serves exactly one project; point "
                    "AUTOGRAPH_SERVICE_PATH at the right one, or unset it.",
                ) from None
            raise

    def _poll(
        self,
        stage: str,
        fetch: Callable[[], dict[str, Any]],
        finished: Callable[[dict[str, Any]], bool],
        summary: Callable[[dict[str, Any]], str],
        timeout: float,
        what: str,
    ) -> dict[str, Any]:
        deadline = self.clock() + timeout
        shown = None
        while True:
            state = fetch()
            if finished(state):
                return state
            message = summary(state)
            if message and message != shown:
                self.on_progress(stage, message)
                shown = message
            if self.clock() >= deadline:
                raise PipelineError(
                    f"{what} did not finish within {timeout / 60:.0f} minute(s); last seen: {message}",
                    "it may still be running server-side - rerun to resume from where it got to.",
                )
            self.sleep(self.config.poll_seconds)

    # --- stage 0: the service ---------------------------------------------

    def _connect(self) -> StageReport:
        stage = STAGE_CONNECT
        if self.config.service_path:
            self.autograph = self.platform.autograph(self.config.service_path)
            self._check_health(self.autograph)
            self.result.service = self.autograph.base
            return StageReport(stage, STATUS_DONE, f"using {self.autograph.base} (configured)")

        record = self.platform.project(self.project)
        if record is None:
            if not self.provision:
                raise PipelineError(
                    f"there is no AutoGraph project named {self.project!r} in {self.platform.database}.",
                    "create it in the platform UI, or pass --provision to create it and "
                    "deploy its AutoGraph service.",
                )
            if not self.write:
                env = self._service_env({})
                return self._plan(
                    stage,
                    f"would create project {self.project} and deploy its AutoGraph service"
                    + self._deploy_note(env),
                )
            self._create_project()
            record = self.platform.project(self.project) or {}
            return self._deploy(record, created=True)

        node = read(read(record, "project_metadata") or {}, "autograph_service") or {}
        url = read(node, "service_url")
        service_id = read(node, "service_id") or ""
        if url:
            autograph = self.platform.autograph(service_path(url))
            if self._healthy(autograph):
                self.autograph = autograph
                self.result.service = autograph.base
                return StageReport(
                    stage, STATUS_DONE, f"{self.project} is served by {autograph.base}",
                    {"service_id": service_id},
                )

            # Installed but silent is not the same as gone: a pod that cannot start
            # (an image that will not pull, a crash loop) keeps its release, and a
            # second deploy would hang the same way beside it.
            info = self.platform.service(service_id) if service_id else None
            if info is not None and str(read(info, "status", "")).upper() == "DEPLOYED":
                raise PipelineError(
                    f"{service_id} is installed for {self.project}, but {autograph.base} does not answer.",
                    f"its pod is most likely not running - `kubectl get pods | grep {service_id}`. "
                    "An image that cannot be pulled (ImagePullBackOff) looks exactly like this; "
                    "deploying another service would hang the same way. Rerun once it is up.",
                )

        # The record names a service whose route no longer answers - ACP keeps
        # the metadata after the Helm release is deleted - or names none at all.
        gone = f"its service {service_id} is no longer running" if service_id else "it has no AutoGraph service"
        if not self.provision:
            raise PipelineError(
                f"project {self.project} exists, but {gone}.",
                "deploy an AutoGraph service for it in the platform UI, or pass "
                "--provision to deploy one with the project's saved model settings.",
            )
        if not self.write:
            env = self._service_env(record)
            return self._plan(
                stage, f"project {self.project} exists but {gone}; would deploy one" + self._deploy_note(env)
            )
        return self._deploy(record, created=False)

    def _healthy(self, autograph: AutoGraph) -> bool:
        try:
            return str(read(autograph.health(), "status", "")).upper() == "SERVING"
        except ApiError as error:
            if error.status in (404, 502, 503, 504):
                return False
            raise

    def _check_health(self, autograph: AutoGraph) -> None:
        if not self._healthy(autograph):
            raise PipelineError(
                f"no AutoGraph service answers at {autograph.base}.",
                "check AUTOGRAPH_SERVICE_PATH, or unset it to use the project's own service.",
            )

    def _create_project(self) -> None:
        # ACP has been seen to answer a create with 200 and never store the
        # project, so the write is read back rather than trusted.
        for attempt in range(3):
            try:
                self.platform.create_project(self.project)
            except ApiError as error:
                if error.status != 409:  # 409: an earlier attempt landed after all
                    raise
            if self.platform.project(self.project) is not None:
                self.on_progress(STAGE_CONNECT, f"created project {self.project}")
                return
            self.sleep(1 + attempt)
        raise PipelineError(
            f"ACP accepted project {self.project} but it never appeared.",
            "create it in the platform UI and rerun.",
        )

    def _service_env(self, record: dict[str, Any]) -> dict[str, str]:
        """What a new AutoGraph service is deployed with.

        Model settings come from the project's own saved ones first, then from a
        project named by AUTOGRAPH_MODEL_FROM, then from AUTOGRAPH_* variables.
        The embedding model is fixed for the life of a project, so it is never
        guessed.
        """
        env: dict[str, str] = {}
        sources = [read(record, "model_settings") or {}]
        if self.config.model_from:
            template = self.platform.project(self.config.model_from)
            if template is None:
                raise ConfigError(
                    f"AUTOGRAPH_MODEL_FROM names {self.config.model_from!r}, which is not a project.",
                    "name a project whose model settings should be copied.",
                )
            sources.append(read(template, "model_settings") or {})
        for settings in sources:
            for acp_name, var in MODEL_FIELDS.items():
                value = settings.get(acp_name)
                if value and var not in env:
                    env[var] = str(value)
        for var, value in self.config.model_env.items():
            env.setdefault(var, value)

        missing = [v for v in ("chat_model", "embedding_model") if not env.get(v)]
        if missing:
            raise ConfigError(
                "no model settings to deploy an AutoGraph service with (missing "
                + ", ".join(missing) + ").",
                "set AUTOGRAPH_MODEL_FROM to a project whose settings to copy, or "
                "AUTOGRAPH_CHAT_MODEL, AUTOGRAPH_EMBEDDING_MODEL and the secret profile ids.",
            )
        env.update({"db_name": self.platform.database, "genai_project_name": self.project})
        fps = self._fps_recovery_user()
        if fps:
            env["fps_recovery_username"] = fps
        return env

    def _deploy_note(self, env: dict[str, str]) -> str:
        fps = env.get("fps_recovery_username")
        model = env.get("chat_model", "?")
        if fps:
            return f" (models {model} / {env.get('embedding_model', '?')}, FPS recovery user {fps})"
        return (
            f" (models {model} / {env.get('embedding_model', '?')}; no FPS recovery user found - "
            "the platform may refuse the install)"
        )

    def _fps_recovery_user(self) -> str:
        """The ArangoDB user File Parsing resumes a corpus build as.

        The platform refuses to install AutoGraph without one, and it must have
        `rw` on the project database. A named user is checked; otherwise one is
        looked for - a user whose name mentions fps, `fps_recovery` first - since
        a database that has run AutoGraph before already has one. When users
        cannot be listed with this login, none is sent and the platform's own
        refusal is reported.
        """
        database = self.platform.database
        if self.fps_user:
            level = self.platform.access(self.fps_user, database)
            if level is not None and level != "rw":
                raise ConfigError(
                    f"the FPS recovery user {self.fps_user!r} has {level!r} access to {database}, not rw.",
                    f"grant it rw on {database}, or name a user that has it.",
                )
            return self.fps_user
        names = self.platform.users() or []
        candidates = sorted(
            (n for n in names if "fps" in n.lower()),
            key=lambda n: (n.lower() != "fps_recovery", n.lower()),
        )
        for name in candidates:
            if self.platform.access(name, database) == "rw":
                self.fps_user = name
                return name
        return ""

    def _deploy(self, record: dict[str, Any], created: bool) -> StageReport:
        stage = STAGE_CONNECT
        env = self._service_env(record)
        try:
            service_id = self.platform.deploy_service(env)
        except ApiError as error:
            if error.status == 400 and "fps_recovery_username" in error.text:
                raise ConfigError(
                    "the platform requires an fps_recovery_username to install AutoGraph.",
                    "set AUTOGRAPH_FPS_RECOVERY_USERNAME to an ArangoDB user with rw on "
                    f"{self.platform.database}; the File Parsing Service uses it to resume "
                    "a corpus build after a restart.",
                ) from None
            raise
        self.on_progress(stage, f"deploying {service_id}")

        def installed(info: dict[str, Any]) -> bool:
            return str(read(info, "status", "")).upper() not in ("", "DEPLOYING", "PENDING")

        info = self._poll(
            stage,
            lambda: self.platform.service(service_id) or {},
            installed,
            lambda info: f"{service_id}: {read(info, 'status', 'waiting')}",
            self.config.deploy_timeout,
            f"deploying {service_id}",
        )
        if str(read(info, "status", "")).upper() != "DEPLOYED":
            raise PipelineError(
                f"{service_id} ended {read(info, 'status')!r}: {read(info, 'description', '')}",
                "check the service in the platform UI.",
            )

        # The route is registered some time after the install reports DEPLOYED.
        fresh = self.platform.project(self.project) or {}
        node = read(read(fresh, "project_metadata") or {}, "autograph_service") or {}
        url = read(node, "service_url")
        base = service_path(url) if url else f"/autograph/{service_id.rsplit('-', 1)[-1]}"
        autograph = self.platform.autograph(base)
        try:
            self._poll(
                stage,
                lambda: {"up": self._healthy(autograph)},
                lambda state: state["up"],
                lambda _state: f"waiting for {base} to answer",
                self.config.deploy_timeout,
                f"the route to {base}",
            )
        except PipelineError as error:
            raise PipelineError(
                f"{service_id} installed, but {base} never answered: {error.message}",
                f"its pod is most likely not running - `kubectl get pods | grep {service_id}`. "
                "An image that cannot be pulled (ImagePullBackOff) looks exactly like this. "
                "Rerun once it is up; the pipeline resumes from here.",
            ) from None
        self.autograph = autograph
        self.result.service = base
        what = "created the project and deployed" if created else "deployed"
        return StageReport(stage, STATUS_DONE, f"{what} {service_id} at {base}", {"service_id": service_id})

    # --- stage 1: files -----------------------------------------------------

    def _upload(self) -> StageReport:
        stage = STAGE_UPLOAD
        scope = [self.project, self.category]
        overview = self._overview()
        built = overview.corpus_current(self.category) or overview.has_strategies(self.category)
        deleted = ""

        if self.rebuild and (self.category in overview.categories or self.platform.files(scope)):
            if not self.write:
                return self._plan(
                    stage,
                    f"would delete category {self.category} (its corpus, strategies, knowledge-graph "
                    f"partitions and files), then upload {len(self.rulebooks)} rulebook(s)",
                )
            deleted = self._delete_category()
            built = False

        present = {row.get("name"): row for row in self.platform.files(scope)}
        ours = {r.name for r in self.rulebooks}
        new = [r for r in self.rulebooks if r.name not in present]
        # Size is what File Manager reports back, so it is the one comparison
        # available without downloading every file.
        changed = [
            r for r in self.rulebooks
            if r.name in present and int(present[r.name].get("size") or -1) != len(r.content)
        ]
        extra = sorted(name for name in present if name not in ours)

        if built and (new or changed):
            names = [r.name for r in new + changed]
            raise PipelineError(
                f"category {self.category} is already built, and "
                f"{len(names)} rulebook(s) are new or changed: {', '.join(names[:5])}"
                + (" ..." if len(names) > 5 else "") + ".",
                "pass --rebuild to delete the category and build it again from these "
                "rulebooks; AutoGraph does not re-extract a partition it has imported.",
            )

        todo = new + changed
        data = {
            "scope": scope,
            "uploaded": [r.name for r in todo],
            "present": sorted(ours & set(present) - {r.name for r in changed}),
            "extra": extra,
        }
        tail = f"; also in the category and built with them: {', '.join(extra)}" if extra else ""
        if not todo:
            return StageReport(
                stage, STATUS_SKIPPED,
                f"all {len(self.rulebooks)} rulebook(s) already in {'/'.join(scope)}{tail}", data,
            )
        if not self.write:
            return self._plan(stage, f"would upload {len(todo)} rulebook(s) to {'/'.join(scope)}{tail}", **data)

        for position, rulebook in enumerate(todo, start=1):
            self.platform.upload(rulebook.name, rulebook.content, scope)
            self.on_progress(stage, f"uploaded {position}/{len(todo)}: {rulebook.name}")
        detail = f"uploaded {len(todo)} rulebook(s) to {'/'.join(scope)}"
        if deleted:
            detail = f"{deleted}; {detail}"
        return StageReport(stage, STATUS_DONE, detail + tail, data)

    def _delete_category(self) -> str:
        assert self.autograph is not None
        try:
            reply = self.autograph.delete_category(self.project, self.category, delete_files=True)
        except ApiError as error:
            if error.status == 404:  # never built: nothing to delete
                return "nothing was built for the category yet"
            if error.status == 409:
                raise PipelineError(
                    f"category {self.category} cannot be deleted while a build or "
                    f"orchestration is running: {describe(error.body)}",
                    "wait for it to finish and rerun.",
                ) from None
            raise
        locked = read(reply, "locked_skipped") or []
        detail = f"deleted category {self.category} ({read(reply, 'files_deleted', 0)} file(s) removed)"
        if locked:
            # A locked file stays; uploading the same name supersedes it with a
            # new version, which is the one a build reads.
            detail += f", {len(locked)} locked file(s) kept"
        self.on_progress(STAGE_UPLOAD, detail)
        return detail

    # --- stage 2: corpus graph ---------------------------------------------

    def _corpus(self) -> StageReport:
        stage = STAGE_CORPUS
        assert self.autograph is not None
        overview = self._overview()
        if overview.corpus_current(self.category):
            documents = overview.categories[self.category]["documents"]
            return StageReport(stage, STATUS_SKIPPED, f"already current ({documents} document(s))")
        if not self.write:
            return self._plan(stage, f"would build the corpus graph for {self.category}")

        try:
            started = self.autograph.build_corpus([self.category], incremental=False)
        except ApiError as error:
            if error.status == 409 and "REBUILD_NOT_ALLOWED" in error.text:
                # The category is in the corpus already but behind its files - an
                # interrupted earlier run. Append rather than rebuild.
                started = self.autograph.build_corpus([self.category], incremental=True)
            elif error.status == 409:
                raise PipelineError(
                    f"AutoGraph is busy: {describe(error.body)}",
                    "only one corpus build runs at a time; wait for it and rerun.",
                ) from None
            else:
                raise PipelineError(
                    f"AutoGraph refused the corpus build: {describe(error.body)}",
                    "a 400 here usually means the category has no files in File Manager, "
                    "or the service's model configuration is invalid.",
                ) from None

        build_id = read(started, "corpus_build_id")
        if not build_id:
            raise PipelineError(f"the corpus build returned no id: {describe(started)}")
        self.on_progress(stage, f"build {build_id} started")

        def fetch() -> dict[str, Any]:
            try:
                return self.autograph.build_status(build_id)
            except ApiError as error:
                if error.status == 404:
                    raise PipelineError(
                        f"corpus build {build_id} is no longer known to the service.",
                        "build status lives in the pod; a restart loses it. Rerun to resume.",
                    ) from None
                raise

        final = self._poll(
            stage,
            fetch,
            lambda s: str(read(s, "status", "")).lower() in ("completed", "failed"),
            lambda s: f"{read(s, 'progress', 0)}% {read(s, 'message', '')}".strip(),
            self.config.corpus_timeout,
            f"corpus build {build_id}",
        )
        code = read(final, "error_code") or ""
        if str(read(final, "status")).lower() == "failed":
            raise PipelineError(
                f"corpus build {build_id} failed ({code or 'no code'}): "
                f"{read(final, 'error') or read(final, 'message')}"
            )
        if code == "CORPUS_TOO_SMALL":
            raise PipelineError(
                f"the corpus built, but produced no clusters: {read(final, 'message')}",
                "the strategizer needs at least one cluster; add rulebooks and rebuild.",
            )
        detail = (
            f"{read(final, 'documents_created', 0) or read(final, 'files_written', 0)} document(s), "
            f"{read(final, 'cluster_count', 0)} cluster(s) in {read(final, 'graph_name', '')}"
        )
        if code:
            # A partial success: some files parsed, some did not.
            detail += f"; warning {code}: {read(final, 'message', '')}"
        return StageReport(stage, STATUS_DONE, detail, {"build_id": build_id, "error_code": code})

    # --- stage 3: strategies -------------------------------------------------

    def _strategize(self) -> StageReport:
        stage = STAGE_STRATEGIZE
        overview = self._overview()
        if overview.has_strategies(self.category):
            return StageReport(stage, STATUS_SKIPPED, "strategies already exist for the category")
        if not self.write:
            return self._plan(stage, f"would analyze {self.category}'s clusters at {self.config.complexity}")

        final = self._run_strategizer(self.category)
        if str(read(final, "status")).lower() != "completed" and "unknown categories" in str(
            read(final, "message", "")
        ).lower():
            final = self._run_strategizer(encode_module(self.project, self.category))
        if str(read(final, "status")).lower() != "completed":
            raise PipelineError(
                f"the strategizer failed: {read(final, 'message') or 'no reason given'}",
                "CORPUS_TOO_SMALL means the corpus has no clusters; anything else, check the "
                "AutoGraph service log.",
            )
        return StageReport(
            stage, STATUS_DONE,
            str(read(final, "message") or "strategies stored"),
            {"clusters": read(final, "clusters_total", 0), "complexity": self.config.complexity},
        )

    def _run_strategizer(self, label: str) -> dict[str, Any]:
        stage = STAGE_STRATEGIZE
        assert self.autograph is not None
        try:
            started = self.autograph.analyze(self.project, self.config.complexity, [label])
        except ApiError as error:
            if error.status == 409:
                raise PipelineError(
                    f"the strategizer cannot start: {describe(error.body)}",
                    "a corpus build or another analysis is still running; wait and rerun.",
                ) from None
            raise
        job_id = read(started, "strategize_job_id")
        if not job_id:
            raise PipelineError(f"the strategizer returned no job id: {describe(started)}")
        return self._poll(
            stage,
            lambda: self.autograph.strategizer_job(job_id),
            lambda s: str(read(s, "status", "")).lower() in ("completed", "failed"),
            lambda s: (
                f"{read(s, 'clusters_done', 0)}/{read(s, 'clusters_total', 0)} cluster(s) "
                f"{read(s, 'message', '')}"
            ).strip(),
            self.config.strategize_timeout,
            f"strategizer job {job_id}",
        )

    # --- stage 4: ontology ---------------------------------------------------

    def _ours(self, strategy: dict[str, Any]) -> bool:
        module = encode_module(self.project, self.category)
        params = read(strategy, "parameters") or {}
        if params.get("module") in (module, self.category):
            return True
        cluster = str(read(strategy, "cluster_id", ""))
        return cluster.startswith((f"cluster_{module}_", f"cluster_{self.category}_"))

    def _ontology(self) -> StageReport:
        stage = STAGE_ONTOLOGY
        assert self.autograph is not None
        target = list(self.config.ontology)
        ours = [s for s in self.autograph.strategies() if self._ours(s)]
        if not ours:
            raise PipelineError(
                f"no strategy belongs to category {self.category}.",
                "the strategizer stage should have written one; check the AutoGraph service log.",
            )

        def matches(strategy: dict[str, Any]) -> bool:
            types = {str(t).upper() for t in read(strategy, "entity_types") or []}
            return read(strategy, "strategy_type") == FULL_GRAPH_RAG and types == set(target)

        todo = [s for s in ours if not matches(s)]
        clusters = [read(s, "cluster_id") for s in ours]
        if not todo:
            return StageReport(
                stage, STATUS_SKIPPED,
                f"{len(ours)} cluster(s) already extract {', '.join(target)}",
                {"clusters": clusters},
            )

        overview = self._overview()
        if overview.in_kg(self.category):
            wrong = ", ".join(
                f"{read(s, 'cluster_id')}={read(s, 'strategy_type')}"
                f"[{', '.join(read(s, 'entity_types') or [])}]"
                for s in todo
            )
            raise PipelineError(
                f"category {self.category} was imported under a different ontology ({wrong}).",
                "a patch after import changes nothing; pass --rebuild to rebuild the category.",
            )
        if not self.write:
            return self._plan(
                stage, f"would set {len(todo)} cluster(s) to {FULL_GRAPH_RAG} [{', '.join(target)}]",
                clusters=clusters,
            )

        for strategy in todo:
            cluster = read(strategy, "cluster_id")
            try:
                self.autograph.patch_strategy(cluster, FULL_GRAPH_RAG, target)
            except ApiError as error:
                if error.status == 409:
                    raise PipelineError(
                        f"cannot change {cluster}'s strategy while a build or orchestration runs.",
                        "wait for it to finish and rerun.",
                    ) from None
                raise
            self.on_progress(stage, f"{cluster}: {FULL_GRAPH_RAG} [{', '.join(target)}]")
        return StageReport(
            stage, STATUS_DONE,
            f"set {len(todo)} of {len(ours)} cluster(s) to {FULL_GRAPH_RAG} [{', '.join(target)}]",
            {"clusters": clusters},
        )

    # --- stage 5: knowledge graph -------------------------------------------

    def _knowledge_graph(self) -> StageReport:
        stage = STAGE_KG
        assert self.autograph is not None
        overview = self._overview()
        if overview.in_kg(self.category):
            return StageReport(
                stage, STATUS_SKIPPED,
                f"already built ({overview.entity_count} entities, "
                f"{overview.relationship_count} relationships)",
            )
        if not self.write:
            return self._plan(stage, f"would orchestrate the importer for {self.category}")

        started = self._orchestrate(self.category)
        if started is None:
            return StageReport(stage, STATUS_SKIPPED, "nothing to orchestrate: already in the knowledge graph")
        orchestration_id = read(started, "orchestration_id")
        if not orchestration_id:
            raise PipelineError(f"orchestration returned no id: {describe(started)}")
        self.on_progress(stage, f"orchestration {orchestration_id} started")

        def fetch() -> dict[str, Any]:
            try:
                return self.autograph.orchestration(orchestration_id)
            except ApiError as error:
                if error.status == 404:
                    raise PipelineError(
                        f"orchestration {orchestration_id} is no longer known to the service.",
                        "status is in-memory and a pod restart or a newer run evicts it; "
                        "rerun to see whether the knowledge graph landed.",
                    ) from None
                raise

        def summary(state: dict[str, Any]) -> str:
            return (
                f"{read(state, 'phase', '')}: {read(state, 'completed_jobs', 0)}/"
                f"{read(state, 'total_jobs', 0)} job(s), {read(state, 'entities_added', 0)} entities"
            )

        final = self._poll(
            stage,
            fetch,
            lambda s: str(read(s, "status", "")).lower() in ("completed", "failed", "cancelled"),
            summary,
            self.config.orchestrate_timeout,
            f"orchestration {orchestration_id}",
        )
        if str(read(final, "status")).lower() != "completed":
            failures = (read(final, "strategy_summary") or {}).get("failures") or []
            reasons = "; ".join(
                f"{read(f, 'rag_partition_id')}: {read(f, 'error_message', 'no reason')}" for f in failures
            )
            raise PipelineError(
                f"orchestration {orchestration_id} ended {read(final, 'status')}: "
                f"{read(final, 'message', '')}" + (f" ({reasons})" if reasons else "")
            )
        return StageReport(
            stage, STATUS_DONE,
            f"{read(final, 'completed_jobs', 0)} partition(s) imported, "
            f"{read(final, 'entities_added', 0)} entities added",
            {"orchestration_id": orchestration_id},
        )

    def _orchestrate(self, label: str, fallback: bool = True) -> dict[str, Any] | None:
        assert self.autograph is not None
        try:
            return self.autograph.orchestrate(
                self.project, [label], self.config.replicas, self.config.max_retries
            )
        except ApiError as error:
            if error.status == 409 and "nothing to orchestrate" in error.text.lower():
                return None
            if error.status == 409:
                raise PipelineError(
                    f"orchestration cannot start: {describe(error.body)}",
                    "another orchestration holds the project's slot; wait for it and rerun.",
                ) from None
            if error.status == 400 and fallback:
                return self._orchestrate(encode_module(self.project, self.category), fallback=False)
            raise PipelineError(f"AutoGraph refused the orchestration: {describe(error.body)}") from None

    # --- stage 6: PlanGraph --------------------------------------------------

    def _plangraph(self) -> StageReport:
        stage = STAGE_PLANGRAPH
        if self.build_plangraph is None:
            return StageReport(stage, STATUS_SKIPPED, "not requested")
        if not self.write:
            return self._plan(stage, f"would run the bridge over {self.project}_kg")

        def relay(station: str, report: Any) -> None:
            if report is not None:
                self.on_progress(stage, f"{station}: {report.detail}")

        built = self.build_plangraph(self.project, relay)
        payload = built.to_dict() if hasattr(built, "to_dict") else dict(built)
        self.result.plangraph = payload
        if not payload.get("ok"):
            raise PipelineError(
                f"the bridge could not build the PlanGraph: {payload.get('failed') or 'no skill scope written'}"
            )
        return StageReport(
            stage, STATUS_DONE,
            f"wrote {len(payload.get('scopes') or [])} skill scope(s) into {payload.get('graph')}",
            {"scopes": payload.get("scopes") or []},
        )


def run_pipeline(*args: Any, **kwargs: Any) -> PipelineResult:
    return Pipeline(*args, **kwargs).run()


__all__ = [
    "EXIT_PIPELINE",
    "Overview",
    "Pipeline",
    "PipelineError",
    "PipelineResult",
    "Rulebook",
    "STAGES",
    "STAGE_CONNECT",
    "STAGE_CORPUS",
    "STAGE_KG",
    "STAGE_ONTOLOGY",
    "STAGE_PLANGRAPH",
    "STAGE_STRATEGIZE",
    "STAGE_UPLOAD",
    "STATUS_DONE",
    "STATUS_FAILED",
    "STATUS_PLANNED",
    "STATUS_SKIPPED",
    "StageReport",
    "encode_module",
    "run_pipeline",
]
