"""Background jobs the browser polls.

Two things in this server take long enough that a synchronous request would leave
the page holding a spinner with nothing to say: reconstructing a rulebook, and
building a PlanGraph. Both want the same shape — start the work, hand back an id,
report the stage it has reached — so they share one store rather than keeping two
copies of the same bookkeeping.

Bounded on purpose. This is a demo server, and an unbounded dict of past jobs is
a slow leak.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"

#: How many finished jobs to keep for polling before the oldest is dropped.
MAX_JOBS = 32


@dataclass
class Job:
    id: str
    state: str = STATE_RUNNING
    stage: str = ""
    result: dict[str, Any] | None = None
    error: str = ""
    source: str = ""
    #: Anything the runner wants the page to see before the result is ready -
    #: which pages were read, which stations have reported.
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


class JobStore:
    """Start jobs on background threads and report on them."""

    def __init__(self, max_jobs: int = MAX_JOBS) -> None:
        self.max_jobs = max_jobs
        self._jobs: dict[str, Job] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    def create(self, stage: str = "", source: str = "") -> Job:
        job = Job(id=uuid.uuid4().hex[:12], stage=stage, source=source)
        with self._lock:
            self._jobs[job.id] = job
            self._order.append(job.id)
            while len(self._order) > self.max_jobs:
                self._jobs.pop(self._order.pop(0), None)
        return job

    def run(self, job: Job, target: Callable[[Job], None]) -> Job:
        """Run `target(job)` on a daemon thread. Exceptions become job errors."""
        thread = threading.Thread(target=self._guard, args=(job, target), daemon=True)
        thread.start()
        return job

    @staticmethod
    def _guard(job: Job, target: Callable[[Job], None]) -> None:
        import traceback

        from kg_read_harness.errors import HarnessError

        try:
            target(job)
        except HarnessError as error:
            job.state = STATE_ERROR
            job.error = error.render()
        except Exception as error:  # a bad request must not take the server down
            job.state = STATE_ERROR
            job.error = f"{error.__class__.__name__}: {error}"
            traceback.print_exc()

    def status(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
        return job.to_dict() if job else None

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)
