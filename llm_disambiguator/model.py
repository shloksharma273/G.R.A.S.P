"""Station 3's records: the verdict, the resolved edge, and the run result.

PRD Section 8. Station 3 emits two buckets — stamped `requires`/`produces` edges
for Station 4, and parked bundles for quality review. It reuses Station 2's
`ParkedBundle` and `Orientation` because the piles are shared downstream; only
what is genuinely new to this station is defined here.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from kg_read_harness.bundle import Bundle
from rule_preclassifier.model import ConservationError, Orientation, ParkedBundle
from rule_preclassifier.table import PRODUCES, REQUIRES

STATION = "station3:llm_disambiguator"

#: The closed vocabulary the model may return (Section 5, Section 7).
LABEL_REQUIRES = REQUIRES
LABEL_PRODUCES = PRODUCES
LABEL_UNCLEAR = "unclear"
LABELS = (LABEL_REQUIRES, LABEL_PRODUCES, LABEL_UNCLEAR)

#: How a verdict was reached (Section 8).
METHOD_LEXICAL = "lexical"
METHOD_LLM = "llm"

#: For requires and produces alike the canonical arrow runs primitive -> state;
#: the label fixes the meaning, so Station 4's work here is confirmatory.
ORIENTATION_IMPLIED_BY_LABEL = "implied_by_label"


@dataclass(frozen=True)
class Verdict:
    """One closed-form judgement about a (primitive, state) pair."""

    label: str
    confidence: float
    rationale: str
    method: str
    model: str | None = None

    def __post_init__(self) -> None:
        if self.label not in LABELS:
            raise ValueError(f"label must be one of {LABELS}, got {self.label!r}")

    @property
    def is_resolved(self) -> bool:
        return self.label in (LABEL_REQUIRES, LABEL_PRODUCES)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "method": self.method,
            "model": self.model,
        }


@dataclass(frozen=True)
class ResolvedEdge:
    """A disambiguated edge, stamped for Station 4 (Section 8)."""

    bundle: Bundle
    edge_type: str
    confidence: float
    rationale: str
    method: str
    orientation: Orientation
    model: str | None = None
    run_timestamp: str | None = None
    from_cache: bool = False

    @property
    def relation_key(self) -> str:
        return self.bundle.relation_key

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": "stamped",
            "edge_type": self.edge_type,
            "method": self.method,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "model": self.model,
            "run_timestamp": self.run_timestamp,
            "from_cache": self.from_cache,
            "orientation": self.orientation.to_dict(),
            "station": STATION,
            **self.bundle.to_dict(),
        }


@dataclass
class CallStats:
    """What the run actually cost — the check on FR-5's idempotency claim."""

    requests: int = 0
    retries: int = 0
    items_sent: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    reprompts: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "retries": self.retries,
            "items_sent": self.items_sent,
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "reprompts": self.reprompts,
        }


@dataclass
class DisambiguationResult:
    """Every deferred input, in exactly one of two buckets (FR-8)."""

    total_input: int = 0
    stamped: list[ResolvedEdge] = field(default_factory=list)
    parked: list[ParkedBundle] = field(default_factory=list)
    calls: CallStats = field(default_factory=CallStats)
    model: str | None = None

    @property
    def total_output(self) -> int:
        return len(self.stamped) + len(self.parked)

    def assert_conservation(self) -> None:
        if self.total_input != self.total_output:
            raise ConservationError(
                f"conservation violated: {self.total_input} deferred bundle(s) in, "
                f"{self.total_output} out (stamped={len(self.stamped)}, "
                f"parked={len(self.parked)})."
            )

    def counts_by_bucket(self) -> dict[str, int]:
        return {"stamped": len(self.stamped), "parked": len(self.parked)}

    def counts_by_label(self) -> Counter[str]:
        return Counter(edge.edge_type for edge in self.stamped)

    def counts_by_method(self) -> Counter[str]:
        return Counter(edge.method for edge in self.stamped)

    def counts_by_reason_code(self) -> Counter[str]:
        return Counter(item.reason_code for item in self.parked)

    def mean_confidence(self) -> float | None:
        """Mean confidence over stamped edges (FR-8); None when nothing stamped."""
        if not self.stamped:
            return None
        return sum(edge.confidence for edge in self.stamped) / len(self.stamped)

    def all_records(self) -> Iterable[Any]:
        yield from self.stamped
        yield from self.parked

    def to_dict(self) -> dict[str, Any]:
        mean = self.mean_confidence()
        return {
            "summary": {
                "total_input": self.total_input,
                "model": self.model,
                "by_bucket": self.counts_by_bucket(),
                "by_label": dict(self.counts_by_label()),
                "by_method": dict(self.counts_by_method()),
                "by_reason_code": dict(self.counts_by_reason_code()),
                "mean_confidence": round(mean, 4) if mean is not None else None,
                "calls": self.calls.to_dict(),
            },
            "stamped": [edge.to_dict() for edge in self.stamped],
            "parked": [item.to_dict() for item in self.parked],
        }
