"""The decision table and the reason-code taxonomy — Station 2's entire policy.

PRD Sections 6 and 9. Non-Functional Requirement: "the decision table and
reason-priority live in one readable, editable place — not scattered through
conditionals — so adding an ontology type is adding a row." Everything a future
ontology change touches is in this module; `classifier.py` only reads it.
"""

from __future__ import annotations

from dataclasses import dataclass

# --------------------------------------------------------------------------
# Ontology
# --------------------------------------------------------------------------

#: The planning ontology (Section 12). Anything outside it parks as missing_type.
ONTOLOGY = ("SKILL", "PRIMITIVE", "OBJECT", "STATE")

#: Vocabulary a build emits that means an ontology type under another name.
#:
#: Type values are matched case-insensitively, so this map only carries genuine
#: *synonyms*, not casing variants. It is deliberately small: an unmapped value
#: parks as `missing_type` (group B), which is visible in the summary and is
#: exactly the feedback loop Section 9 describes. Under-mapping is therefore
#: safe; over-mapping silently invents meaning. Add a row only with evidence.
#:
#: TOOL: the roboticsPlanner/kitchen build labels primitive actions `tool`
#: (its own descriptions call them "the primitive action Place Pan").
TYPE_SYNONYMS = {
    "TOOL": "PRIMITIVE",
    "ACTION": "PRIMITIVE",
}

# --------------------------------------------------------------------------
# Edge types (Section 6)
# --------------------------------------------------------------------------

DECOMPOSES_TO = "decomposes_to"
PRECEDES = "precedes"
USES = "uses"
REQUIRES = "requires"
PRODUCES = "produces"

#: Outcomes a table row can order.
STAMP = "stamp"
DEFER = "defer"


@dataclass(frozen=True)
class Rule:
    """One row of the decision table.

    `head_type`/`tail_type` name the ontology types of the canonical head and
    tail when the orientation is implied by the pair itself (Section 6's note).
    Both are None when direction is left to Stations 3/4.
    """

    edge_type: str | tuple[str, ...]
    outcome: str
    head_type: str | None = None
    tail_type: str | None = None

    @property
    def orientation_is_implied(self) -> bool:
        return self.head_type is not None and self.tail_type is not None


#: The decision table (Section 6), keyed on the *unordered* entity-type pair —
#: stored as a sorted tuple so a reversed edge matches the same row.
DECISION_TABLE: dict[tuple[str, str], Rule] = {
    ("PRIMITIVE", "SKILL"): Rule(DECOMPOSES_TO, STAMP, head_type="SKILL", tail_type="PRIMITIVE"),
    ("PRIMITIVE", "PRIMITIVE"): Rule(PRECEDES, STAMP),  # direction deferred
    ("OBJECT", "PRIMITIVE"): Rule(USES, STAMP, head_type="PRIMITIVE", tail_type="OBJECT"),
    ("PRIMITIVE", "STATE"): Rule((REQUIRES, PRODUCES), DEFER),
}


def pair_key(source_type: str, target_type: str) -> tuple[str, str]:
    """The order-independent key the table is indexed by (FR-2)."""
    return tuple(sorted((source_type, target_type)))  # type: ignore[return-value]


def lookup(source_type: str, target_type: str) -> Rule | None:
    return DECISION_TABLE.get(pair_key(source_type, target_type))


# --------------------------------------------------------------------------
# Parked-bundle taxonomy (Section 9)
# --------------------------------------------------------------------------

SUSPECT_TYPE = "suspect_type"
MISSING_TYPE = "missing_type"
AMBIGUOUS_DIRECTION = "ambiguous_direction"
SELF_LOOP = "self_loop"
CONFLICTING_EDGE = "conflicting_edge"
EMPTY_DESCRIPTION = "empty_description"
DANGLING_ENDPOINT = "dangling_endpoint"
ALIAS_MISMATCH = "alias_mismatch"
UNMAPPED_PAIR = "unmapped_pair"

