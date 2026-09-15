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
from typing import Any

from kg_read_harness.errors import ConfigError, HarnessError

#: The one stage this module owns. The rest are reported by the pipeline itself
#: through `on_stage`, so the page names real boundaries rather than a progress
#: bar invented to look busy.
STAGE_FETCHING = "fetching captions"

STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"

#: Jobs are kept so the page can poll them. Bounded: this is a demo server, and
#: an unbounded dict of past generations is a slow leak.
MAX_JOBS = 32


@dataclass
class Job:
    id: str
    state: str = STATE_RUNNING
    stage: str = STAGE_FETCHING
    result: dict[str, Any] | None = None
    error: str = ""
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "stage": self.stage,
            "source": self.source,
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

    def start(self, url: str = "", transcript_text: str = "") -> dict[str, Any]:
        """Begin a generation. Returns the job to poll."""
        url = (url or "").strip()
        transcript_text = (transcript_text or "").strip()
        if not url and not transcript_text:
            return {"error": "Give a video link, or paste a transcript."}

        try:
            self.config()
        except ConfigError as error:
            return {"error": error.message, "hint": error.hint}

        job = Job(id=uuid.uuid4().hex[:12], source=url or "pasted transcript")
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > MAX_JOBS:
                self._jobs.pop(self._order.pop(0), None)

        thread = threading.Thread(
            target=self._run, args=(job, url, transcript_text), daemon=True
        )
        thread.start()
        return job.to_dict()

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return job.to_dict() if job else None

    def _run(self, job: Job, url: str, transcript_text: str) -> None:
        from rulebook_generator.cache import PayloadCache
        from rulebook_generator.pipeline import generate
        from rulebook_generator.transcript import Transcript, clean, from_youtube

        try:
            config = self.config()

            job.stage = STAGE_FETCHING
            if transcript_text:
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

            result = generate(
                transcript,
                config,
                provider=self.provider,
                cache=PayloadCache(config.cache_path, config.cache_enabled),
                on_stage=lambda stage: setattr(job, "stage", stage),
            )

            job.result = self._serialize(result, config)
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
        payload["writable"] = result.verdict in config.accepts
        payload["filename"] = (
            f"rulebook_{result.rulebook.skill}.md" if result.rulebook else "rulebook.md"
        )
        return payload

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
