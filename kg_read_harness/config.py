"""Configuration, loaded exclusively from environment variables (FR-1, Section 8).

Two rules govern this module: fail fast naming any missing required variable, and
never echo a secret value. `Config.describe()` is the only thing printed, and it
carries no credentials.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from .errors import ConfigError

#: Variables that must always be present (Section 8, "Req. = yes").
REQUIRED_VARS = ("ARANGO_URL", "ARANGO_DB", "PROJECT_NAME")

#: Never printed, never logged.
SECRET_VARS = ("ARANGO_PASSWORD", "ARANGO_AUTH_TOKEN")

VALID_OUTPUT_FORMATS = ("table", "json")

#: The AutoGraph ontology. Used only to sanity-check ENTITY_TYPE_FILTER values.
KNOWN_ENTITY_TYPES = ("SKILL", "PRIMITIVE", "OBJECT", "STATE")


@dataclass(frozen=True)
class Config:
    # connection
    url: str
    database: str
    username: str | None
    password: str | None
    auth_token: str | None
    # naming
    project_name: str
    entity_collection: str
    relation_collection: str
    entity_name_field: str
    entity_type_field: str
    relation_type_field: str
    description_field: str
    # read shaping
    relation_type: str
    entity_type_filter: tuple[str, ...] = ()
    # output
    output_format: str = "table"
    limit: int = 0

    @property
    def uses_token_auth(self) -> bool:
        return bool(self.auth_token)

    def describe(self) -> list[tuple[str, str]]:
        """Effective settings, safe to print: no secret ever appears here."""
        return [
            ("endpoint", self.url),
            ("database", self.database),
            ("auth", "bearer token" if self.uses_token_auth else f"basic ({self.username})"),
            ("entities", self.entity_collection),
            ("relations", self.relation_collection),
            ("relation type", f"{self.relation_type_field} == {self.relation_type}"),
            ("entity type field", self.entity_type_field),
            ("description field", self.description_field),
            (
                "entity type filter",
                ", ".join(self.entity_type_filter) if self.entity_type_filter else "(none)",
            ),
            ("limit", "all" if self.limit == 0 else str(self.limit)),
            ("output format", self.output_format),
        ]


def _clean(env: Mapping[str, str], name: str) -> str | None:
    """Fetch a variable, treating whitespace-only as absent."""
    raw = env.get(name)
    if raw is None:
        return None
    stripped = raw.strip()
    return stripped or None


def _optional(env: Mapping[str, str], name: str, default: str) -> str:
    value = _clean(env, name)
    return value if value is not None else default


def _parse_limit(env: Mapping[str, str]) -> int:
    raw = _clean(env, "LIMIT")
    if raw is None:
        return 0
    try:
        limit = int(raw)
    except ValueError:
        raise ConfigError(
            f"LIMIT must be an integer, got {raw!r}.",
            "set LIMIT to a positive row count, or 0 / leave it unset to read everything.",
        ) from None
    if limit < 0:
        raise ConfigError(
            f"LIMIT must be zero or positive, got {limit}.",
            "0 (or unset) means no limit.",
        )
    return limit


def _parse_entity_type_filter(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = _clean(env, "ENTITY_TYPE_FILTER")
    if raw is None:
        return ()
    # Case is preserved for display but ignored when matching: AutoGraph builds
    # have used both 'PRIMITIVE' and 'tool'-style lowercase type values.
    types = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not types:
        raise ConfigError(
            "ENTITY_TYPE_FILTER is set but lists no types.",
            "use a comma list such as SKILL,PRIMITIVE,OBJECT,STATE, or unset it.",
        )
    return types


def _parse_output_format(env: Mapping[str, str]) -> str:
    fmt = _optional(env, "OUTPUT_FORMAT", "table").lower()
    if fmt not in VALID_OUTPUT_FORMATS:
        raise ConfigError(
            f"OUTPUT_FORMAT must be one of {' | '.join(VALID_OUTPUT_FORMATS)}, got {fmt!r}.",
            "unset OUTPUT_FORMAT for the default table view.",
        )
    return fmt


def _check_required(env: Mapping[str, str]) -> None:
    missing = [name for name in REQUIRED_VARS if _clean(env, name) is None]
    if missing:
        raise ConfigError(
            "missing required environment variable(s): " + ", ".join(missing) + ".",
            "export them (see .env.example) before running; nothing was connected to.",
        )


def _resolve_auth(env: Mapping[str, str]) -> tuple[str | None, str | None, str | None]:
    """Either username/password or a bearer token must be supplied (Section 8)."""
    token = _clean(env, "ARANGO_AUTH_TOKEN")
    username = _clean(env, "ARANGO_USERNAME")
    # An empty password is legitimate (a fresh root user), so presence of the
    # variable — not its content — is what counts.
    password = env.get("ARANGO_PASSWORD")

    if token:
        return None, None, token

    missing = []
    if username is None:
        missing.append("ARANGO_USERNAME")
    if password is None:
        missing.append("ARANGO_PASSWORD")
    if missing:
        raise ConfigError(
            "missing required environment variable(s): " + ", ".join(missing) + ".",
            "supply ARANGO_USERNAME + ARANGO_PASSWORD, or ARANGO_AUTH_TOKEN instead.",
        )
    return username, password, None


def load_config(env: Mapping[str, str] | None = None) -> Config:
    """Build a Config from the environment, failing fast with a named variable."""
    env = os.environ if env is None else env

    _check_required(env)
    username, password, token = _resolve_auth(env)

    project = _clean(env, "PROJECT_NAME")
    assert project is not None  # guaranteed by _check_required

    config = Config(
        url=_clean(env, "ARANGO_URL"),  # type: ignore[arg-type]
        database=_clean(env, "ARANGO_DB"),  # type: ignore[arg-type]
        username=username,
        password=password,
        auth_token=token,
        project_name=project,
        entity_collection=_optional(env, "ENTITY_COLLECTION", f"{project}_Entities"),
        relation_collection=_optional(env, "RELATION_COLLECTION", f"{project}_Relations"),
        entity_name_field=_optional(env, "ENTITY_NAME_FIELD", "name"),
        entity_type_field=_optional(env, "ENTITY_TYPE_FIELD", "entity_type"),
        relation_type_field=_optional(env, "RELATION_TYPE_FIELD", "type"),
        description_field=_optional(env, "DESCRIPTION_FIELD", "description"),
        relation_type=_optional(env, "RELATION_TYPE", "RELATED_TO"),
        entity_type_filter=_parse_entity_type_filter(env),
        output_format=_parse_output_format(env),
        limit=_parse_limit(env),
    )
    return config


def unknown_filter_types(config: Config) -> tuple[str, ...]:
    """Filter values outside the AutoGraph ontology — a warning, not an error."""
    return tuple(t for t in config.entity_type_filter if t.upper() not in KNOWN_ENTITY_TYPES)
