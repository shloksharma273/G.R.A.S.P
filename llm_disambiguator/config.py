"""Configuration for Station 3, loaded from environment variables.

PRD Section 10: "threshold, batch size, cue lists, and pre-pass on/off live in
configuration, not code." The cue lists are in `cues.py`; everything else is here.
As in Station 1, the API key comes only from the environment and is never printed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from kg_read_harness.errors import ConfigError

#: Never printed, never logged.
SECRET_VARS = ("LLM_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY")

#: An OpenAI-compatible endpoint (PRD Section 7). OpenRouter is the default
#: because it is what this project is configured against; any OpenAI-compatible
#: base URL works unchanged.
DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "anthropic/claude-opus-5"


@dataclass(frozen=True)
class LLMConfig:
    # provider
    api_key: str
    base_url: str
    model: str
    # determinism (Section 7)
    temperature: float
    reasoning: bool
    json_mode: bool
    max_tokens: int
    # pipeline shaping (Section 6)
    lexical_prepass: bool
    confidence_threshold: float
    batch_size: int
    # resilience and cost (FR-7, Section 10)
    max_requests: int
    max_retries: int
    backoff_seconds: float
    timeout_seconds: float
    # reproducibility (FR-5)
    cache_path: str
    cache_enabled: bool

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + "/chat/completions"

    def describe(self) -> list[tuple[str, str]]:
        """Effective settings, safe to print: no key ever appears here."""
        return [
            ("endpoint", self.base_url),
            ("model", self.model),
            ("api key", f"set ({len(self.api_key)} chars, not shown)"),
            ("temperature", str(self.temperature)),
            ("provider reasoning", "on" if self.reasoning else "off"),
            ("lexical pre-pass", "on" if self.lexical_prepass else "off"),
            ("confidence threshold", str(self.confidence_threshold)),
            ("batch size", str(self.batch_size)),
            ("request cap", str(self.max_requests) + (" (no calls)" if not self.max_requests else "")),
            ("cache", self.cache_path if self.cache_enabled else "(disabled)"),
        ]


def _clean(env: Mapping[str, str], name: str) -> str | None:
    raw = env.get(name)
    if raw is None:
        return None
    return raw.strip() or None


def _flag(env: Mapping[str, str], name: str, default: bool) -> bool:
    raw = _clean(env, name)
    if raw is None:
        return default
    if raw.lower() in ("1", "true", "yes", "on"):
        return True
    if raw.lower() in ("0", "false", "no", "off"):
        return False
    raise ConfigError(
        f"{name} must be a boolean (1/0, true/false, on/off), got {raw!r}.",
        f"unset {name} for the default ({'on' if default else 'off'}).",
    )


def _number(env: Mapping[str, str], name: str, default: float, cast, low, high) -> float:
    raw = _clean(env, name)
    if raw is None:
        return default
    try:
        value = cast(raw)
    except ValueError:
        raise ConfigError(
            f"{name} must be a number, got {raw!r}.", f"unset {name} for the default {default}."
        ) from None
    if not low <= value <= high:
        raise ConfigError(
            f"{name} must be between {low} and {high}, got {value}.",
            f"unset {name} for the default {default}.",
        )
    return value


def _resolve_key(env: Mapping[str, str]) -> str:
    """The first key variable that is set, so the same code serves any provider."""
    for name in SECRET_VARS:
        key = _clean(env, name)
        if key:
            return key
    raise ConfigError(
        "no LLM API key found in the environment (checked " + ", ".join(SECRET_VARS) + ").",
        "export one of them (see .env.example). The key is read only from the "
        "environment and is never printed or written to disk.",
    )


def load_llm_config(env: Mapping[str, str] | None = None) -> LLMConfig:
    env = os.environ if env is None else env
    return LLMConfig(
        api_key=_resolve_key(env),
        base_url=_clean(env, "LLM_BASE_URL") or DEFAULT_BASE_URL,
        model=_clean(env, "LLM_MODEL") or DEFAULT_MODEL,
        # Section 7: "Temperature 0 (or the lowest the provider allows)".
        temperature=_number(env, "LLM_TEMPERATURE", 0.0, float, 0.0, 2.0),
        # Provider-side reasoning is off by default: this is a closed-vocabulary
        # classification, and on thinking-by-default models the reasoning tokens
        # consume the whole budget before any JSON is emitted.
        reasoning=_flag(env, "LLM_REASONING", False),
        json_mode=_flag(env, "LLM_JSON_MODE", True),
        max_tokens=int(_number(env, "LLM_MAX_TOKENS", 4000, int, 256, 128_000)),
        lexical_prepass=_flag(env, "LLM_LEXICAL_PREPASS", True),
        confidence_threshold=_number(env, "LLM_CONFIDENCE_THRESHOLD", 0.75, float, 0.0, 1.0),
        batch_size=int(_number(env, "LLM_BATCH_SIZE", 10, int, 1, 100)),
        # A plain request budget: 0 makes no call at all (what --dry-run sets).
        max_requests=int(_number(env, "LLM_MAX_REQUESTS", 50, int, 0, 10_000)),
        max_retries=int(_number(env, "LLM_MAX_RETRIES", 3, int, 0, 10)),
        backoff_seconds=_number(env, "LLM_BACKOFF_SECONDS", 1.0, float, 0.0, 60.0),
        timeout_seconds=_number(env, "LLM_TIMEOUT_SECONDS", 90.0, float, 1.0, 600.0),
        cache_path=_clean(env, "LLM_CACHE_PATH") or ".grasp_cache/station3.json",
        cache_enabled=_flag(env, "LLM_CACHE", True),
    )
