"""The result cache (FR-5) — what makes the batch job idempotent.

PRD Section 7: "A result cache keyed by a hash of (model id, primitive, state,
description); a cache hit skips the call." That key is also the deduplication key
of Section 11, so two identical (primitive, state, description) triples are
classified once and the verdict applied to both.

The cache is a plain JSON file, written atomically so an interrupted run cannot
leave a half-written file behind.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from typing import Any

from .model import Verdict

#: Bumped if the prompt or the label vocabulary changes in a way that would make
#: previously cached verdicts wrong. Old entries then simply miss.
CACHE_VERSION = "1"


def content_key(model: str, primitive: str, state: str, description: str) -> str:
    """The Section 7 content hash.

    Whitespace is normalized so a reflowed description does not masquerade as a
    different item.
    """
    payload = " ".join(
        (CACHE_VERSION, model, primitive.strip(), state.strip(), " ".join(description.split()))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class VerdictCache:
    """A content-hash to verdict store. Disabled instances are no-ops."""

    def __init__(self, path: str | None, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled and bool(path)
        self._entries: dict[str, dict[str, Any]] = {}
        self._dirty = False
        if self.enabled:
            self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:  # type: ignore[arg-type]
                payload = json.load(handle)
        except FileNotFoundError:
            return
        except (json.JSONDecodeError, OSError):
            # A corrupt cache is a performance problem, never a correctness one:
            # start empty and rewrite on save rather than failing the run.
            return
        if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
            self._entries = payload["entries"]

    def get(self, key: str) -> Verdict | None:
        if not self.enabled:
            return None
        entry = self._entries.get(key)
        if not entry:
            return None
        try:
            return Verdict(
                label=entry["label"],
                confidence=float(entry["confidence"]),
                rationale=entry.get("rationale", ""),
                method=entry.get("method", "llm"),
                model=entry.get("model"),
            )
        except (KeyError, TypeError, ValueError):
            return None  # an unreadable entry behaves as a miss

    def put(self, key: str, verdict: Verdict) -> None:
        if not self.enabled:
            return
        self._entries[key] = verdict.to_dict()
        self._dirty = True

    def save(self) -> None:
        """Atomically persist, if anything changed."""
        if not self.enabled or not self._dirty:
            return
        directory = os.path.dirname(os.path.abspath(self.path))  # type: ignore[arg-type]
        os.makedirs(directory, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(
                    {"version": CACHE_VERSION, "entries": self._entries},
                    file,
                    indent=2,
                    ensure_ascii=False,
                    sort_keys=True,
                )
            os.replace(temporary, self.path)  # type: ignore[arg-type]
            self._dirty = False
        except BaseException:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise

    def __len__(self) -> int:
        return len(self._entries)
