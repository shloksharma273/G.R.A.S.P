"""Layer 2 configuration (PRD Section 9, "config-driven").

Threshold, top-k and model come from the environment. The LLM settings are
Station 3's — one provider configuration for the whole project — but Layer 2 adds
its own model override, because phrasing a plan and adjudicating a precondition
are different jobs and may want different models.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Mapping

from kg_read_harness.config import Config, load_config
from kg_read_harness.errors import ConfigError
from llm_disambiguator.config import LLMConfig, load_llm_config
from plangraph_writer.schema import Schema


@dataclass(frozen=True)
class PlannerConfig:
    arango: Config
    prefix: str
    #: Vector-match score at or above which a skill is accepted as the goal.
    threshold: float
    #: How many candidates to return when the goal is ambiguous.
    top_k: int
    #: If the top two scores are within this, treat the match as a near-tie.
    tie_margin: float
    #: Maximum traversal depth. Generous: a subgraph is skill -> primitive ->
    #: state, so 3 suffices, but precedes chains can run longer.
    max_depth: int
    llm: LLMConfig | None
    use_llm: bool

    @property
    def schema(self) -> Schema:
        return Schema(prefix=self.prefix)

    def describe(self) -> list[tuple[str, str]]:
        return [
            ("endpoint", self.arango.url),
            ("database", self.arango.database),
            ("named graph", self.schema.graph_name),
            ("match threshold", f"{self.threshold:.2f}"),
            ("tie margin", f"{self.tie_margin:.2f}"),
            ("top-k", str(self.top_k)),
            ("composer", self.llm.model if (self.use_llm and self.llm) else "templated (no LLM)"),
        ]


def _clean(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name)
    return (raw.strip() or None) if raw is not None else None


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _clean(env, name)
    if raw is None:
        return default
    if raw.lower() in ("1", "true", "yes", "on"):
        return True
    if raw.lower() in ("0", "false", "no", "off"):
        return False
    raise ConfigError(f"{name} must be a boolean, got {raw!r}.", f"unset {name} for the default.")


def _number(env: Mapping[str, str], name: str, default, cast, low, high):
    raw = _clean(env, name)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}.", f"unset it for {default}.") from None
    if not low <= value <= high:
        raise ConfigError(
            f"{name} must be between {low} and {high}, got {value}.", f"unset it for {default}."
        )
    return value


def load_planner_config(
    env: Mapping[str, str] | None = None, use_llm: bool = True
) -> PlannerConfig:
    env = os.environ if env is None else env
    arango = load_config(env)

    llm: LLMConfig | None = None
    if use_llm:
        try:
            llm = load_llm_config(env)
        except ConfigError:
            # No key configured: the plan is still correct, only plainly worded.
            # Section 9 requires exactly this degradation.
            llm = None
        else:
            model = _clean(env, "PLANNER_MODEL")
            if model:
                llm = replace(llm, model=model)

    return PlannerConfig(
        arango=arango,
        prefix=_clean(env, "PLANGRAPH_PREFIX") or arango.project_name,
        threshold=_number(env, "PLANNER_THRESHOLD", 0.35, float, 0.0, 1.0),
        top_k=int(_number(env, "PLANNER_TOP_K", 3, int, 1, 25)),
        tie_margin=_number(env, "PLANNER_TIE_MARGIN", 0.05, float, 0.0, 1.0),
        max_depth=int(_number(env, "PLANNER_MAX_DEPTH", 8, int, 1, 50)),
        llm=llm,
        use_llm=use_llm and llm is not None,
    )
