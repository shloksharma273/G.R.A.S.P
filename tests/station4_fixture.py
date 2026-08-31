"""Stamped-edge fixtures for Station 4.

Station 4's job is to normalize and chain *correct* labels, so its tests are fed a
complete edge set built from the chai answer key rather than piped through
Station 3's own coverage. Otherwise a gap in Station 3's pre-pass would silently
show up as a Station 4 failure, which tests the wrong thing.

`chai_stamped()` is Station 2's real output plus ground-truth requires/produces
edges; `edge()` builds one-off edges for the direction and cycle cases.
"""

from __future__ import annotations

from kg_read_harness.bundle import Bundle, Entity
from llm_disambiguator.model import (
    METHOD_LLM,
    ORIENTATION_IMPLIED_BY_LABEL,
    ResolvedEdge,
)
from rule_preclassifier import classify
from rule_preclassifier.model import (
    ORIENTATION_DEFERRED,
    ORIENTATION_IMPLIED,
    Orientation,
    StampedEdge,
)

from .chai_answer_key import expected_label
from .station2_fixture import chai_bundles


def resolved(bundle: Bundle, label: str, confidence: float = 0.95) -> ResolvedEdge:
    """A Station 3 record for an already-canonical PRIMITIVE -> STATE bundle."""
    return ResolvedEdge(
        bundle=bundle,
        edge_type=label,
        confidence=confidence,
        rationale="ground truth from the answer key",
        method=METHOD_LLM,
        orientation=Orientation(
            head=bundle.source.name,
            tail=bundle.target.name,
            decided_by=ORIENTATION_IMPLIED_BY_LABEL,
        ),
        model="fixture/answer-key",
    )


def chai_stamped(**overrides):
    """Station 2's stamped edges plus ground-truth Station 3 edges for chai.

    Returns `(all_edges, station2_result)`.
    """
    classified = classify(chai_bundles())
    edges = list(classified.stamped)
    for item in classified.deferred:
        bundle = item.bundle
        label = expected_label(bundle.source.name, bundle.target.name)
        assert label is not None, f"answer key misses {bundle.source.name}->{bundle.target.name}"
        edges.append(resolved(bundle, label))
    return edges, classified


def bundle(source_name, source_type, target_name, target_type, description="", key="r1"):
    return Bundle(
        relation_key=key,
        source=Entity(source_name, source_type),
        target=Entity(target_name, target_type),
        description=description,
    )


def stamped(
    source_name,
    source_type,
    target_name,
    target_type,
    edge_type,
    description="",
    key="r1",
    head=None,
    tail=None,
):
    """A Station 2 `StampedEdge`, with orientation implied unless told otherwise."""
    orientation = (
        Orientation(head=head, tail=tail, decided_by=ORIENTATION_IMPLIED)
        if head is not None
        else Orientation(head=None, tail=None, decided_by=ORIENTATION_DEFERRED)
    )
    return StampedEdge(
        bundle=bundle(source_name, source_type, target_name, target_type, description, key),
        edge_type=edge_type,
        orientation=orientation,
    )


def precedes(source, target, description="", key="r1"):
    """An explicit `precedes` edge with direction unresolved, as Station 2 leaves it."""
    return stamped(source, "PRIMITIVE", target, "PRIMITIVE", "precedes", description, key)


def produces(primitive, state, key="r1"):
    return resolved(bundle(primitive, "PRIMITIVE", state, "STATE", "", key), "produces")


def requires(primitive, state, key="r1"):
    return resolved(bundle(primitive, "PRIMITIVE", state, "STATE", "", key), "requires")


#: The order the chai recipe must come out in. Any topological sort has to
#: respect every one of these pairs *whose endpoints are both in the graph*.
#:
#: The last-but-one pair is the difference between the two corpora: the live
#: AutoGraph build says turn_off_stove requires tea_brewed, the offline fixture
#: (which mirrors the raw rulebook) does not. There, turn_off_stove produces a
#: state nothing consumes, so no ordering involves it at all — Section 9's
#: "produced but never required", left for the validation pass to flag.
CHAI_ORDER_CONSTRAINTS = (
    ("place_pan", "add_water"),
    ("turn_on_stove", "boil_water"),
    ("add_water", "boil_water"),
    ("boil_water", "add_tea_leaves"),
    ("add_tea_leaves", "add_milk"),
    ("add_tea_leaves", "add_sugar"),
    ("add_milk", "simmer"),
    ("add_sugar", "simmer"),
    ("simmer", "strain"),
    ("simmer", "turn_off_stove"),
    ("strain", "serve"),
)


def violated_constraints(order: list[str]) -> list[tuple[str, str]]:
    """Constraints the given sequence breaks, ignoring absent primitives."""
    position = {name: index for index, name in enumerate(order)}
    return [
        (earlier, later)
        for earlier, later in CHAI_ORDER_CONSTRAINTS
        if earlier in position and later in position and position[earlier] > position[later]
    ]