#: Contributed by Station 3 (LLM Disambiguator). The taxonomy is shared across
#: the bridge because one quality dashboard reads every station's parked pile;
#: Station 3 owns when these fire, this module owns what they mean.
LOW_CONFIDENCE = "low_confidence"
UNCLEAR = "unclear"
SERVICE_ERROR = "service_error"

#: Which diagnostic group each code belongs to, and what a heavy pile means.
REASON_GROUPS = {
    SUSPECT_TYPE: "B",
    MISSING_TYPE: "B",
    AMBIGUOUS_DIRECTION: "C",
    SELF_LOOP: "C",
    CONFLICTING_EDGE: "C",
    EMPTY_DESCRIPTION: "D",
    DANGLING_ENDPOINT: "D",
    ALIAS_MISMATCH: "D",
    UNMAPPED_PAIR: "A",
    LOW_CONFIDENCE: "C",
    UNCLEAR: "C",
    SERVICE_ERROR: "D",
}

GROUP_MEANING = {
    "A": "normal — a valid pair with no planning meaning; ignore",
    "B": "fix Layer 1 — the ontology or the extraction prompt",
    "C": "the rulebook's ordering language is too vague",
    "D": "clean the source data",
}

#: Priority order B -> C -> D -> A (Section 9): when several codes apply, the
#: most actionable wins, so a mis-typed *and* directionless edge reports as the
#: fixable suspect_type rather than the blander ambiguous_direction.
REASON_PRIORITY = (
    # B — fix Layer 1
    SUSPECT_TYPE,
    MISSING_TYPE,
    # C — vague rulebook wording
    AMBIGUOUS_DIRECTION,
    SELF_LOOP,
    CONFLICTING_EDGE,
    LOW_CONFIDENCE,
    UNCLEAR,
    # D — clean the source
    EMPTY_DESCRIPTION,
    DANGLING_ENDPOINT,
    ALIAS_MISMATCH,
    SERVICE_ERROR,
    # A — normal
    UNMAPPED_PAIR,
)

#: Codes that make a bundle untrustworthy even when the table has a row for its
#: pair — they preempt the stamp/defer decision.
BLOCKING_REASONS = frozenset(
    {
        MISSING_TYPE,
        DANGLING_ENDPOINT,
        SELF_LOOP,
        CONFLICTING_EDGE,
        EMPTY_DESCRIPTION,
        AMBIGUOUS_DIRECTION,
    }
)

#: Codes that only *explain* a pair the table could not route; they never
#: override a successful classification.
EXPLANATORY_REASONS = frozenset({SUSPECT_TYPE, ALIAS_MISMATCH, UNMAPPED_PAIR})

#: Codes no Station 2 rule can produce — Station 3 decides when they fire.
STATION_3_REASONS = frozenset({LOW_CONFIDENCE, UNCLEAR, SERVICE_ERROR})

assert set(REASON_PRIORITY) == set(REASON_GROUPS), "every reason code needs a group"
assert (
    BLOCKING_REASONS | EXPLANATORY_REASONS | STATION_3_REASONS == set(REASON_PRIORITY)
), "every reason code must be owned by exactly one station's routing rules"


def reason_rank(code: str) -> int:
    return REASON_PRIORITY.index(code)


def group_of(code: str) -> str:
    return REASON_GROUPS[code]


# --------------------------------------------------------------------------
# Heuristic vocabulary for suspect_type (Section 9, group B)
# --------------------------------------------------------------------------

#: First tokens that make a name read as a robot action. Used only to flag an
#: endpoint that looks like a PRIMITIVE but carries some other type, and only
#: for pairs the table already failed to route.
ACTION_VERBS = frozenset(
    """
    add align approach attach boil carry clean close cook cut detach drop empty
    fill grasp heat hold insert lift lower make mix move navigate open pick place
    play pour press pull push put release remove rotate serve set simmer start
    stir stop strain take turn wait wash
    """.split()
)

#: Articles stripped when comparing names for alias_mismatch.
NAME_ARTICLES = ("the", "a", "an")
