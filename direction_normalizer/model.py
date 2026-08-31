"""Station 4's records: the input adapter and the three output streams.

PRD Section 6. Stations 2 and 3 emit different record shapes — a rule-stamped
edge carries a categorical confidence, an LLM-resolved one a numeric score — so
`InputEdge` is the single shape Station 4 works in, and `adapt()` is the only
place that knows about either upstream type.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Iterable

from kg_read_harness.bundle import Bundle
from rule_preclassifier.model import ConservationError, ParkedBundle
from rule_preclassifier.table import CREATES_CYCLE

STATION = "station4:direction_normalizer"

#: Station 4 parks under `ambiguous_direction`, `conflicting_edge` (both shared
#: with Station 2) and `creates_cycle` (contributed here). All three live in the
#: shared taxonomy, because one quality dashboard reads every station's pile.
__all_reason_codes__ = CREATES_CYCLE


@dataclass(frozen=True)
class InputEdge:
    """One stamped edge from Station 2 or 3, in Station 4's own shape."""

    bundle: Bundle
    edge_type: str
    #: The upstream orientation hint: head/tail when the type implied them, or
    #: None when direction was deferred to this station.
    head: str | None
    tail: str | None
    #: How the upstream station decided the *type* (rule / lexical / llm).
    upstream_method: str
    upstream_station: str
    confidence: float | None = None

    @property
    def relation_key(self) -> str:
        return self.bundle.relation_key

    @property
    def written(self) -> tuple[str, str]:
        """The arrow as AutoGraph wrote it, which is the weak tiebreaker."""
        return (self.bundle.source.name, self.bundle.target.name)


@dataclass(frozen=True)
class FinalizedEdge:
    """An input edge with its canonical direction fixed. Goes to Station 5."""

    bundle: Bundle
    edge_type: str
    head: str
    tail: str
    direction_method: str
    upstream_method: str
    upstream_station: str
    flipped: bool = False
    confidence: float | None = None
    #: Set when state chaining independently agreed with this explicit edge.
    confirmed_by_chaining: bool = False

    @property
    def relation_key(self) -> str:
        return self.bundle.relation_key

    @property
    def arrow(self) -> tuple[str, str]:
        return (self.head, self.tail)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream": "finalized",
            "edge_type": self.edge_type,
            "from": self.head,
            "to": self.tail,
            "direction_method": self.direction_method,
            "flipped": self.flipped,
            "confidence": self.confidence,
            "confirmed_by_chaining": self.confirmed_by_chaining,
            "upstream_method": self.upstream_method,
            "upstream_station": self.upstream_station,
            "station": STATION,
            **self.bundle.to_dict(),
        }


@dataclass(frozen=True)
class DerivedEdge:
    """A `precedes` edge state chaining produced. Goes to Station 5 as an addition.

    Section 8 asks for traceability: every derived edge names the state it came
    through and the two relation keys that justified it, so a surprising ordering
    can be walked back to the two sentences that caused it.
    """

    head: str
    tail: str
    via_state: str
    producing_relation_key: str
    requiring_relation_key: str
    confidence: float
    direction_method: str
    #: True when an explicit precedes edge independently said the same thing.
    confirms_explicit: bool = False

    edge_type: str = "precedes"

    @property
    def arrow(self) -> tuple[str, str]:
        return (self.head, self.tail)

    @property
    def trace(self) -> str:
        return (
            f"{self.head} produces {self.via_state}, which {self.tail} requires "
            f"({self.producing_relation_key} + {self.requiring_relation_key})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "stream": "derived",
            "edge_type": self.edge_type,
            "from": self.head,
            "to": self.tail,
            "direction_method": self.direction_method,
            "confidence": self.confidence,
            "confirms_explicit": self.confirms_explicit,
            "via_state": self.via_state,
            "source_relation_keys": [self.producing_relation_key, self.requiring_relation_key],
            "trace": self.trace,
            "station": STATION,
        }


