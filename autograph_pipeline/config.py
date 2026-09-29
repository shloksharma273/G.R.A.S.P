"""Configuration for the AutoGraph pipeline, from the environment.

It reuses the platform connection the rest of the bridge already reads -
`ARANGO_URL`, `ARANGO_DB` and the credentials - because the AutoGraph services,
File Manager and ACP all sit behind that same gateway. Everything else has a
default chosen for this project, not for AutoGraph in general:

* `complexity` is `very_high`, i.e. every cluster FullGraphRAG. A VectorRAG
  partition extracts no entities, and a knowledge graph with no entities has no
  SKILL or PRIMITIVE for the bridge to read. At AutoGraph's `moderate` a
  one-cluster module - the usual case for a few rulebooks - rounds to zero
  FullGraphRAG clusters.
* the ontology is the bridge's own, SKILL / PRIMITIVE / OBJECT / STATE. It is
  what Station 1 reads, so it is what the importer is told to extract.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from kg_read_harness.config import KNOWN_ENTITY_TYPES
from kg_read_harness.errors import ConfigError

COMPLEXITIES = ("very_low", "low", "moderate", "high", "very_high")

#: Model settings a new AutoGraph service is deployed with, as ACP names them on
#: a project record (camelCase) -> the env var ACP passes to the chart.
MODEL_FIELDS = {
    "chatApiProvider": "chat_api_provider",
    "embeddingApiProvider": "embedding_api_provider",
    "chatModel": "chat_model",
    "embeddingModel": "embedding_model",
    "chatSecretProfileId": "chat_secret_profile_id",
    "embeddingSecretProfileId": "embedding_secret_profile_id",
    "chatApiUrl": "chat_api_url",
    "embeddingApiUrl": "embedding_api_url",
}


@dataclass(frozen=True)
class PipelineConfig:
    url: str
    database: str
    username: str | None
    password: str | None
    auth_token: str | None
    #: Talk to this AutoGraph path instead of the one the project's record names.
    service_path: str | None = None
    complexity: str = "very_high"
    ontology: tuple[str, ...] = KNOWN_ENTITY_TYPES
    replicas: int = 1
    max_retries: int = 3
    poll_seconds: float = 10.0
    corpus_timeout: float = 2 * 3600.0
    strategize_timeout: float = 3600.0
    orchestrate_timeout: float = 6 * 3600.0
    deploy_timeout: float = 20 * 60.0
    #: Copy a new service's model settings from this existing project.
    model_from: str | None = None
    #: Explicit model settings for a new service (env var name -> value).
    model_env: dict[str, str] = field(default_factory=dict)
    fps_recovery_username: str | None = None

    def describe(self) -> list[tuple[str, str]]:
        """Effective settings, safe to print: no credential appears here."""
        return [
            ("endpoint", self.url),
            ("database", self.database),
            ("auth", "bearer token" if self.auth_token else f"password ({self.username})"),
            ("autograph", self.service_path or "from the project's ACP record"),
            ("complexity", self.complexity),
            ("ontology", ", ".join(self.ontology)),
            ("replicas", str(self.replicas)),
        ]


def _clean(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _number(env: Mapping[str, str], name: str, default: float, minimum: float = 0) -> float:
    raw = _clean(env, name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}.", f"unset {name} for the default.") from None
    if value < minimum:
        raise ConfigError(f"{name} must be at least {minimum:g}, got {raw}.", f"unset {name} for the default.")
    return value


def load_pipeline_config(env: Mapping[str, str] | None = None) -> PipelineConfig:
    env = os.environ if env is None else env

    missing = [name for name in ("ARANGO_URL", "ARANGO_DB") if _clean(env, name) is None]
    token = _clean(env, "ARANGO_AUTH_TOKEN")
    username = _clean(env, "ARANGO_USERNAME")
    if not token and (username is None or env.get("ARANGO_PASSWORD") is None):
        missing.append("ARANGO_USERNAME + ARANGO_PASSWORD (or ARANGO_AUTH_TOKEN)")
    if missing:
        raise ConfigError(
            "missing required environment variable(s): " + ", ".join(missing) + ".",
            "export them (see .env.example); nothing was connected to.",
        )

    complexity = (_clean(env, "AUTOGRAPH_COMPLEXITY") or "very_high").lower()
    if complexity not in COMPLEXITIES:
        raise ConfigError(
            f"AUTOGRAPH_COMPLEXITY must be one of {' | '.join(COMPLEXITIES)}, got {complexity!r}.",
            "unset it: very_high makes every cluster FullGraphRAG, which the bridge needs.",
        )

    raw_ontology = _clean(env, "AUTOGRAPH_ONTOLOGY")
    ontology = (
        tuple(dict.fromkeys(t.strip().upper() for t in raw_ontology.split(",") if t.strip()))
        if raw_ontology
        else KNOWN_ENTITY_TYPES
    )
    if not {"SKILL", "PRIMITIVE"} <= set(ontology):
        raise ConfigError(
            f"AUTOGRAPH_ONTOLOGY must include SKILL and PRIMITIVE, got {', '.join(ontology)}.",
            "without them nothing in the knowledge graph decomposes into steps.",
        )

    model_env = {
        var: value
        for var in MODEL_FIELDS.values()
        if (value := _clean(env, f"AUTOGRAPH_{var.upper()}")) is not None
    }

    return PipelineConfig(
        url=_clean(env, "ARANGO_URL"),  # type: ignore[arg-type]
        database=_clean(env, "ARANGO_DB"),  # type: ignore[arg-type]
        username=None if token else username,
        password=None if token else env.get("ARANGO_PASSWORD"),
        auth_token=token,
        service_path=_clean(env, "AUTOGRAPH_SERVICE_PATH"),
        complexity=complexity,
        ontology=ontology,
        replicas=int(_number(env, "AUTOGRAPH_REPLICAS", 1, minimum=1)),
        max_retries=int(_number(env, "AUTOGRAPH_MAX_RETRIES", 3)),
        poll_seconds=_number(env, "AUTOGRAPH_POLL_SECONDS", 10.0, minimum=0.0),
        corpus_timeout=_number(env, "AUTOGRAPH_CORPUS_TIMEOUT", 2 * 3600.0, minimum=1),
        strategize_timeout=_number(env, "AUTOGRAPH_STRATEGIZE_TIMEOUT", 3600.0, minimum=1),
        orchestrate_timeout=_number(env, "AUTOGRAPH_ORCHESTRATE_TIMEOUT", 6 * 3600.0, minimum=1),
        deploy_timeout=_number(env, "AUTOGRAPH_DEPLOY_TIMEOUT", 20 * 60.0, minimum=1),
        model_from=_clean(env, "AUTOGRAPH_MODEL_FROM"),
        model_env=model_env,
        fps_recovery_username=_clean(env, "AUTOGRAPH_FPS_RECOVERY_USERNAME"),
    )


__all__ = ["COMPLEXITIES", "MODEL_FIELDS", "PipelineConfig", "load_pipeline_config"]
