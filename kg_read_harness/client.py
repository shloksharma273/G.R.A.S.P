"""Read-only ArangoDB access (FR-2) plus the guard that keeps it that way.

The single most important constraint in the PRD (Section 10) is that this harness
never writes. Two things enforce it here:

1. `assert_read_only()` rejects any AQL text containing a mutating operation, and
   `run_query()` is the only path through which the harness talks to the server.
2. Nothing in this module calls a python-arango method that mutates; only
   `aql.execute`, `has_collection` and `collection(...).properties()` are used.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterator, Mapping

from .config import Config
from .errors import (
    AuthError,
    ConnectivityError,
    HarnessError,
    ReadOnlyViolation,
)

#: AQL operations that could change data or schema. Matched case-insensitively on
#: word boundaries, so an entity named "update_stove" in a bind value is fine —
#: bind values never reach this check, only the query text does.
_WRITE_OPERATIONS = (
    "INSERT",
    "UPDATE",
    "REPLACE",
    "REMOVE",
    "UPSERT",
    "TRUNCATE",
    "CREATE",
    "DROP",
)

_WRITE_PATTERN = re.compile(r"\b(" + "|".join(_WRITE_OPERATIONS) + r")\b", re.IGNORECASE)

#: Server-side cursor batch size; keeps memory bounded on 10^4-scale graphs.
BATCH_SIZE = 500


def assert_read_only(query: str) -> None:
    """Raise if `query` contains anything that could mutate the KG."""
    found = _WRITE_PATTERN.search(query)
    if found:
        raise ReadOnlyViolation(
            f"refusing to execute a query containing {found.group(1).upper()}.",
            "this is a bug in the harness, not a configuration problem: the harness "
            "is read-only by contract and must never issue write or DDL operations.",
        )


def connect(config: Config):
    """Open a connection to the project database and verify it works.

    Raises AuthError for rejected credentials and ConnectivityError for an
    unreachable endpoint (Section 12), never printing the credentials themselves.
    """
    try:
        from arango import ArangoClient
    except ImportError:  # pragma: no cover - environment problem, not logic
        raise HarnessError(
            "the python-arango client library is not installed.",
            "pip install -r requirements.txt (the harness needs exactly one dependency).",
        ) from None

    # urllib3 logs a WARNING per connection retry; our own message is clearer.
    logging.getLogger("urllib3.connectionpool").setLevel(logging.ERROR)

    client = ArangoClient(hosts=config.url)
    try:
        if config.uses_token_auth:
            db = client.db(config.database, user_token=config.auth_token, verify=True)
        else:
            db = client.db(
                config.database,
                username=config.username,
                password=config.password,
                verify=True,
            )
    except Exception as exc:  # python-arango wraps transport + HTTP errors
        raise _classify_connection_error(exc, config) from exc
    return db


def _classify_connection_error(exc: Exception, config: Config) -> HarnessError:
    text = str(exc).lower()
    http_code = getattr(exc, "http_code", None)

    if http_code in (401, 403) or "unauthorized" in text or "bad username" in text:
        who = "the supplied bearer token" if config.uses_token_auth else f"user {config.username!r}"
        return AuthError(
            f"ArangoDB rejected {who} for database {config.database!r}.",
            "check ARANGO_USERNAME / ARANGO_PASSWORD (or ARANGO_AUTH_TOKEN) and that "
            "the user has read access to that database. Credentials are not printed.",
        )
    if http_code == 404 or "database not found" in text:
        return ConnectivityError(
            f"database {config.database!r} does not exist at {config.url}.",
            "check ARANGO_DB against the database list in the ArangoDB web UI.",
        )
    return ConnectivityError(
        f"could not reach {config.url} ({exc.__class__.__name__}: {exc}).",
        "confirm ArangoDB is running and the URL/port in ARANGO_URL is reachable "
        "(e.g. curl the endpoint); check VPN or container port mapping.",
    )


def run_query(
    db: Any,
    query: str,
    bind_vars: Mapping[str, Any] | None = None,
    stream: bool = True,
) -> Iterator[Any]:
    """Execute a read-only AQL query and yield rows from a server-side cursor."""
    assert_read_only(query)
    try:
        cursor = db.aql.execute(
            query,
            bind_vars=dict(bind_vars or {}),
            batch_size=BATCH_SIZE,
            stream=stream,
        )
    except Exception as exc:
        if isinstance(exc, HarnessError):
            raise
        raise _classify_query_error(exc) from exc
    return iter(cursor)


def _classify_query_error(exc: Exception) -> HarnessError:
    text = str(exc).lower()
    http_code = getattr(exc, "http_code", None)
    if http_code in (401, 403) or "unauthorized" in text:
        return AuthError(
            "the database user is not permitted to read the configured collections.",
            "grant the user read access to the project database.",
        )
    return HarnessError(
        f"AQL read failed: {exc}",
        "verify the collection and attribute names in Section 8 config against the "
        "live instance (ArangoDB Graph Explorer shows both).",
    )


def collection_exists(db: Any, name: str) -> bool:
    return bool(db.has_collection(name))


def collection_properties(db: Any, name: str) -> dict[str, Any]:
    return dict(db.collection(name).properties())
