"""Station 5 configuration (PRD Section 10, "config-driven").

Collection prefix, embedding field, task scope and the connection all come from
the environment. Section 12 notes that the writing user is "distinct from the
read-only user Station 1 may use", so write credentials can be supplied
separately and fall back to Station 1's when they are not.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Mapping

from kg_read_harness.config import Config, load_config
from kg_read_harness.errors import ConfigError

from .schema import EMBEDDING_FIELD, Schema

SECRET_VARS = ("ARANGO_PASSWORD", "ARANGO_WRITE_PASSWORD", "AUTOGRAPH_API_KEY")


@dataclass(frozen=True)
class WriterConfig:
    #: Station 1's connection settings, with write credentials applied.
    arango: Config
    prefix: str
    skill_scope: str | None
    build_id: str | None
    embedding_field: str
    autograph_url: str | None
    autograph_api_key: str | None
    dry_run: bool

    @property
    def schema(self) -> Schema:
        return Schema(prefix=self.prefix)

    @property
    def writes_as(self) -> str:
        return self.arango.username or "(bearer token)"

    def describe(self) -> list[tuple[str, str]]:
        """Effective settings, safe to print: no credential appears here."""
        schema = self.schema
        return [
            ("endpoint", self.arango.url),
            ("database", self.arango.database),
            ("writes as", self.writes_as),
            ("vertex collections", ", ".join(schema.vertex_collections)),
            ("edge collection", schema.edge_collection),
            ("named graph", schema.graph_name),
            ("skill scope", self.skill_scope or "(inferred from the input)"),
            ("embedding field", f"{schema.skills_collection}.{self.embedding_field}"),
            ("embed-field endpoint", self.autograph_url or "(not configured)"),
            ("mode", "DRY RUN - nothing is written" if self.dry_run else "write"),
        ]


def _clean(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name)
    if raw is None:
        return None
    return raw.strip() or None


def load_writer_config(
    env: Mapping[str, str] | None = None, dry_run: bool = False
) -> WriterConfig:
    env = os.environ if env is None else env
    arango = load_config(env)

    # Section 12: the writing user may differ from the reading one.
    write_user = _clean(env, "ARANGO_WRITE_USERNAME")
    if write_user:
        password = env.get("ARANGO_WRITE_PASSWORD")
        if password is None:
            raise ConfigError(
                "ARANGO_WRITE_USERNAME is set but ARANGO_WRITE_PASSWORD is not.",
                "supply both, or neither to reuse the Station 1 credentials.",
            )
        arango = replace(arango, username=write_user, password=password, auth_token=None)

    prefix = _clean(env, "PLANGRAPH_PREFIX") or arango.project_name
    if prefix in (arango.entity_collection, arango.relation_collection):
        raise ConfigError(
            f"PLANGRAPH_PREFIX={prefix!r} would collide with AutoGraph's collections.",
            "choose a prefix whose collections are distinct from "
            f"{arango.entity_collection!r} and {arango.relation_collection!r}; the "
            "PlanGraph must survive an AutoGraph rebuild.",
        )

    return WriterConfig(
        arango=arango,
        prefix=prefix,
        skill_scope=_clean(env, "SKILL_SCOPE"),
        build_id=_clean(env, "PLANGRAPH_BUILD_ID"),
        embedding_field=_clean(env, "PLANGRAPH_EMBEDDING_FIELD") or EMBEDDING_FIELD,
        autograph_url=_clean(env, "AUTOGRAPH_URL"),
        autograph_api_key=_clean(env, "AUTOGRAPH_API_KEY"),
        dry_run=dry_run,
    )
