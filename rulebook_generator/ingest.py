"""Stage 5 — optional auto-ingest (PRD Section 6, FR-8).

On accept, push the rulebook to AutoGraph and trigger the bridge, making
link-in -> plan-out a single command.

AutoGraph's ingest API is AutoGraph's, not ours, so its URL and payload are
configuration rather than an assumption baked into code. With `AUTOGRAPH_URL`
unset the rulebook is written to disk and the step reports itself as not
configured - the honest state, and the same pattern Station 5 uses for the
embed-field endpoint.

FR-7 is enforced above this module: only an `accept` verdict ever reaches it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

INGEST_PATH = "/import-rulebook"
TIMEOUT_SECONDS = 120.0


def write_rulebook(markdown: str, skill: str, output_dir: str) -> str:
    """Save the rulebook where a human (or AutoGraph) can pick it up."""
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"rulebook_{skill}.md"
    path.write_text(markdown, encoding="utf-8")
    return str(path)


def ingest(markdown: str, skill: str, project: str | None = None) -> tuple[bool, str]:
    """Hand the rulebook to AutoGraph. Returns `(ingested, detail)`."""
    base = (os.environ.get("AUTOGRAPH_URL") or "").strip()
    if not base:
        return False, (
            "AUTOGRAPH_URL is not set, so the rulebook was written to disk but not "
            "ingested. Import it through the AutoGraph UI, or set AUTOGRAPH_URL to "
            "make link-in to plan-out one command."
        )

    url = base.rstrip("/") + INGEST_PATH
    payload = {"project": project or skill, "name": f"rulebook_{skill}.md", "content": markdown}
    headers = {"Content-Type": "application/json"}
    key = (os.environ.get("AUTOGRAPH_API_KEY") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:200]
        return False, f"AutoGraph rejected the rulebook (HTTP {error.code}): {detail}"
    except Exception as error:
        return False, f"could not reach {url} ({error.__class__.__name__}: {error})"
    return True, f"ingested to {url}"
