"""LLM Disambiguator — Bridge Station 3 ("requires vs. produces").

Receives the one ambiguous bucket Station 2 could not settle — PRIMITIVE-STATE
relationships — and decides, for each, whether the primitive *requires* the state
(a precondition that must hold before it runs) or *produces* it (an effect that
becomes true after it runs).

It is the only station that calls an LLM, so its design is built around
reproducibility and honest abstention: temperature 0, a content-hash result
cache, a confidence gate, and parking rather than guessing when the text is
silent. `disambiguate(deferred, config)` is the entry point.
"""

from .cache import VerdictCache, content_key
from .config import LLMConfig, load_llm_config
from .disambiguator import disambiguate
from .lexical import classify_lexically
from .model import (
    LABELS,
    CallStats,
    DisambiguationResult,
    ResolvedEdge,
    Verdict,
)
from .provider import Provider, ServiceError
from .report import dump_json, print_report

__version__ = "1.0.0"

__all__ = [
    "disambiguate",
    "classify_lexically",
    "LLMConfig",
    "load_llm_config",
    "DisambiguationResult",
    "ResolvedEdge",
    "Verdict",
    "CallStats",
    "LABELS",
    "VerdictCache",
    "content_key",
    "Provider",
    "ServiceError",
    "print_report",
    "dump_json",
    "__version__",
]
