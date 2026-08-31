"""Generator configuration (PRD Section 9, "config-driven").

Model, strictness and auto-ingest come from the environment. The LLM settings are
the project's existing ones (Station 3's), with a generator-specific model
override: reconstructing a rulebook from a rambling transcript is a much harder
job than adjudicating one precondition, and may want a different model.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from typing import Mapping

from kg_read_harness.errors import ConfigError
from llm_disambiguator.config import LLMConfig, load_llm_config

from .schema import ACCEPT, FLAG

#: Which verdicts may be written out and (if enabled) ingested.
STRICTNESS = {
    "strict": (ACCEPT,),
    "normal": (ACCEPT,),
    "lenient": (ACCEPT, FLAG),
}

DEFAULT_OUTPUT_DIR = "generated"


@dataclass(frozen=True)
class GeneratorConfig:
    llm: LLMConfig
    strictness: str
    output_dir: str
    cache_path: str
    cache_enabled: bool
    auto_ingest: bool
    check_grounding: bool

    @property
    def accepts(self) -> tuple[str, ...]:
        return STRICTNESS[self.strictness]

    def may_ingest(self, verdict: str) -> bool:
        """FR-7: a non-accept verdict is never auto-ingested."""
        return self.auto_ingest and verdict == ACCEPT

    def describe(self) -> list[tuple[str, str]]:
        return [
            ("model", self.llm.model),
            ("endpoint", self.llm.base_url),
            ("temperature", str(self.llm.temperature)),
            ("strictness", f"{self.strictness} (writes: {', '.join(self.accepts)})"),
            ("output dir", self.output_dir),
            ("cache", self.cache_path if self.cache_enabled else "(disabled)"),
            ("auto-ingest", "on" if self.auto_ingest else "off"),
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


def load_generator_config(env: Mapping[str, str] | None = None) -> GeneratorConfig:
    env = os.environ if env is None else env
    llm = load_llm_config(env)

    model = _clean(env, "RULEBOOK_MODEL")
    if model:
        llm = replace(llm, model=model)
    # A rulebook is a much longer reply than a batch of verdicts.
    llm = replace(llm, max_tokens=max(llm.max_tokens, 8000))

    strictness = (_clean(env, "RULEBOOK_STRICTNESS") or "normal").lower()
    if strictness not in STRICTNESS:
        raise ConfigError(
            f"RULEBOOK_STRICTNESS must be one of {', '.join(STRICTNESS)}, got {strictness!r}.",
            "'lenient' also writes flagged rulebooks; nothing ever auto-ingests them.",
        )

    return GeneratorConfig(
        llm=llm,
        strictness=strictness,
        output_dir=_clean(env, "RULEBOOK_OUTPUT_DIR") or DEFAULT_OUTPUT_DIR,
        cache_path=_clean(env, "RULEBOOK_CACHE_PATH") or ".grasp_cache/rulebooks.json",
        cache_enabled=_flag(env, "RULEBOOK_CACHE", True),
        auto_ingest=_flag(env, "RULEBOOK_AUTO_INGEST", False),
        check_grounding=_flag(env, "RULEBOOK_CHECK_GROUNDING", True),
    )
