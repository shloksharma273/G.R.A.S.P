"""Station 4's policy — canonical directions, the trust hierarchy, and the cues.

PRD Section 8: "cue lists (reused from Station 3) and conflict-resolution policy
live in configuration." Everything a future edge type or trust change touches is
in this module; the pipeline only reads it.
"""

from __future__ import annotations

from llm_disambiguator.cues import compile_cues, find_cues
from rule_preclassifier.table import DECOMPOSES_TO, PRECEDES, PRODUCES, REQUIRES, USES

# --------------------------------------------------------------------------
# Job 1 — direction implied by the endpoint types (Section 5)
# --------------------------------------------------------------------------

#: edge type -> (head entity type, tail entity type). An edge AutoGraph wrote the
#: other way round is simply flipped; that is expected, not an error.
CANONICAL_DIRECTION = {
    DECOMPOSES_TO: ("SKILL", "PRIMITIVE"),
    USES: ("PRIMITIVE", "OBJECT"),
    REQUIRES: ("PRIMITIVE", "STATE"),
    PRODUCES: ("PRIMITIVE", "STATE"),
}

#: The one edge type whose direction the endpoint types cannot fix: both ends are
#: PRIMITIVEs, so the arrow has to come from the trust hierarchy below.
ORDERING_EDGE = PRECEDES

# --------------------------------------------------------------------------
# Job 2 — the trust hierarchy (Section 5)
# --------------------------------------------------------------------------

METHOD_TYPE_IMPLIED = "type_implied"
METHOD_DERIVED = "derived"
METHOD_DESCRIPTION = "description"
METHOD_ORIENTATION = "orientation"

#: Priority 1 is structural and beats everything; 3 is a weak tiebreaker only.
TRUST_ORDER = (METHOD_DERIVED, METHOD_DESCRIPTION, METHOD_ORIENTATION)

#: Confidence attached to a precedes edge, by how its direction was decided.
#: State chaining is a structural fact about the precondition graph, so it
#: outranks anything read out of prose; an edge kept only because AutoGraph
#: happened to write it that way is deliberately marked as barely trusted.
CONFIDENCE = {
    METHOD_DERIVED: 0.95,
    METHOD_DESCRIPTION: 0.70,
    METHOD_ORIENTATION: 0.40,
}

#: A derived edge that an explicit edge independently agrees with (Section 5,
#: "a derived edge that agrees with an explicit one confirms it").
CONFIRMED_CONFIDENCE = 0.99

# --------------------------------------------------------------------------
# Ordering cues (priority 2) — built with Station 3's cue machinery
# --------------------------------------------------------------------------

#: "A <cue> B" reads as: A runs first. The edge keeps its written orientation.
FORWARD_CUES = (
    "before",
    "precedes",
    "prior to",
    "first",
    "then",
    "followed by",
    "leads to",
    "earlier than",
    "ahead of",
)

#: "A <cue> B" reads as: B runs first. The edge is flipped.
BACKWARD_CUES = (
    "after",
    "once",
    "following",
    "subsequent to",
    "later than",
    "depends on",
    "requires",
    "needs",
    "must already",
)

FORWARD_PATTERN = compile_cues(FORWARD_CUES)
BACKWARD_PATTERN = compile_cues(BACKWARD_CUES)


def ordering_cues(description: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The forward-cues and backward-cues present in `description`."""
    return find_cues(FORWARD_PATTERN, description), find_cues(BACKWARD_PATTERN, description)


# --------------------------------------------------------------------------
# Conflict-resolution policy (Section 5, "Reconciliation")
# --------------------------------------------------------------------------

#: When state chaining and an explicit edge disagree, which one survives.
#: Structural evidence wins: the states two actions share are a fact about the
#: plan, whereas an explicit edge's direction is at best a reading of prose.
PREFER_DERIVED_ON_CONFLICT = True

#: Whether a primitive that both produces and requires the same state should
#: yield a self-precedes edge. It never should — it is a data artifact (Section 9).
ALLOW_SELF_CHAIN = False
