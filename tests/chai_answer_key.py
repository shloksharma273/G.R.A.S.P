"""The chai answer key for Station 3 (PRD Section 13, criterion 1).

Every PRIMITIVE-STATE pair in the masala-chai rulebook, with the label it must
receive. Taken from the rulebook's own wording, not from any model's output:
"After this action, X" is an effect, "This action requires X" is a precondition.

Keys are normalized names, so the same key scores both the offline fixture
(`place_pan` / `pan_on_stove`) and the live AutoGraph build (`PLACE PAN` /
`PAN ON STOVE`).
"""

from __future__ import annotations

from rule_preclassifier.detect import normalize_name

#: (primitive, state) -> the correct label.
_RAW = {
    # effects: the state becomes true after the action runs
    ("place_pan", "pan_on_stove"): "produces",
    ("turn_on_stove", "stove_on"): "produces",
    ("add_water", "water_in_pan"): "produces",
    ("boil_water", "water_boiling"): "produces",
    ("add_tea_leaves", "tea_brewing"): "produces",
    ("add_milk", "milk_added"): "produces",
    ("add_sugar", "sugar_added"): "produces",
    ("simmer", "tea_brewed"): "produces",
    ("strain", "tea_in_cup"): "produces",
    ("turn_off_stove", "stove_off"): "produces",
    # preconditions: the state must already hold before the action can run
    ("add_water", "pan_on_stove"): "requires",
    ("boil_water", "water_in_pan"): "requires",
    ("boil_water", "stove_on"): "requires",
    ("add_tea_leaves", "water_boiling"): "requires",
    ("add_milk", "tea_brewing"): "requires",
    ("add_sugar", "tea_brewing"): "requires",
    ("simmer", "milk_added"): "requires",
    ("simmer", "sugar_added"): "requires",
    ("strain", "tea_brewed"): "requires",
    ("turn_off_stove", "tea_brewed"): "requires",
    ("serve", "tea_in_cup"): "requires",
}

ANSWER_KEY = {
    (normalize_name(primitive), normalize_name(state)): label
    for (primitive, state), label in _RAW.items()
}

#: Section 13 requires a stated target. Two are stated, and they differ in kind:
#:
#: PRECISION is the one that matters for plan safety — a wrongly stamped
#: precondition corrupts the ordering, whereas an abstention only costs a review.
#: Nothing may be stamped incorrectly.
PRECISION_TARGET = 1.00
#: COVERAGE is how much of the set gets resolved at all. Below this, the station
#: is technically correct but not pulling its weight.
COVERAGE_TARGET = 0.90


def expected_label(primitive: str, state: str) -> str | None:
    return ANSWER_KEY.get((normalize_name(primitive), normalize_name(state)))


def score(result) -> dict:
    """Grade a DisambiguationResult against the key.

    Returns correct / wrong / unscored stamped counts, the parked count, and the
    two rates the targets are stated in.
    """
    correct = wrong = unscored = 0
    mistakes = []

    for edge in result.stamped:
        head = edge.orientation.head or edge.bundle.source.name
        tail = edge.orientation.tail or edge.bundle.target.name
        expected = expected_label(head, tail)
        if expected is None:
            unscored += 1
        elif expected == edge.edge_type:
            correct += 1
        else:
            wrong += 1
            mistakes.append((head, tail, edge.edge_type, expected, edge.method, edge.rationale))

    scored = correct + wrong
    return {
        "total": result.total_input,
        "stamped": len(result.stamped),
        "parked": len(result.parked),
        "correct": correct,
        "wrong": wrong,
        "unscored": unscored,
        "precision": (correct / scored) if scored else 1.0,
        "coverage": (len(result.stamped) / result.total_input) if result.total_input else 0.0,
        "mistakes": mistakes,
    }