@dataclass
class NormalizationResult:
    """Every input edge finalized or parked, plus the derived additions."""

    total_input: int = 0
    finalized: list[FinalizedEdge] = field(default_factory=list)
    derived: list[DerivedEdge] = field(default_factory=list)
    parked: list[ParkedBundle] = field(default_factory=list)
    #: (primitive, state) pairs where one action both produces and requires a
    #: state — skipped as a data artifact rather than chained (Section 9).
    self_chains: list[tuple[str, str]] = field(default_factory=list)
    #: Derived edges the cycle guard refused. They are additions, not inputs, so
    #: they cannot be "parked" against a bundle — but they must still be visible.
    refused_derived: list[DerivedEdge] = field(default_factory=list)

    @property
    def total_output(self) -> int:
        """Derived edges are additions, so they are counted separately (Section 6)."""
        return len(self.finalized) + len(self.parked)

    def assert_conservation(self) -> None:
        if self.total_input != self.total_output:
            raise ConservationError(
                f"conservation violated: {self.total_input} stamped edge(s) in, "
                f"{self.total_output} out (finalized={len(self.finalized)}, "
                f"parked={len(self.parked)}). Derived additions: {len(self.derived)}."
            )

    def counts_by_stream(self) -> dict[str, int]:
        return {
            "finalized": len(self.finalized),
            "derived": len(self.derived),
            "parked": len(self.parked),
        }

    def counts_by_edge_type(self) -> Counter[str]:
        counts: Counter[str] = Counter(edge.edge_type for edge in self.finalized)
        for edge in self.derived:
            counts[f"{edge.edge_type} (derived)"] += 1
        return counts

    def counts_by_direction_method(self) -> Counter[str]:
        counts: Counter[str] = Counter(edge.direction_method for edge in self.finalized)
        for edge in self.derived:
            counts[edge.direction_method] += 1
        return counts

    def counts_by_reason_code(self) -> Counter[str]:
        return Counter(item.reason_code for item in self.parked)

    def flipped_count(self) -> int:
        return sum(1 for edge in self.finalized if edge.flipped)

    def overlap(self) -> dict[str, int]:
        """Derived-vs-explicit agreement (FR-8).

        Three numbers that say how much of the ordering the rulebook actually
        stated: how many derived edges an explicit edge confirmed, how many were
        new, and how many explicit edges chaining had nothing to say about.
        """
        confirmed = sum(1 for edge in self.derived if edge.confirms_explicit)
        explicit_precedes = [
            edge for edge in self.finalized if edge.edge_type == "precedes"
        ]
        return {
            "derived_total": len(self.derived),
            "derived_confirming_explicit": confirmed,
            "derived_new": len(self.derived) - confirmed,
            "explicit_precedes": len(explicit_precedes),
            "explicit_unsupported": sum(
                1 for edge in explicit_precedes if not edge.confirmed_by_chaining
            ),
        }

    def precedes_edges(self) -> list[tuple[str, str]]:
        """Every ordering arrow Station 5 will write, derived and explicit."""
        arrows = [edge.arrow for edge in self.derived]
        arrows += [e.arrow for e in self.finalized if e.edge_type == "precedes"]
        return sorted(set(arrows))

    def all_records(self) -> Iterable[Any]:
        yield from self.finalized
        yield from self.parked

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "total_input": self.total_input,
                "by_stream": self.counts_by_stream(),
                "by_edge_type": dict(self.counts_by_edge_type()),
                "by_direction_method": dict(self.counts_by_direction_method()),
                "by_reason_code": dict(self.counts_by_reason_code()),
                "flipped": self.flipped_count(),
                "overlap": self.overlap(),
                "self_chains_skipped": len(self.self_chains),
                "derived_refused_as_cyclic": len(self.refused_derived),
            },
            "finalized": [edge.to_dict() for edge in self.finalized],
            "derived": [edge.to_dict() for edge in self.derived],
            "parked": [item.to_dict() for item in self.parked],
        }
