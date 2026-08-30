"""Lexical cue lists for the pre-pass (PRD Sections 5, 6) — configuration, not code.

Section 5 gives the reading of each label in temporal terms; these are the surface
forms that reading takes in a rulebook. The pre-pass fires only when cues from
exactly one class are present, so a description carrying both ("requires X ... after
which Y") falls through to the LLM rather than being decided by whichever cue
happened to be listed first.

Cues are matched on word boundaries against the lower-cased description.
"""

from __future__ import annotations

import re

#: "The state must already be TRUE before the primitive can run."
REQUIRES_CUES = (
    "requires",
    "required",
    "require",
    "precondition",
    "preconditions",
    "prerequisite",
    "must already",
    "must be",
    "must first",
    "must hold",
    "needs",
    "needed",
    "depends on",
    "only if",
    "only when",
    "provided that",
    "before it can",
    "before this",
    "before the",
    "before adding",
    "already",
)

#: "The state becomes TRUE after the primitive runs."
PRODUCES_CUES = (
    "effect",
    "effects",
    "results in",
    "resulting in",
    "produces",
    "produce",
    "yields",
    "causes",
    "becomes",
    "become",
    "makes",
    "leads to",
    "after this action",
    "after the action",
    "after performing",
    "after it runs",
    "once complete",
    "now true",
    "sets the",
    "places the",
    "turns the",
)


def _compile(cues: tuple[str, ...]) -> re.Pattern[str]:
    ordered = sorted(cues, key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(re.escape(cue) for cue in ordered) + r")\b")


REQUIRES_PATTERN = _compile(REQUIRES_CUES)
PRODUCES_PATTERN = _compile(PRODUCES_CUES)


def matches(description: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The requires-cues and produces-cues present in `description`."""
    text = (description or "").lower()
    return (
        tuple(dict.fromkeys(REQUIRES_PATTERN.findall(text))),
        tuple(dict.fromkeys(PRODUCES_PATTERN.findall(text))),
    )
