"""The Skills vector index (FR-6) via AutoGraph's embed-field endpoint.

Section 11 is explicit about what happens when the endpoint is unreachable:
"Write the graph anyway; mark the vector index as pending and report it (planning
goal-resolution degrades until built)." So a missing or failing endpoint is never
an error here — the PlanGraph is still written and still traversable, and only
goal resolution is degraded until the index exists.

The endpoint's exact contract is AutoGraph's, not ours, so the URL and payload
shape are configuration rather than an assumption baked into code. With
`AUTOGRAPH_URL` unset the index is simply reported as `not_configured`, which is
the honest state rather than a silent success.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from .config import WriterConfig
from .records import WriteReport

#: AutoGraph's endpoint for building an embedding + index + view over a field.
EMBED_PATH = "/embed-field-in-collection"

TIMEOUT_SECONDS = 120.0

STATUS_NOT_CONFIGURED = "not_configured"
STATUS_PENDING = "pending"
STATUS_BUILT = "built"
STATUS_SKIPPED = "skipped"


def build_payload(config: WriterConfig) -> dict[str, Any]:
    return {
        "database": config.arango.database,
        "collection": config.schema.skills_collection,
        "field": config.embedding_field,
    }


def ensure_vector_index(
    config: WriterConfig, report: WriteReport, dry_run: bool = False
) -> None:
    """Ask AutoGraph to (re)build the Skills embedding, index and view."""
    if dry_run:
        report.vector_index = STATUS_SKIPPED
        report.vector_index_detail = "dry run: the embed-field endpoint was not called"
        return

    if not config.autograph_url:
        report.vector_index = STATUS_NOT_CONFIGURED
        report.vector_index_detail = (
            "AUTOGRAPH_URL is not set, so no embedding was built. The PlanGraph is "
            "written and traversable; Layer 2 goal resolution by vector search will "
            "not work until the index exists."
        )
        return

    url = config.autograph_url.rstrip("/") + EMBED_PATH
    try:
        _post(url, build_payload(config), config.autograph_api_key)
    except Exception as error:
        # Section 11: never fail the write because the index could not be built.
        report.vector_index = STATUS_PENDING
        report.vector_index_detail = (
            f"the embed-field endpoint at {url} did not succeed "
            f"({error.__class__.__name__}: {error}). The graph is written; re-run "
            "Station 5 once the endpoint is reachable to build the index."
        )
        return

    report.vector_index = STATUS_BUILT
    report.vector_index_detail = (
        f"embedding, index and view requested for "
        f"{config.schema.skills_collection}.{config.embedding_field}"
    )


def _post(url: str, payload: dict[str, Any], api_key: str | None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise RuntimeError(f"HTTP {error.code}: {detail}") from None
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError:
        return {"raw": body[:300]}
