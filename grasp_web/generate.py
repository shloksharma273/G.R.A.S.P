"""The generator half of the API — a video link becomes a rulebook.

Unlike planning, this is slow: fetching captions takes a moment and the
schema-constrained extraction takes a minute or two. A synchronous request would
leave the browser staring at a spinner with nothing to say, so a generation runs
as a **job**: the POST starts it and returns an id, and the page polls for the
stage it has reached.

As with the planning API, nothing here decides anything. The verdict, the issues
and the implied plan all come from `rulebook_generator`'s own validation gate;
this module starts the work, names the stage it is in, and serializes the result.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from kg_read_harness.errors import ConfigError, HarnessError

#: The one stage this module owns. The rest are reported by the pipeline itself
#: through `on_stage`, so the page names real boundaries rather than a progress
#: bar invented to look busy.
STAGE_FETCHING = "fetching captions"

#: The repository page's own first stage: listing and downloading the chosen
#: pages before there is any text to reconstruct from.
STAGE_READING_REPO = "reading the repository"

STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"

#: Jobs are kept so the page can poll them. Bounded: this is a demo server, and
#: an unbounded dict of past generations is a slow leak.
MAX_JOBS = 32


def unused_pages(document: str, paths: list[str], rulebook: dict[str, Any] | None) -> list[str]:
    """Chosen pages that no primitive was drawn from.

    A live run showed why this is needed: four PX4 pages went in - arming,
    takeoff, return, landing - and a rulebook for arming alone came out, with the
    model judging the rest a different procedure and the gate raising nothing,
    because "the rulebook covers less than you selected" is not a defect in the
    rulebook. It is still something the person who picked those pages has to be
    told, or the page reports four pages read and shows one procedure's worth of
    steps without ever saying they are not the same thing.

    A page counts as used when *every* word of some primitive's name appears in
    it. The gate's own grounding check is looser - any one word - because it asks
    the opposite question, whether an action was invented, and there a single hit
    is evidence. Here a single hit proves nothing: "vehicle" appears on every page
    of a drone manual, so a one-word rule would call every page used and report
    nothing, ever.
    """
    if not rulebook or not paths:
        return []

    from rulebook_generator.validate import normalize_name

    names = [
        [token for token in normalize_name(p.get("name", "")).split("_") if token]
        for p in rulebook.get("primitives") or []
    ]
    names = [name for name in names if name]
    if not names:
        return []

    # repo.py writes each page as `# <path>` followed by its body, so the
    # headings are where one page ends and the next begins.
    marks = []
    for path in paths:
        index = document.find(f"# {path}")
        if index >= 0:
            marks.append((index, path))
    marks.sort()

    unused = []
    for position, (start, path) in enumerate(marks):
        end = marks[position + 1][0] if position + 1 < len(marks) else len(document)
        words = set(normalize_name(document[start:end]).split("_"))
        if not any(all(token in words for token in name) for name in names):
            unused.append(path)
    return unused


@dataclass
class Job:
    id: str
    state: str = STATE_RUNNING
    stage: str = STAGE_FETCHING
    result: dict[str, Any] | None = None
    error: str = ""
    source: str = ""
    #: What the source turned out to be, for a source that had to be assembled -
    #: which repo, which ref, which pages were actually read.
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "stage": self.stage,
            "source": self.source,
            "detail": self.detail,
            "result": self.result,
            "error": self.error,
        }


@dataclass
class GeneratorService:
    """Runs generations in the background and reports on them.

    The config is loaded lazily rather than at construction: without an LLM key
    the rest of the server should still serve the planning UI, and the generator
    page should say why it cannot run instead of the process failing to start.
    """

    provider: Any = None
    _jobs: dict[str, Job] = field(default_factory=dict)
    _order: list[str] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # --- configuration ------------------------------------------------------

    def config(self):
        from rulebook_generator.config import load_generator_config

        return load_generator_config()

    def health(self) -> dict[str, Any]:
        """What the generator is configured to do, or why it cannot run."""
        try:
            config = self.config()
        except ConfigError as error:
            return {
                "available": False,
                "reason": error.message,
                "hint": error.hint,
                "youtube": self._youtube_available(),
            }
        return {
            "available": True,
            "model": config.llm.model,
            "endpoint": config.llm.base_url,
            "strictness": config.strictness,
            "writes": list(config.accepts),
            "output_dir": config.output_dir,
            "cache": config.cache_path if config.cache_enabled else None,
            "youtube": self._youtube_available(),
        }

    @staticmethod
    def _youtube_available() -> bool:
        """Whether live caption fetching is possible (an optional dependency)."""
        try:
            import youtube_transcript_api  # noqa: F401
        except ImportError:
            return False
        return True

    # --- jobs ---------------------------------------------------------------

    def start(
        self,
        url: str = "",
        transcript_text: str = "",
        prepare: Callable[[], dict[str, Any]] | None = None,
        prepare_stage: str = STAGE_FETCHING,
        source_label: str = "",
        register: str = "manual",
        split: bool = False,
    ) -> dict[str, Any]:
        """Begin a generation. Returns the job to poll.

        `split` generates a reference rulebook of the whole source and cuts one
        rulebook per task out of it (see `rulebook_generator.split`).

        `prepare` is for a source that has to be assembled before it can be read -
        a set of pages from a documentation repo, say. It runs inside the job
        thread, under `prepare_stage`, and returns `{"text", "source_url", ...}`.
        Doing it here rather than in the POST keeps the slow part behind the same
        polling the extraction already uses, so the page can say what it is doing
        instead of holding a request open.

        Text that arrives through `prepare` is documentation, not speech, so it is
        cleaned and prompted as a manual (see `rulebook_generator.transcript`).
        """
        url = (url or "").strip()
        transcript_text = (transcript_text or "").strip()
        if prepare is None and not url and not transcript_text:
            return {"error": "Give a video link, or paste a transcript."}

        try:
            self.config()
        except ConfigError as error:
            return {"error": error.message, "hint": error.hint}

        job = Job(
            id=uuid.uuid4().hex[:12],
            stage=prepare_stage,
            source=source_label or url or "pasted transcript",
        )
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > MAX_JOBS:
                self._jobs.pop(self._order.pop(0), None)

        thread = threading.Thread(
            target=self._run,
            args=(job, url, transcript_text, prepare, prepare_stage, register, split),
            daemon=True,
        )
        thread.start()
        return job.to_dict()

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return job.to_dict() if job else None

    def _run(
        self,
        job: Job,
        url: str,
        transcript_text: str,
        prepare: Callable[[], dict[str, Any]] | None = None,
        prepare_stage: str = STAGE_FETCHING,
        register: str = "manual",
        split: bool = False,
    ) -> None:
        from rulebook_generator.cache import PayloadCache
        from rulebook_generator.pipeline import generate
        from rulebook_generator.split import generate_split
        from rulebook_generator.transcript import Transcript, clean, from_youtube

        try:
            config = self.config()

            job.stage = prepare_stage if prepare is not None else STAGE_FETCHING
            if prepare is not None:
                prepared = prepare()
                text = clean(str(prepared.get("text") or ""), caption_artifacts=False)
                if not text:
                    raise HarnessError(
                        "those pages contain no readable text.",
                        "choose pages that describe a procedure.",
                    )
                transcript = Transcript(
                    text=text,
                    video_id=str(prepared.get("source_url") or job.source),
                    # Source code and documentation are read with different
                    # prompts: a manual states its preconditions, code states its
                    # call names and little else.
                    source="code" if register == "code" else "manual",
                    url=str(prepared.get("source_url") or ""),
                )
                job.detail = {
                    key: prepared[key]
                    for key in ("repo", "ref", "paths", "skipped")
                    if key in prepared
                }
            elif transcript_text:
                text = clean(transcript_text)
                if not text:
                    raise HarnessError(
                        "that transcript has no usable words in it.",
                        "paste the spoken words, one cue per line or as prose.",
                    )
                transcript = Transcript(
                    text=text, video_id=url or "pasted", source="pasted", url=url
                )
            else:
                transcript = from_youtube(url)

            run = generate_split if split else generate
            result = run(
                transcript,
                config,
                provider=self.provider,
                cache=PayloadCache(config.cache_path, config.cache_enabled),
                on_stage=lambda stage: setattr(job, "stage", stage),
            )

            job.result = (
                self._serialize_split(result, config) if split else self._serialize(result, config)
            )
            reference = job.result.get("reference", job.result) if split else job.result
            if prepare is not None and job.detail.get("paths"):
                job.detail["unused"] = unused_pages(
                    transcript.text,
                    list(job.detail["paths"]),
                    (reference.get("extraction") or {}).get("intermediate"),
                )
            job.state = STATE_DONE

        except HarnessError as error:
            job.state = STATE_ERROR
            job.error = error.render()
        except Exception as error:  # a bad link must not take the server down
            job.state = STATE_ERROR
            job.error = f"{error.__class__.__name__}: {error}"
            traceback.print_exc()

    # --- serialization ------------------------------------------------------

    @staticmethod
    def _serialize(result: Any, config: Any) -> dict[str, Any]:
        payload = result.to_dict()
        payload["markdown"] = result.markdown
        payload["source_kind"] = (
            result.transcript.source
            if result.transcript and result.transcript.source in ("manual", "code")
            else "video"
        )
        payload["writable"] = result.verdict in config.accepts
        payload["filename"] = (
            f"rulebook_{result.rulebook.skill}.md" if result.rulebook else "rulebook.md"
        )
        return payload

    @classmethod
    def _serialize_split(cls, split: Any, config: Any) -> dict[str, Any]:
        """A reference rulebook and the task rulebooks cut from it."""
        tasks = [cls._serialize(task, config) for task in split.tasks]
        return {
            "kind": "split",
            "reference": cls._serialize(split.reference, config),
            "tasks": tasks,
            "method": split.choice.method if split.choice else None,
            "reprompted": bool(split.choice and split.choice.reprompted),
            "fallback_reason": split.choice.fallback_reason if split.choice else "",
            "assumes": {
                task.skill: list(task.assumes) for task in (split.choice.tasks if split.choice else [])
            },
            "reason": split.reason,
            "accepted": sum(1 for t in tasks if t["verdict"] == "accept"),
        }

    def rulebook_files(self, job_id: str, filenames: list[str]) -> list[tuple[str, str]]:
        """The markdown of chosen rulebooks from a finished generation.

        The build reads them from here rather than from the browser, so what is
        uploaded to AutoGraph is exactly what the gate graded.
        """
        job = self._jobs.get(job_id)
        if job is None or job.state != STATE_DONE or not job.result:
            raise HarnessError(
                "that generation is not available any more.",
                "generate the rulebooks again; finished generations are kept only for a while.",
            )
        result = job.result
        books = (
            [result["reference"], *result["tasks"]] if result.get("kind") == "split" else [result]
        )
        by_name = {b["filename"]: b["markdown"] for b in books if b.get("markdown")}
        missing = [name for name in filenames if name not in by_name]
        if missing:
            raise HarnessError(
                "the generation has no rulebook named " + ", ".join(missing) + ".",
                "choose from the rulebooks the generation produced.",
            )
        return [(name, by_name[name]) for name in filenames]

    def library_files(self, filenames: list[str]) -> list[tuple[str, str]]:
        """The markdown of chosen rulebooks already on disk, by file name only.

        Names are matched against the directory listing rather than joined onto a
        path, so a name like `../.env` can never be read.
        """
        on_disk = {row["filename"]: row["markdown"] for row in self.rulebooks()}
        missing = [name for name in filenames if name not in on_disk]
        if missing:
            raise HarnessError(
                "no rulebook on disk is named " + ", ".join(missing) + ".",
                "choose from the rulebooks listed on the page.",
            )
        return [(name, on_disk[name]) for name in filenames]

    # --- what has already been generated ------------------------------------

    def rulebooks(self) -> list[dict[str, Any]]:
        """Rulebooks already on disk, so a demo can reopen one without re-running."""
        try:
            directory = Path(self.config().output_dir)
        except ConfigError:
            return []
        if not directory.is_dir():
            return []

        rows = []
        for path in sorted(directory.glob("rulebook_*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            rows.append(
                {
                    "name": path.stem.replace("rulebook_", ""),
                    "filename": path.name,
                    "steps": text.count("\n**"),
                    "markdown": text,
                }
            )
        return rows
