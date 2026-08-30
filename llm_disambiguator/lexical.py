"""The lexical pre-pass (FR-3, PRD Section 6, stage 1).

"If the description contains cues from exactly one class with no conflicting
cues, short-circuit to that label with method = lexical. Conflicting or absent
cues fall through."

Being deliberately conservative here is the point: the pre-pass exists to keep
cost down on trivially-worded edges, not to compete with the model. Anything
carrying signals from both classes is exactly the case the LLM is for.
"""

from __future__ import annotations

from .cues import matches
from .model import LABEL_PRODUCES, LABEL_REQUIRES, METHOD_LEXICAL, Verdict

#: Confidence attributed to a single-class cue match. High, but deliberately
#: below 1.0: a cue is strong evidence, not proof.
LEXICAL_CONFIDENCE = 0.9


def classify_lexically(description: str) -> Verdict | None:
    """A verdict when exactly one cue class fires, otherwise None (fall through)."""
    requires_cues, produces_cues = matches(description)

    if requires_cues and not produces_cues:
        return Verdict(
            label=LABEL_REQUIRES,
            confidence=LEXICAL_CONFIDENCE,
            rationale="precondition cue(s): " + ", ".join(requires_cues),
            method=METHOD_LEXICAL,
        )
    if produces_cues and not requires_cues:
        return Verdict(
            label=LABEL_PRODUCES,
            confidence=LEXICAL_CONFIDENCE,
            rationale="effect cue(s): " + ", ".join(produces_cues),
            method=METHOD_LEXICAL,
        )
    # Both classes present, or neither: not the pre-pass's decision to make.
    return None
