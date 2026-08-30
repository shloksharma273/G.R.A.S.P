"""The annotated buckets Station 2 emits — the contract Stations 3 and 4 read.

PRD Section 8. Station 2 *adds to* a Station 1 bundle and never mutates it: every
record here holds the original `Bundle` untouched and carries its own annotations
alongside. Provenance (`relation_key`) survives into all three buckets.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from kg_read_harness.bundle import Bundle

from .table import GROUP_MEANING, group_of

#: Recorded on every annotation so a downstream pile says who produced it.
STATION = "station2:rule_preclassifier"

METHOD_RULE = "rule"
CONFIDENCE_HIGH = "high"

BUCKET_STAMPED = "stamped"
BUCKET_DEFERRED = "deferred"
BUCKET_PARKED = "parked"

#: How a stamped edge's head->tail was decided.
ORIENTATION_IMPLIED = "implied_by_type_pair"
ORIENTATION_DEFERRED = "deferred_to_station_4"


@dataclass(frozen=True)
class Orientation:
    """The orientation *hint* — Station 2 records, Station 4 decides (Section 7).

    `head`/`tail` are entity names when the type pair implies the direction
    (a SKILL decomposes to a PRIMITIVE, never the reverse) and None when it does
    not. `reversed_from_input` says whether AutoGraph wrote the edge the other
    way round, which is the fact Station 4 needs and which would otherwise be
    lost once the pair is canonicalized.
    """

    head: str | None
    tail: str | None
    decided_by: str
    reversed_from_input: bool = False

    @property
    def is_resolved(self) -> bool:
        return self.head is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "head": self.head,
            "tail": self.tail,
            "decided_by": self.decided_by,
            "reversed_from_input": self.reversed_from_input,
        }


@dataclass(frozen=True)
class StampedEdge:
    """A bundle the table settled with certainty. Goes to Station 4."""

    bundle: Bundle
    edge_type: str
    orientation: Orientation
    method: str = METHOD_RULE
    confidence: str = CONFIDENCE_HIGH

    @property
    def relation_key(self) -> str:
        return self.bundle.relation_key

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": BUCKET_STAMPED,
            "edge_type": self.edge_type,
            "method": self.method,
            "confidence": self.confidence,
            "orientation": self.orientation.to_dict(),
            "station": STATION,
            **self.bundle.to_dict(),
        }


@dataclass(frozen=True)
class DeferredBundle:
    """The one genuinely ambiguous pair. Goes to Station 3, unresolved (FR-4)."""

    bundle: Bundle
    candidate_edge_types: tuple[str, ...]
    detail: str

    @property
    def relation_key(self) -> str:
        return self.bundle.relation_key

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": BUCKET_DEFERRED,
            "ambiguous": True,
            "candidate_edge_types": list(self.candidate_edge_types),
            "detail": self.detail,
            "station": STATION,
            **self.bundle.to_dict(),
        }


@dataclass(frozen=True)
class ParkedBundle:
    """An unclassified bundle with exactly one reason code (FR-5).

    Carries the full shared payload of Section 8 — both names, both types, the
    description and the relation key — so the parked pile is a diagnostic rather
    than a graveyard.
    """

    bundle: Bundle
    reason_code: str
    detail: str
    #: Which station parked it — the pile is shared, so it must say who wrote it.
    station: str = STATION

    @property
    def relation_key(self) -> str:
        return self.bundle.relation_key

    @property
    def group(self) -> str:
        return group_of(self.reason_code)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bucket": BUCKET_PARKED,
            "reason_code": self.reason_code,
            "group": self.group,
            "group_meaning": GROUP_MEANING[self.group],
            "detail": self.detail,
            "station": self.station,
            **self.bundle.to_dict(),
        }


class ConservationError(AssertionError):
    """The one invariant Station 2 must never break (FR-6)."""


@dataclass
class ClassificationResult:
    """Every input bundle, in exactly one of three buckets."""

    total_input: int = 0
    stamped: list[StampedEdge] = field(default_factory=list)
    deferred: list[DeferredBundle] = field(default_factory=list)
    parked: list[ParkedBundle] = field(default_factory=list)
    #: Type values rewritten by TYPE_SYNONYMS, e.g. {"tool -> PRIMITIVE": 24}.
    normalized_types: Counter[str] = field(default_factory=Counter)

    @property
    def total_output(self) -> int:
        return len(self.stamped) + len(self.deferred) + len(self.parked)

    def assert_conservation(self) -> None:
        """|input| == |stamped| + |deferred| + |parked| — checked, not assumed."""
        if self.total_input != self.total_output:
            raise ConservationError(
                f"conservation violated: {self.total_input} bundle(s) in, "
                f"{self.total_output} out "
                f"(stamped={len(self.stamped)}, deferred={len(self.deferred)}, "
                f"parked={len(self.parked)})."
            )

    def counts_by_bucket(self) -> dict[str, int]:
        return {
            BUCKET_STAMPED: len(self.stamped),
            BUCKET_DEFERRED: len(self.deferred),
            BUCKET_PARKED: len(self.parked),
        }

    def counts_by_edge_type(self) -> Counter[str]:
        counts: Counter[str] = Counter()
        for edge in self.stamped:
            counts[edge.edge_type] += 1
        for item in self.deferred:
            counts[" / ".join(item.candidate_edge_types) + " (deferred)"] += 1
        return counts

    def counts_by_reason_code(self) -> Counter[str]:
        return Counter(item.reason_code for item in self.parked)

    def counts_by_group(self) -> Counter[str]:
        return Counter(item.group for item in self.parked)

    def all_records(self) -> Iterable[Any]:
        """Every annotated record, stamped then deferred then parked."""
        yield from self.stamped
        yield from self.deferred
        yield from self.parked

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "total_input": self.total_input,
                "by_bucket": self.counts_by_bucket(),
                "by_edge_type": dict(self.counts_by_edge_type()),
                "by_reason_code": dict(self.counts_by_reason_code()),
                "by_group": dict(self.counts_by_group()),
                "normalized_types": dict(self.normalized_types),
            },
            BUCKET_STAMPED: [edge.to_dict() for edge in self.stamped],
            BUCKET_DEFERRED: [item.to_dict() for item in self.deferred],
            BUCKET_PARKED: [item.to_dict() for item in self.parked],
        }
