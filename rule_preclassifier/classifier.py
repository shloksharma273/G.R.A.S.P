"""Station 2's pure routing function (PRD Sections 6, 10).

`classify()` takes Station 1 bundles and returns every one of them in exactly one
of three buckets. It is a pure function of its input: no LLM, no database, no
network, no filesystem write, no randomness, no clock (FR-8). Identical input
always yields identical output, including ordering.

The policy itself is not here — it is in `table.py`. This module only reads that
table, so adding an ontology type is adding a row, never editing a conditional.
"""

from __future__ import annotations

from typing import Iterable

from kg_read_harness.bundle import Bundle

from .detect import CorpusIndex, canonical_type, detect_reasons, type_rewrite
from .model import (
    ORIENTATION_DEFERRED,
    ORIENTATION_IMPLIED,
    ClassificationResult,
    DeferredBundle,
    Orientation,
    ParkedBundle,
    StampedEdge,
)
from .table import DEFER, Rule, lookup, reason_rank


def classify(bundles: Iterable[Bundle]) -> ClassificationResult:
    """Route every bundle to exactly one bucket (FR-1, FR-3, FR-6).

    Args:
        bundles: Station 1 bundles as an in-memory iterable. They are read, never
            re-fetched and never mutated.

    Returns:
        A `ClassificationResult` whose conservation invariant has been checked.
    """
    items = list(bundles)
    index = CorpusIndex(items)
    result = ClassificationResult(total_input=len(items))

    for bundle in items:
        _route(bundle, index, result)

    result.assert_conservation()
    return result


def _route(bundle: Bundle, index: CorpusIndex, result: ClassificationResult) -> None:
    source_type = canonical_type(bundle.source.type)
    target_type = canonical_type(bundle.target.type)

    for raw in (bundle.source.type, bundle.target.type):
        rewrite = type_rewrite(raw)
        if rewrite:
            result.normalized_types[rewrite] += 1

    rule = (
        lookup(source_type, target_type)
        if source_type is not None and target_type is not None
        else None
    )

    reasons = detect_reasons(bundle, source_type, target_type, rule, index)
    if reasons:
        # Exactly one reason code survives: the most actionable (Section 9).
        code, detail = min(reasons, key=lambda item: reason_rank(item[0]))
        result.parked.append(ParkedBundle(bundle=bundle, reason_code=code, detail=detail))
        return

    assert rule is not None  # no reasons means the table routed the pair
    if rule.outcome == DEFER:
        result.deferred.append(
            DeferredBundle(
                bundle=bundle,
                candidate_edge_types=tuple(rule.edge_type),
                detail=(
                    "PRIMITIVE-STATE is either a precondition or an effect; only the "
                    "description distinguishes them (Station 3)"
                ),
            )
        )
        return

    result.stamped.append(
        StampedEdge(
            bundle=bundle,
            edge_type=str(rule.edge_type),
            orientation=_orientation(bundle, rule, source_type),
        )
    )


def _orientation(bundle: Bundle, rule: Rule, source_type: str | None) -> Orientation:
    """Record the direction hint without deciding it (Section 7).

    Where the type pair implies head->tail — a SKILL decomposes to a PRIMITIVE,
    a PRIMITIVE uses an OBJECT — the canonical ends are named and it is noted
    whether AutoGraph wrote the edge the other way round, which is the fact
    Station 4 would otherwise lose when the pair is canonicalized. Where the pair
    implies nothing (`precedes`), the arrow is left entirely to Station 4.
    """
    if not rule.orientation_is_implied:
        return Orientation(head=None, tail=None, decided_by=ORIENTATION_DEFERRED)

    source_is_head = source_type == rule.head_type
    head = bundle.source if source_is_head else bundle.target
    tail = bundle.target if source_is_head else bundle.source
    return Orientation(
        head=head.name,
        tail=tail.name,
        decided_by=ORIENTATION_IMPLIED,
        reversed_from_input=not source_is_head,
    )
