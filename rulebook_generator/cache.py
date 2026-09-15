"""A JSON payload cache keyed by content hash (FR-6).

The same shape as Station 3's verdict cache, holding raw extraction replies
rather than verdicts: re-running on the same video issues no call and returns the
same rulebook, which is half of what reproducibility means here.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

CACHE_VERSION = "1"


class PayloadCache:
    """content key -> the model's raw reply."""

    def __init__(self, path: str | None, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled and bool(path)
        self._entries: dict[str, Any] = {}
        self._dirty = False
        if self.enabled:
            self._load()

    def _load(self) -> None:
        try:
            with open(self.path, encoding="utf-8") as handle:  # type: ignore[arg-type]
                payload = json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return
        if isinstance(payload, dict) and isinstance(payload.get("entries"), dict):
            self._entries = payload["entries"]

    def get(self, key: str) -> Any:
        return self._entries.get(key) if self.enabled else None

    def put(self, key: str, value: Any) -> None:
        if not self.enabled:
            return
        self._entries[key] = value
        self._dirty = True

    def save(self) -> None:
        if not self.enabled or not self._dirty:
            return
        directory = os.path.dirname(os.path.abspath(self.path))  # type: ignore[arg-type]
        os.makedirs(directory, exist_ok=True)
        handle, temporary = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(
                    {"version": CACHE_VERSION, "entries": self._entries},
                    file, indent=2, ensure_ascii=False, sort_keys=True,
                )
            os.replace(temporary, self.path)  # type: ignore[arg-type]
            self._dirty = False
        except BaseException:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise

    def __len__(self) -> int:
        return len(self._entries)
